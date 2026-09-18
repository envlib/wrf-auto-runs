#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 22 17:21:47 2025

@author: mike
"""
from collections import OrderedDict

import f90nml
import subprocess
import pendulum

import params
import utils
import defaults
from create_trmask import num_wvt_regions as count_wvt_regions
from create_trmask import normalize_boundary_faces, resolve_relax_width

# Compiled tracer-member ceiling. Must match MAX_WVT_REGIONS in the WRF build:
# module_check_a_mundo.F (the num_wvt_regions bound), MAXREG in module_diag_wvt_columns.F,
# and the three Registry generators (gen_wvt_{tracers,cuten,thum}.py).
MAX_WVT_REGIONS = 12


def _first(v):
    """Namelist values may be per-domain lists; these checks look at d01.

    Module level, not nested: set_nml_params calls it too. While it was nested inside
    validate_wvt_regions, every config with a non-empty boundary_faces died with NameError
    before a namelist was written -- and no test drove that path.
    """
    return v[0] if isinstance(v, list) else v


################################################
### Helper


def apply_overrides(target, overrides, domains, old_n_domains):
    """Merge TOML overrides into a WRF namelist section dict, slicing per-domain arrays."""
    n_domains = len(domains)
    for k, v in overrides.items():
        if isinstance(v, list) and len(v) == old_n_domains and old_n_domains != n_domains:
            target[k] = [v[d - 1] for d in domains]
        else:
            target[k] = v


def broadcast_field(value, n_domains, domains, old_n_domains):
    """
    Handle per-domain values from TOML:
    - scalar -> [scalar] * n_domains
    - array of old_n_domains -> slice to selected domains
    - array of n_domains -> pass through
    """
    if not isinstance(value, list):
        return [value] * n_domains
    if len(value) == old_n_domains and old_n_domains != n_domains:
        return [value[d - 1] for d in domains]
    if len(value) == n_domains:
        return list(value)
    raise ValueError(f'Array has {len(value)} values, expected {old_n_domains} (full domain count) or {n_domains} (run domain count)')


def validate_tracer_opt_uniform(dynamics):
    """tracer_opt must be one value on every domain (mirrors the check_a_mundo rule added
    2026-09-18): every WVT state field is Registry-packaged on tracer_opt==4 PER DOMAIN, while
    WRF's generated nest interpolation/feedback guards on the PARENT's array size only, so
    `tracer_opt = 4, 0` would interpolate a full patch into the nest's size-1 dummy. Raises
    ValueError; a scalar or a uniform list passes."""
    v = dynamics.get('tracer_opt', 0)
    if isinstance(v, list) and 4 in v and len(set(v)) > 1:
        raise ValueError(
            f'[dynamics] tracer_opt={v}: tracer_opt=4 (WVT) must be set on every domain or on none '
            '(the WVT fields are packaged per domain, and each side of a nest exchange tests only its '
            'own array). Stock tracer_opt values may still differ between domains.'
        )


def validate_tracer_sources_need_wvt(dynamics):
    """tracer3dsource / tracer3dsink require tracer_opt=4 (mirrors the check_a_mundo rule added
    2026-09-18: the 3-D masks they read are Registry-packaged on tracer_opt==4 and their solve_em
    loops were keyed on these switches alone). tracer2dsource is deliberately NOT checked: its
    consumers were always gated on tracer_opt==4, so tracer_opt=0 + tracer2dsource=1 is a harmless
    no-op -- the shape of the P1 timing family's n00 configs (2026-09-19). Raises ValueError."""
    if _first(dynamics.get('tracer_opt', 0)) == 4:
        return
    on = {k: _first(dynamics.get(k, 0)) for k in ('tracer3dsource', 'tracer3dsink')}
    bad = {k: val for k, val in on.items() if val}
    if bad:
        raise ValueError(
            f'[dynamics] {bad} set without tracer_opt=4: the WVT source/sink masks exist only in a '
            'WVT run. Set tracer_opt=4 or switch the source/sink options off.'
        )


def validate_wvt_regions(wvt_config, dynamics, bl_pbl):
    """Validate the multi-region WVT constraints (mirrors WRF check_a_mundo) so a bad
    config fails fast, before geogrid/metgrid/real, rather than at WRF startup.

    num_wvt_regions is derived from the number of [[wvt.regions]] (1 for the flat
    single-region form). `bl_pbl` is the de-listed bl_pbl_physics value. Raises ValueError.
    """
    n_regions = count_wvt_regions(wvt_config)
    if n_regions > MAX_WVT_REGIONS:
        raise ValueError(
            f'[wvt] defines {n_regions} regions but the build supports at most {MAX_WVT_REGIONS} '
            '(MAX_WVT_REGIONS). Reduce the number of [[wvt.regions]].'
        )
    if n_regions <= 1:
        return

    tracer_opt = _first(dynamics.get('tracer_opt', 0))
    t3src = _first(dynamics.get('tracer3dsource', 0))
    t3sink = _first(dynamics.get('tracer3dsink', 0))
    if tracer_opt != 4:
        raise ValueError(
            f'[wvt] defines {n_regions} regions but [dynamics] tracer_opt={tracer_opt} (must be 4). '
            'Set tracer_opt=4 to enable multi-region WVT, or define a single region.'
        )
    if bl_pbl != 0:
        raise ValueError(
            f'multi-region WVT ({n_regions} regions) requires bl_pbl_physics=0 (SMS-3DTKE); '
            f'got bl_pbl_physics={bl_pbl}. Multi-region is not wired for YSU.'
        )
    if t3src != 0 or t3sink != 0:
        raise ValueError(
            f'multi-region WVT ({n_regions} regions) requires tracer3dsource=0 and tracer3dsink=0 '
            f'(3D source/sink is single-region only); got tracer3dsource={t3src}, tracer3dsink={t3sink}.'
        )

    # The auxinput8 trmask read is gated on tracer2dsource/tracer3dsource/tracer3dsink in
    # share/mediation_wrfmain.F, and NOTHING couples that gate to the region count. With all
    # three off, grid%trmask stays zero and every region sources nothing -- silently, with no
    # error anywhere. That failure has happened in this stack before; refuse it here.
    t2src = _first(dynamics.get('tracer2dsource', 0))
    if t2src != 1:
        raise ValueError(
            f'multi-region WVT ({n_regions} regions) requires tracer2dsource=1 so WRF actually '
            f'reads the trmask file; got tracer2dsource={t2src}. Without it grid%trmask stays '
            'zero and every region sources nothing, without erroring.'
        )


################################################
### Functions


def check_nml_params(domains):
    """
    Validate executables and domain configuration from parameters.toml.
    """
    ##############################################
    ### Assign and check executables

    if not params.wrf_path.exists():
        raise ValueError(f'wrf path does not exist: {params.wrf_path}')

    if not params.wrf_exe.exists():
        raise ValueError(f'wrf.exe does not exist: {params.wrf_exe}')

    if not params.real_exe.exists():
        raise ValueError(f'real.exe does not exist: {params.real_exe}')

    if not params.wps_path.exists():
        raise ValueError(f'wps path does not exist: {params.wps_path}')

    if not params.geogrid_exe.exists():
        raise ValueError(f'geogrid.exe does not exist: {params.geogrid_exe}')

    if not params.metgrid_exe.exists():
        raise ValueError(f'metgrid.exe does not exist: {params.metgrid_exe}')

    ##############################################
    ### Validate domain config from TOML [domains] section

    if 'domains' not in params.file:
        raise ValueError('[domains] section is missing from parameters.toml.')

    domain_config = params.file['domains']

    parent_ids = utils.to_list(domain_config['parent_id'])

    src_n_domains = len(parent_ids)

    for f in params.geogrid_array_fields:
        if f not in domain_config:
            raise ValueError(f'The field {f} is missing from [domains] in parameters.toml.')

        v = utils.to_list(domain_config[f])

        if len(v) != src_n_domains:
            raise ValueError(f'The field {f} must be an array with {src_n_domains} values.')

        if f in ('e_we', 'e_sn'):
            for i in v:
                if i < 100:
                    raise ValueError('The number of grid points in the domain must be greater than or equal to 100.')

    for f in params.geogrid_single_fields:
        if f not in domain_config:
            raise ValueError(f'The field {f} is missing from [domains] in parameters.toml.')

        v = domain_config[f]

        if isinstance(v, list):
            raise ValueError(f'The field {f} must be a single value.')

    if domains:

        domains.sort()

        # Check if assigned domains are properly nested
        for domain in domains[1:]:
            parent_id = parent_ids[domain - 1]
            if parent_id not in domains:
                raise ValueError(
                    f'The parent_id {parent_id} does not exist in the assigned domains. The parent/child domains must match.'
                )
    else:
        domains = list(range(1, src_n_domains + 1))

    ##############################################
    ### Validate physics configuration

    physics = params.file.get('physics', {})
    dynamics = params.file.get('dynamics', {})

    bl_pbl = physics.get('bl_pbl_physics', 0)
    if isinstance(bl_pbl, list):
        bl_pbl = bl_pbl[0]

    if bl_pbl == 0:
        scalar_pblmix = physics.get('scalar_pblmix', 0)
        tracer_pblmix = physics.get('tracer_pblmix', 0)
        if scalar_pblmix == 0:
            raise ValueError(
                'bl_pbl_physics=0 requires scalar_pblmix=1. '
                'Without it, scalars will not be vertically mixed.'
            )
        if _first(dynamics.get('tracer_opt', 0)) > 0 and tracer_pblmix == 0:
            raise ValueError(
                'bl_pbl_physics=0 with tracer_opt>0 requires tracer_pblmix=1. '
                'Without it, tracers will not be vertically mixed.'
            )

    # WVT multi-region constraints (caught before geogrid/metgrid/real).
    validate_tracer_opt_uniform(dynamics)
    validate_tracer_sources_need_wvt(dynamics)
    validate_wvt_regions(params.file.get('wvt', {}), dynamics, bl_pbl)

    return src_n_domains, domains


def _scalarize(value):
    """First element of a per-domain list, or the value itself. None stays None.

    Namelist fields reach here either as a scalar or as an already-broadcast per-domain list,
    depending on whether the user wrote one value or many; every comparison against a switch
    wants the domain-1 value.
    """
    if isinstance(value, list):
        return value[0] if value else None
    return value


def set_nml_params(domains=None):
    """
    Build WPS and WRF namelists from scratch using defaults + TOML overrides.
    """
    #########################################
    ### Read domain geometry from TOML [domains] section

    grid_config = params.file['domains']
    parent_ids = utils.to_list(grid_config['parent_id'])
    old_n_domains = len(parent_ids)

    # Build geogrid dict from TOML
    geogrid = {}
    for field in defaults.GEOGRID_ARRAY_FIELDS:
        geogrid[field] = list(utils.to_list(grid_config[field]))
    for field in defaults.GEOGRID_SINGLE_FIELDS:
        geogrid[field] = grid_config[field]
    for field, default_val in defaults.GEOGRID_OPTIONAL_DEFAULTS.items():
        geogrid[field] = grid_config.get(field, default_val)
    if 'truelat1' in grid_config:
        geogrid['truelat1'] = grid_config['truelat1']
    if 'truelat2' in grid_config:
        geogrid['truelat2'] = grid_config['truelat2']

    #########################################
    ### Domain subsetting

    if domains:

        domains.sort()

        # Update the geogrid if needed
        _ = utils.update_geogrid(geogrid, domains)

        n_domains = len(domains)

    else:
        domains = list(range(1, old_n_domains + 1))
        n_domains = old_n_domains

    data_path = params.data_path

    #########################################
    ### BUILD WPS NAMELIST

    wps_share = dict(defaults.WPS_SHARE_DEFAULTS)
    wps_share['max_dom'] = n_domains
    wps_share['opt_output_from_geogrid_path'] = str(data_path)

    wps_geogrid = dict(geogrid)
    wps_geogrid['geog_data_path'] = str(params.geog_data_path)
    wps_geogrid['opt_geogrid_tbl_path'] = str(params.geogrid_exe.parent.joinpath('geogrid'))

    wps_ungrib = dict(defaults.WPS_UNGRIB_DEFAULTS)

    wps_metgrid = dict(defaults.WPS_METGRID_DEFAULTS)
    if params.is_wrf_input:
        wps_metgrid['fg_name'] = str(data_path.joinpath('WRF'))
    else:
        fg_names = [str(data_path.joinpath('ERA5'))]
        if params.sst_source == 'cci':
            fg_names.append(str(data_path.joinpath('SST')))
        wps_metgrid['fg_name'] = fg_names if len(fg_names) > 1 else fg_names[0]
    wps_metgrid['opt_metgrid_tbl_path'] = str(params.metgrid_exe.parent.joinpath('metgrid'))
    wps_metgrid['opt_output_from_metgrid_path'] = str(data_path)

    #########################################
    ### BUILD WRF NAMELIST

    ## time_control
    wrf_tc = dict(defaults.WRF_TIME_CONTROL_DEFAULTS)

    ## domains
    wrf_dom = dict(defaults.WRF_DOMAINS_DEFAULTS)

    # Merge domain geometry from geogrid (only WRF-relevant fields)
    for k, v in geogrid.items():
        if k in defaults.WRF_DOMAIN_GEOGRID_FIELDS:
            wrf_dom[k] = v

    wrf_dom['max_dom'] = n_domains

    # e_vert from TOML (scalar or array)
    e_vert = grid_config.get('e_vert', 33)
    wrf_dom['e_vert'] = broadcast_field(e_vert, n_domains, domains, old_n_domains)

    # p_top_requested from TOML (override default)
    wrf_dom['p_top_requested'] = grid_config.get('p_top_requested', defaults.WRF_DOMAINS_DEFAULTS['p_top_requested'])

    # parent_time_step_ratio from TOML or derive from parent_grid_ratio
    ptr = grid_config.get('parent_time_step_ratio', geogrid.get('parent_grid_ratio', [1] * n_domains))
    wrf_dom['parent_time_step_ratio'] = broadcast_field(ptr, n_domains, domains, old_n_domains)
    wrf_dom['parent_time_step_ratio'][0] = 1

    # grid_id: sequential
    wrf_dom['grid_id'] = list(range(1, n_domains + 1))

    # time_step: derived from dx
    wrf_dom['time_step'] = int(wrf_dom['dx'] * 0.001 * 6)

    # max_step_increase_pct: broadcast then force parent=5
    msip = broadcast_field(wrf_dom.get('max_step_increase_pct', 51), n_domains, domains, old_n_domains)
    msip[0] = 5
    wrf_dom['max_step_increase_pct'] = msip

    # Broadcast remaining per-domain domain fields
    for field in defaults.DOMAINS_PER_DOMAIN_FIELDS:
        if field in wrf_dom and field not in ('max_step_increase_pct', 'parent_time_step_ratio', 'e_vert'):
            wrf_dom[field] = broadcast_field(wrf_dom[field], n_domains, domains, old_n_domains)

    ## physics: merge defaults + user overrides
    physics = dict(defaults.PHYSICS_DEFAULTS)
    if 'physics' in params.file:
        physics.update(params.file['physics'])
    for field in defaults.PHYSICS_PER_DOMAIN_FIELDS:
        if field in physics:
            physics[field] = broadcast_field(physics[field], n_domains, domains, old_n_domains)

    ## dynamics: merge defaults + user overrides
    dynamics = dict(defaults.DYNAMICS_DEFAULTS)
    if 'dynamics' in params.file:
        dynamics.update(params.file['dynamics'])

    ## WVT ONLY: moisture and tags must ride the same advection scheme, and the method names it
    ## (defaults.WVT_ADV_OPT). Injected here rather than set as a global default so a NON-WVT run
    ## keeps WRF's own moist_adv_opt -- it has no tags to stay consistent with, so the scheme is
    ## the user's choice. Done BEFORE the per-domain broadcast below so both switches become
    ## max_dom-length lists like every other dynamics field.
    if _scalarize(dynamics.get('tracer_opt', 0)) == 4:
        user_dyn = params.file.get('dynamics') or {}
        for field in ('moist_adv_opt', 'tracer_adv_opt'):
            asked = _scalarize(user_dyn.get(field))
            if asked is not None and asked != defaults.WVT_ADV_OPT:
                raise ValueError(
                    f'[dynamics] {field}={asked} is incompatible with tracer_opt=4 (WVT). Moisture '
                    f'and its tags must use the same advection scheme, and the WVT method specifies '
                    f'{defaults.WVT_ADV_OPT} (5th-order WENO with a positive-definite limiter; '
                    f'Insua-Costa & Miguez-Macho 2018). WRF itself refuses the mismatch at real.exe. '
                    f'Omit {field} to have it set automatically.'
                )
            dynamics[field] = defaults.WVT_ADV_OPT

    for field in defaults.DYNAMICS_PER_DOMAIN_FIELDS:
        if field in dynamics:
            dynamics[field] = broadcast_field(dynamics[field], n_domains, domains, old_n_domains)

    ## other sections
    bdy_control = dict(defaults.WRF_BDY_CONTROL_DEFAULTS)
    diags = {}
    namelist_quilt = dict(defaults.WRF_NAMELIST_QUILT_DEFAULTS)
    fdda = {}
    grib2 = {}

    ## Passthrough: unknown [domains] keys → WRF &domains
    domain_overrides = {k: v for k, v in params.file['domains'].items()
                        if k not in defaults.DOMAINS_PIPELINE_KEYS}
    apply_overrides(wrf_dom, domain_overrides, domains, old_n_domains)

    ## Passthrough: unknown [time_control] keys → WRF &time_control
    tc_overrides = {k: v for k, v in params.file['time_control'].items()
                    if k not in defaults.TIME_CONTROL_PIPELINE_KEYS}
    apply_overrides(wrf_tc, tc_overrides, domains, old_n_domains)

    ## WVT: auto-inject auxinput8 settings when tracer_opt=4
    ## TRMASK is read via manual open/input/close in mediation_wrfmain.F.
    ## Only io_form and inname are needed -- interval/begin/end alarm settings
    ## interfere with the manual read and must NOT be set.
    tracer_opt_val = dynamics.get('tracer_opt', 0)
    if isinstance(tracer_opt_val, list):
        tracer_opt_val = tracer_opt_val[0]
    if tracer_opt_val == 4:
        wrf_tc.setdefault('io_form_auxinput8', 2)
        wrf_tc.setdefault('auxinput8_inname', 'trmask_d<domain>')
        # num_wvt_regions drives the wvtreg dimension + tracer packages in WRF. The
        # [wvt] region list is the single source of truth; derive the count and inject
        # it. If the user also set num_wvt_regions in [dynamics] (e.g. copied from a raw
        # namelist), require the two to agree rather than silently overriding.
        n_regions = count_wvt_regions(params.file.get('wvt', {}))
        user_n = dynamics.get('num_wvt_regions')
        if isinstance(user_n, list):
            user_n = user_n[0]
        if user_n is not None and int(user_n) != n_regions:
            raise ValueError(
                f'[dynamics] num_wvt_regions={user_n} does not match the {n_regions} region(s) '
                'defined in [wvt]. Omit num_wvt_regions to derive it automatically, or fix the count.'
            )
        dynamics['num_wvt_regions'] = n_regions

        # Lateral-boundary face tags: the LAST n_bdy regions are boundary regions.
        # normalize_wvt_regions appends them, so "last" is true by construction and there is
        # no ordering to validate. 0 (the Registry default) means the face relabel in
        # solve_em.F never runs, which is exactly today's 8-region behaviour.
        n_bdy = len(normalize_boundary_faces(params.file.get('wvt', {})))
        user_b = dynamics.get('num_wvt_bdy_regions')
        if isinstance(user_b, list):
            user_b = user_b[0]
        if user_b is not None and int(user_b) != n_bdy:
            raise ValueError(
                f'[dynamics] num_wvt_bdy_regions={user_b} does not match the {n_bdy} face(s) '
                'in [wvt] boundary_faces. Omit it to derive it automatically.'
            )
        dynamics['num_wvt_bdy_regions'] = n_bdy

        # Face tags are d01-only. On a nest, solve_em.F's spec_bdy_final runs AFTER the
        # relabel and overwrites the boundary cells, and relax_bdy_scalar/spec_bdy_scalar run
        # for tracers as well -- so d02 shells would be silently wrong rather than absent.
        # create_trmask writes a mask for every domain, so without this guard a two-domain run
        # would look fine and produce a broken d02 attribution.
        if n_bdy:
            # ⚠ Read the DERIVED domain count, not a TOML key. `max_dom` is something this
            # function WRITES (wrf_dom['max_dom'] = n_domains above); real configs define
            # nests with parent_id/parent_grid_ratio arrays and never carry a max_dom key, so
            # reading it back from params.file always saw the default 1 and the guard never
            # fired. Both arms of round wvt-bdytags-code-2 built a two-domain config and
            # walked straight through it. Same shape as the earlier _first() defect: a check
            # reading a value that is not where it looks.
            if int(n_domains) > 1:
                raise ValueError(
                    f'[wvt] boundary_faces is set but the run has {n_domains} domains. '
                    f'Lateral-boundary face '
                    'tags are validated for a single domain only: on a nest WRF overwrites the '
                    'relabelled boundary cells after the relabel runs. See '
                    'wvt_boundary_tags_design.md sec 8.'
                )

        # create_trmask derives its margin width from [wvt] relax_width -> [bdy_control]
        # spec_bdy_width -> literal 5, while the namelist value written below comes from
        # defaults.WRF_BDY_CONTROL_DEFAULTS. Two independent derivations that agree only by
        # coincidence of defaults. For a source mask a mismatch is cosmetic; for a boundary
        # shell the margin width IS the region definition, so tie them together.
        if n_bdy:
            # Both sides must come from the SAME merged view. Reading the namelist side out
            # of `bdy_control` was wrong: that dict is seeded from defaults above and the
            # user's [bdy_control] is not merged into it until the override loop further
            # down, so it always read 5 -- refusing a legitimate spec_bdy_width=10 and,
            # worse, PASSING relax_width=5 alongside spec_bdy_width=10, which is exactly the
            # silent mask/namelist mismatch this check exists to catch.
            wvt_cfg = params.file.get('wvt', {})
            bdy_cfg = params.file.get('bdy_control', {})
            nml_w = _first(bdy_cfg.get('spec_bdy_width',
                                       defaults.WRF_BDY_CONTROL_DEFAULTS['spec_bdy_width']))
            # The mask side is asked THROUGH create_trmask's own resolver, so this compares the
            # width the masks will actually be built with -- not a re-derivation of it.
            mask_w = resolve_relax_width(wvt_cfg, bdy_cfg)
            if int(mask_w) != int(nml_w):
                raise ValueError(
                    f'[wvt] boundary_faces is set, but the mask margin width ({mask_w}) does not '
                    f'match the spec_bdy_width written to the namelist ({nml_w}). The shells would '
                    'not line up with the boundary zone they are meant to cover.'
                )

    ## Direct WRF namelist sections from TOML
    override_sections = {
        'fdda': fdda,
        'bdy_control': bdy_control,
        'grib2': grib2,
        'namelist_quilt': namelist_quilt,
        'diags': diags,
    }
    for section_name, target in override_sections.items():
        if section_name in params.file:
            apply_overrides(target, params.file[section_name], domains, old_n_domains)

    #########################################
    ### TIME / OUTPUT LOGIC

    start_date = pendulum.parse(params.file['time_control']['start_date'])
    if 'end_date' in params.file['time_control']:
        end_date = pendulum.parse(params.file['time_control']['end_date'])
    elif 'duration_hours' in params.file['time_control']:
        end_date = start_date.add(hours=params.file['time_control']['duration_hours'])
    else:
        raise ValueError('end_date or duration must be assigned in the parameters.')

    if start_date > end_date:
        raise ValueError(f'start_date ({start_date}) is greater than end_date ({end_date}).')

    interval_hours = int(params.file['time_control']['interval_hours'])

    wps_share['interval_seconds'] = interval_hours * 60 * 60
    wrf_tc['interval_seconds'] = interval_hours * 60 * 60

    ## FDDA defaults: apply per-domain where grid_fdda > 0, scalars as-is
    if 'grid_fdda' in fdda:
        grid_fdda = broadcast_field(fdda['grid_fdda'], n_domains, domains, old_n_domains)
        fdda['grid_fdda'] = grid_fdda
        nudge_mask = [v > 0 for v in grid_fdda]

        # Per-domain defaults (masked by grid_fdda)
        for key, default_val in defaults.FDDA_PER_DOMAIN_DEFAULTS.items():
            if key not in fdda:
                fdda[key] = [default_val if on else 0 for on in nudge_mask]

        # Scalar defaults
        fdda.setdefault('gfdda_inname', 'wrffdda_d<domain>')

        # Set runtime values for gfdda_interval_m and gfdda_end_h if not user-specified
        if 'gfdda_interval_m' not in params.file.get('fdda', {}):
            fdda['gfdda_interval_m'] = [interval_hours * 60 if on else 0 for on in nudge_mask]
        if 'gfdda_end_h' not in params.file.get('fdda', {}):
            duration_hours = int((end_date - start_date).total_hours())
            fdda['gfdda_end_h'] = [duration_hours if on else 0 for on in nudge_mask]

        # Broadcast any remaining user-specified per-domain fdda fields
        for field in defaults.FDDA_PER_DOMAIN_FIELDS:
            if field in fdda and field != 'grid_fdda':
                fdda[field] = broadcast_field(fdda[field], n_domains, domains, old_n_domains)

    # History intervals - list per domain (was dict keyed by domain number)
    history_intervals_raw = params.file['time_control']['history_file']['interval_hours']
    history_intervals = [int(hi * 60) for hi in utils.to_list(history_intervals_raw)]
    history_interval_nml = broadcast_field(history_intervals, n_domains, domains, old_n_domains)

    wrf_tc['history_interval'] = history_interval_nml

    n_hours_per_file = 24

    frames_per_outfile = []
    for hi in history_interval_nml:
        if hi == 0:
            frames_per_outfile.append(0)
        else:
            hours = int(hi / 60)
            frames_per_outfile.append(int(n_hours_per_file / hours))

    history_begin = int(params.file['time_control']['history_file']['begin_hours']) * 60

    wrf_tc['frames_per_outfile'] = frames_per_outfile
    wrf_tc['history_begin'] = [history_begin] * n_domains
    wrf_tc['history_outname'] = params.history_outname

    if params._chunked_mode_active:
        # Chunked mode: chunk_start IS the chunk's real WRF start, and history_begin
        # already holds the remaining spin-up for this chunk (set by main.py via
        # params.set_chunk_dates). Subtracting again would double-count the offset.
        new_start_date = start_date
    else:
        # Single-stage / preprocess-only: pull start back by history_begin so WRF actually
        # integrates the spin-up period; history_begin_h then suppresses wrfout for that span.
        new_start_date = start_date.subtract(minutes=history_begin)

    interval = pendulum.interval(start_date, end_date.subtract(minutes=1))

    domain_i = list(range(1, len(domains) + 1))
    output_files = utils.dt_to_file_names('wrfout', interval.range('days'), domain_i)

    ## Summary file
    summ_file = params.file['time_control']['summary_file']

    if summ_file['output']:
        if start_date.hour != 0 or end_date.hour != 0:
            raise ValueError('Generating the summary file requires that the start and end dates are on the hour.')

        diag_interval_days = int(summ_file['interval_days'])
        n_days_per_file = summ_file['n_days_per_file']

        if n_days_per_file < diag_interval_days:
            raise ValueError('For the summary file, n_days_per_file must be >= interval_days')

        wrf_tc['output_diagnostics'] = 1

        wrf_tc['auxhist3_interval'] = [diag_interval_days * 60 * 24] * n_domains

        wrf_tc['frames_per_auxhist3'] = [int(n_days_per_file / diag_interval_days)] * n_domains

        wrf_tc['auxhist3_outname'] = params.summ_outname
        wrf_tc['io_form_auxhist3'] = 2
        wrf_tc['auxhist3_begin'] = [history_begin + 1440] * n_domains

        interval = pendulum.interval(start_date.add(days=1), end_date.add(days=1))

        if interval.days % n_days_per_file != 0:
            raise ValueError(
                f'For the summary file, n_days_per_file ({n_days_per_file}) must divide evenly into '
                f'the end_date - start_date interval ({interval.days}).'
            )

        dts = list(interval.range('days', n_days_per_file))[:-1]
        files = utils.dt_to_file_names('wrfxtrm', dts, domain_i)
        output_files.extend(files)

    else:
        wrf_tc['output_diagnostics'] = 0

    ## Z-level file
    z_level_file = params.file['time_control']['z_level_file']

    if z_level_file['output']:
        diags['z_lev_diags'] = 1

        diags['z_levels'] = [-z for z in z_level_file['z_levels']]
        diags['num_z_levels'] = len(z_level_file['z_levels'])

        wrf_tc['auxhist22_outname'] = params.zlevel_outname
        wrf_tc['io_form_auxhist22'] = 2
        wrf_tc['auxhist22_interval'] = history_interval_nml
        wrf_tc['frames_per_auxhist22'] = frames_per_outfile
        wrf_tc['auxhist22_begin'] = [history_begin] * n_domains

        interval = pendulum.interval(start_date, end_date.subtract(minutes=1))
        files = utils.dt_to_file_names('wrfzlevels', interval.range('days'), domain_i)
        output_files.extend(files)

    else:
        diags['z_lev_diags'] = 0

    ## Date arrays
    wps_share['start_date'] = [new_start_date.strftime(params.wps_date_format)] * n_domains
    wps_share['end_date'] = [end_date.strftime(params.wps_date_format)] * n_domains

    wrf_tc['start_year'] = [new_start_date.year] * n_domains
    wrf_tc['start_month'] = [new_start_date.month] * n_domains
    wrf_tc['start_day'] = [new_start_date.day] * n_domains
    wrf_tc['start_hour'] = [new_start_date.hour] * n_domains
    wrf_tc['end_year'] = [end_date.year] * n_domains
    wrf_tc['end_month'] = [end_date.month] * n_domains
    wrf_tc['end_day'] = [end_date.day] * n_domains
    wrf_tc['end_hour'] = [end_date.hour] * n_domains
    wrf_tc['input_from_file'] = [True] * n_domains

    ## prec_acc_dt: window each domain's PREC_ACC_C/NC + SNOW_ACC_NC to its own history
    ## interval, so frame t carries the precip over (t - history_interval, t]. Without it the
    ## only precip signal is the RAINC/RAINNC running totals, which restart from zero at every
    ## cold start -- so an archive stitched from independent runs loses one interval per seam.
    ## setdefault, not assignment: `physics` already holds the [physics] TOML block, which wins.
    ## list(): history_interval_nml is also bound into wrf_tc['history_interval'] and
    ## auxhist22_interval, and this file mutates such lists in place elsewhere (see
    ## max_step_increase_pct), so sharing one object across three namelist groups is a footgun.
    physics.setdefault('prec_acc_dt', list(history_interval_nml))

    ## Noah-MP symlink
    surface_physics = physics.get('sf_surface_physics', 4)
    if not isinstance(surface_physics, list):
        surface_physics = [surface_physics]

    # if 4 in surface_physics:
    #     subprocess.run(
    #         'ln -sf GEOGRID.TBL.ARW.noahmp GEOGRID.TBL',
    #         shell=True,
    #         check=False,
    #         cwd=params.wps_path.joinpath('geogrid'),
    #     )

    #############################################
    ### ASSEMBLE AND WRITE NAMELISTS

    # OrderedDict preserves section order; f90nml.Namelist sorts plain dicts
    # alphabetically, which breaks WPS (geogrid reads &share then &geogrid sequentially).
    wps_nml = f90nml.Namelist(OrderedDict([
        ('share', wps_share),
        ('geogrid', wps_geogrid),
        ('ungrib', wps_ungrib),
        ('metgrid', wps_metgrid),
    ]))

    wrf_sections = OrderedDict([
        ('time_control', wrf_tc),
        ('domains', wrf_dom),
        ('physics', physics),
        ('fdda', fdda),
        ('dynamics', dynamics),
        ('bdy_control', bdy_control),
        ('diags', diags),
        ('grib2', grib2),
        ('namelist_quilt', namelist_quilt),
    ])

    wrf_nml = f90nml.Namelist(wrf_sections)

    with open(params.wps_nml_path, 'w') as nml_file:
        wps_nml.write(nml_file)

    with open(params.wrf_nml_path, 'w') as nml_file:
        wrf_nml.write(nml_file)

    return new_start_date.naive(), end_date.naive(), int(interval_hours), output_files


def set_ndown_params(interval_seconds):
    """
    Should be set after ndown is run.
    """
    wrf_nml = f90nml.read(params.wrf_nml_path)

    wrf_nml['bdy_control']['have_bcs_moist'] = True
    wrf_nml['bdy_control']['have_bcs_scalar'] = True

    tracer_opt_val = params.file.get('dynamics', {}).get('tracer_opt', 0)
    if isinstance(tracer_opt_val, list):
        tracer_opt_val = tracer_opt_val[0]
    if tracer_opt_val == 4:
        wrf_nml['bdy_control']['have_bcs_tracer'] = True

    wrf_nml['time_control']['io_form_auxinput2'] = 2
    wrf_nml['time_control']['interval_seconds'] = interval_seconds

    with open(params.wrf_nml_path, 'w') as nml_file:
        wrf_nml.write(nml_file)


def apply_restart_namelist(restart_time, restart_interval_minutes, end_date_override=None):
    """In-place edit of run_path/namelist.input to enable a restart run.

    restart_time: pendulum datetime — the wrfrst timestamp; None for cold-start with restart writes enabled.
    restart_interval_minutes: int — WRF restart_interval (interval_days * 24 * 60).
    end_date_override: pendulum datetime — when set, override end_date* fields (used by stop_after_upload).

    Note: adjust_output_times is intentionally NOT modified. Confirmed by reading WRF's share/output_wrf.F
    that the flag only affects history (wrfout) writes, not restart. wrfrst timestamps always reflect the
    actual internal model time, which is what we want for restart correctness.
    """
    nml_path = params.run_path.joinpath('namelist.input')
    nml = f90nml.read(nml_path)
    parent_id = nml['domains']['parent_id']
    n_domains = len(parent_id) if isinstance(parent_id, (list, tuple)) else 1

    if restart_time is not None:
        nml['time_control']['restart'] = True
        nml['time_control']['start_year']   = [restart_time.year]   * n_domains
        nml['time_control']['start_month']  = [restart_time.month]  * n_domains
        nml['time_control']['start_day']    = [restart_time.day]    * n_domains
        nml['time_control']['start_hour']   = [restart_time.hour]   * n_domains
        nml['time_control']['start_minute'] = [restart_time.minute] * n_domains
        nml['time_control']['start_second'] = [restart_time.second] * n_domains
    nml['time_control']['restart_interval'] = restart_interval_minutes
    nml['time_control']['override_restart_timers'] = True
    # write_hist_at_0h_rst forces wrf.exe to write a history frame at chunk_start on restart.
    # Without this, restart chunks skip the chunk_start write and the first wrfout file is named
    # for chunk_start + history_interval (e.g. Feb13_03:00:00.nc), leaving the previous chunk's
    # 1-frame Feb13_00:00:00.nc file orphaned. With it, chunk N+1 writes to Feb13_00:00:00.nc and
    # NF_CLOBBER overwrites the prior 1-frame file with the full 8-frame version. See
    # share/mediation_integrate.F lines 89-120.
    nml['time_control']['write_hist_at_0h_rst'] = True
    if end_date_override is not None:
        nml['time_control']['end_year']   = [end_date_override.year]   * n_domains
        nml['time_control']['end_month']  = [end_date_override.month]  * n_domains
        nml['time_control']['end_day']    = [end_date_override.day]    * n_domains
        nml['time_control']['end_hour']   = [end_date_override.hour]   * n_domains
        nml['time_control']['end_minute'] = [end_date_override.minute] * n_domains
        nml['time_control']['end_second'] = [end_date_override.second] * n_domains

    with open(nml_path, 'w') as f:
        nml.write(f)


def update_metgrid_levels():
    """
    Read the first met_em file and update namelist.input with the actual
    num_metgrid_levels and num_metgrid_soil_levels.
    """
    import h5netcdf

    met_em_files = sorted(params.data_path.glob('met_em.d01.*.nc'))
    if not met_em_files:
        raise FileNotFoundError('No met_em.d01.*.nc files found after metgrid.')

    with h5netcdf.File(str(met_em_files[0]), 'r') as f:
        num_metgrid_levels = int(f.attrs['BOTTOM-TOP_GRID_DIMENSION'])
        num_metgrid_soil_levels = int(f.attrs['NUM_METGRID_SOIL_LEVELS'])

    wrf_nml = f90nml.read(params.wrf_nml_path)
    wrf_nml['domains']['num_metgrid_levels'] = num_metgrid_levels
    wrf_nml['domains']['num_metgrid_soil_levels'] = num_metgrid_soil_levels

    with open(params.wrf_nml_path, 'w') as nml_file:
        wrf_nml.write(nml_file)
