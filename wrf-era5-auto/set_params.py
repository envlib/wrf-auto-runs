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

    return src_n_domains, domains


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
    wps_metgrid['fg_name'] = str(data_path.joinpath('ERA5'))
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

    ## prec_acc_dt
    physics['prec_acc_dt'] = history_interval_nml

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
    wrf_nml['time_control']['io_form_auxinput2'] = 2
    wrf_nml['time_control']['interval_seconds'] = interval_seconds

    with open(params.wrf_nml_path, 'w') as nml_file:
        wrf_nml.write(nml_file)
