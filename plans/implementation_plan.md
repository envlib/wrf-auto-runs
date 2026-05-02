# Implementation Plan: Consolidate Configuration into parameters.toml

## Context

Currently users must maintain 3 files: `parameters.toml`, `namelist.wps`, and `namelist.input`. Most namelist parameters are either hardcoded by the project scope or derived by `set_params.py`, making the namelist files mostly boilerplate. This change moves all user-configurable settings into `parameters.toml` and generates both namelists entirely from code.

**Key user requirement**: Per-domain arrays (physics, dynamics, domain geometry) should be sized to the **full** domain count defined in `[domains]`. The top-level `domains` parameter (e.g., `[1, 2]`) slices the arrays at runtime — users never need to resize arrays when changing which domains to run.

---

## Files to Modify

| File | Change |
|---|---|
| `parameters_example.toml` | Add `[domains]`, `[physics]`, `[dynamics]` sections; remove references to namelist files |
| `params.py` | Remove `src_wps_nml_path`, `src_wrf_nml_path`; import field definitions from `defaults.py` |
| `set_params.py` | Rewrite to build namelists from scratch using defaults + TOML overrides |
| `check_ndown.py` | Read domain config from `params.file['domains']` instead of `f90nml.read()` |
| `main.py` | Minor cleanup — domain parsing unchanged |

## New Files

| File | Purpose |
|---|---|
| `defaults.py` | All namelist defaults: hardcoded values, physics/dynamics sensible defaults, field classification sets |

## Files to Delete

| File | Reason |
|---|---|
| `namelist.wps` | No longer needed — generated entirely by code |
| `namelist.input` | No longer needed — generated entirely by code |

---

## Step 1: Create `defaults.py`

This file contains all namelist default values organized by section, plus metadata about which fields are per-domain arrays.

```python
#!/usr/bin/env python3
"""
Default namelist values for WPS and WRF.

Hardcoded values are fixed by the project scope (ERA5 input, Docker container, ARW core).
Physics and dynamics defaults are sensible starting points that users can override
via [physics] and [dynamics] sections in parameters.toml.
"""

# ============================================================
# WPS Namelist Defaults (all hardcoded — not user-configurable)
# ============================================================

WPS_SHARE_DEFAULTS = {
    'wrf_core': 'ARW',
    'io_form_geogrid': 2,
    'debug_level': 0,
}

WPS_UNGRIB_DEFAULTS = {
    'out_format': 'WPS',
    'prefix': 'ERA5',
}

WPS_METGRID_DEFAULTS = {
    'io_form_metgrid': 2,
}

# ============================================================
# WRF Namelist Defaults — Hardcoded sections
# ============================================================

WRF_TIME_CONTROL_DEFAULTS = {
    'run_days': 0,
    'run_hours': 0,
    'run_minutes': 0,
    'run_seconds': 0,
    'restart': False,
    'restart_interval': 500000,
    'adjust_output_times': True,
    'io_form_history': 2,
    'io_form_restart': 2,
    'io_form_input': 2,
    'io_form_boundary': 2,
    'auxinput4_inname': 'wrflowinp_d<domain>',
    'auxinput4_interval': 360,
    'io_form_auxinput4': 2,
}

WRF_DOMAINS_DEFAULTS = {
    'use_adaptive_time_step': True,
    'step_to_output_time': True,
    'target_cfl': 1.2,
    'max_step_increase_pct': 51,   # per-domain; parent forced to 5 by code
    'starting_time_step': -1,
    'max_time_step': -1,
    'min_time_step': -1,
    'adaptation_domain': 1,
    'time_step_fract_num': 0,
    'time_step_fract_den': 1,
    'p_top_requested': 5000,
    'num_metgrid_levels': 38,      # ERA5 = 37 pressure levels + surface
    'num_metgrid_soil_levels': 4,
    'feedback': 0,
    'smooth_option': 0,
}

WRF_BDY_CONTROL_DEFAULTS = {
    'spec_bdy_width': 5,
    'specified': True,
}

WRF_NAMELIST_QUILT_DEFAULTS = {
    'nio_tasks_per_group': 0,
    'nio_groups': 1,
}

# ============================================================
# WRF Physics Defaults — User can override via [physics] in TOML
# ============================================================

PHYSICS_DEFAULTS = {
    'mp_physics': 6,            # Thompson
    'cu_physics': 16,           # New Tiedtke
    'ra_lw_physics': 4,         # RRTMG
    'ra_sw_physics': 4,         # RRTMG
    'bl_pbl_physics': 0,
    'sf_sfclay_physics': 1,     # MM5 Monin-Obukhov
    'sf_surface_physics': 4,    # Noah-MP
    'sf_ocean_physics': 0,
    'radt': 30,
    'bldt': 0,
    'cudt': 5,
    'icloud': 1,
    'num_land_cat': 21,
    'sf_urban_physics': 0,
    'sst_update': 1,
    'usemonalb': True,
    'sst_skin': 0,
}

# ============================================================
# WRF Dynamics Defaults — User can override via [dynamics] in TOML
# ============================================================

DYNAMICS_DEFAULTS = {
    'hybrid_opt': 2,
    'w_damping': 1,
    'diff_opt': 2,
    'km_opt': 5,
    'diff_6th_opt': 0,
    'diff_6th_factor': 0.12,
    'base_temp': 290.0,
    'damp_opt': 3,
    'zdamp': 5000.0,
    'dampcoef': 0.2,
    'khdif': 0,
    'kvdif': 0,
    'non_hydrostatic': True,
    'moist_adv_opt': 1,
    'scalar_adv_opt': 1,
    'gwd_opt': 1,
    'epssm': 0.5,
}

# ============================================================
# Field Classification
# ============================================================

# Geogrid fields that MUST be arrays of length max_dom
GEOGRID_ARRAY_FIELDS = (
    'parent_id', 'parent_grid_ratio', 'i_parent_start',
    'j_parent_start', 'e_we', 'e_sn', 'geog_data_res',
)

# Geogrid fields that MUST be scalar
GEOGRID_SINGLE_FIELDS = (
    'dx', 'dy', 'map_proj', 'ref_lat', 'ref_lon', 'stand_lon',
)

# Optional geogrid fields with sensible defaults
GEOGRID_OPTIONAL_DEFAULTS = {
    'pole_lat': 90.0,
    'pole_lon': 0.0,
}

# Per-domain fields in &domains that need broadcasting (scalar → array)
DOMAINS_PER_DOMAIN_FIELDS = {
    'target_cfl', 'max_step_increase_pct', 'starting_time_step',
    'max_time_step', 'min_time_step', 'e_vert', 'parent_time_step_ratio',
}

# Per-domain fields in &physics that need broadcasting
PHYSICS_PER_DOMAIN_FIELDS = {
    'mp_physics', 'cu_physics', 'ra_lw_physics', 'ra_sw_physics',
    'bl_pbl_physics', 'sf_sfclay_physics', 'sf_surface_physics',
    'radt', 'bldt', 'cudt', 'sf_urban_physics',
}

# Per-domain fields in &dynamics that need broadcasting
DYNAMICS_PER_DOMAIN_FIELDS = {
    'diff_opt', 'km_opt', 'diff_6th_opt', 'diff_6th_factor',
    'zdamp', 'dampcoef', 'khdif', 'kvdif',
    'non_hydrostatic', 'moist_adv_opt', 'scalar_adv_opt',
    'gwd_opt', 'epssm',
}
```

---

## Step 2: Update `parameters_example.toml`

Add `[domains]` section (replaces `namelist.wps` `&geogrid`). Add commented-out `[physics]`, `[dynamics]`, and `[namelist_overrides]` sections.

```toml
n_cores = 8

domains = [1, 2]  # Which domains to run (subset of those defined below). Comment out to run all.

# output_variables = ['XLAT', 'XLONG', ...] # Filter wrfout to specific variables

# [no_docker]
# wps_path = '/home/mike/Build_WRF/WPS-4.6.0'
# wrf_path = '/home/mike/Build_WRF/WRF-4.6.1-ARW'
# data_path = '/home/mike/data/wrf/tests/test_data/'
# geog_data_path = '/home/mike/WPS_GEOG'

[time_control]
start_date = "1990-07-02 00:00:00"
# end_date = "1990-07-02 06:00:00"
duration_hours = 48
interval_hours = 3

[time_control.history_file]
interval_hours = [24, 1, 1, 1, 1, 1] # Per-domain output interval in hours. Must match domain count in [grid].
begin_hours = 0

[time_control.summary_file]
output = true
interval_days = 1
n_days_per_file = 1

[time_control.z_level_file]
output = true
z_levels = [30, 80, 150, 200, 350, 500, 750, 1000, 1300, 1600, 2000, 2500, 3000, 4000, 5000, 7000, 10000]

# =============================================================================
# Domain geometry — replaces namelist.wps &geogrid section
# Array fields must have one value per domain (6 domains = 6 values).
# The "domains" parameter above selects which subset to actually run.
# =============================================================================

[domains]
dx = 27000
dy = 27000
map_proj = 'lambert'
ref_lat = -39.619
ref_lon = 170.083
truelat1 = -39.619
truelat2 = -39.619
stand_lon = -129.917
parent_id         = [1, 1, 2, 3, 3, 3]
parent_grid_ratio = [1, 3, 3, 3, 3, 3]
i_parent_start    = [1, 35, 14, 160, 171, 42]
j_parent_start    = [1, 21, 12, 35, 308, 419]
e_we              = [100, 133, 316, 319, 316, 406]
e_sn              = [112, 202, 535, 832, 556, 238]
geog_data_res     = ['default', 'default', 'default', 'modis_15s_lake+default', 'modis_15s_lake+default', 'modis_15s_lake+default']
e_vert            = 33               # Scalar = same for all domains, or array per domain
p_top_requested   = 5000
parent_time_step_ratio = [1, 3, 3, 3, 3, 3]

# =============================================================================
# Physics overrides — optional. Uncomment to override defaults.
# Scalar values apply to all domains. Arrays must match domain count above.
# Any valid WRF &physics namelist key is accepted.
#
# Defaults (if this section is omitted):
#   mp_physics = 6 (Thompson), cu_physics = 16 (New Tiedtke),
#   ra_lw_physics = 4 (RRTMG), ra_sw_physics = 4 (RRTMG),
#   bl_pbl_physics = 0, sf_sfclay_physics = 1 (MM5),
#   sf_surface_physics = 4 (Noah-MP), radt = 30, bldt = 0, cudt = 5,
#   icloud = 1, num_land_cat = 21, sf_urban_physics = 0,
#   sst_update = 1, usemonalb = true, sst_skin = 0
# =============================================================================

# [physics]
# mp_physics = 8                         # scalar = same for all domains
# cu_physics = [16, 16, 16, 0, 0, 0]    # array = per domain (disable cumulus on fine grids)

# =============================================================================
# Dynamics overrides — optional. Same rules as [physics].
#
# Defaults (if this section is omitted):
#   hybrid_opt = 2, w_damping = 1, diff_opt = 2, km_opt = 5,
#   diff_6th_opt = 0, diff_6th_factor = 0.12, base_temp = 290.0,
#   damp_opt = 3, zdamp = 5000.0, dampcoef = 0.2, khdif = 0, kvdif = 0,
#   non_hydrostatic = true, moist_adv_opt = 1, scalar_adv_opt = 1,
#   gwd_opt = 1, epssm = 0.5
# =============================================================================

# [dynamics]
# diff_opt = 2
# km_opt = 5

# =============================================================================
# Namelist overrides — escape hatch for arbitrary WRF namelist parameters.
# Keys are namelist section names (&time_control, &domains, &physics, etc.).
# Use this for rare/advanced options not covered above.
# =============================================================================

# [namelist_overrides.physics]
# topo_wind = [0, 1, 1, 2, 2, 2]
# do_radar_ref = 1

# [namelist_overrides.domains]
# use_adaptive_time_step = false

# [namelist_overrides.fdda]
# grid_fdda = 1

# [sentry]
# dsn = ""
# tags = {task = 'wrf run'}

# [ndown]
# [ndown.input]
# type = 's3'
# provider = 'Mega'
# endpoint = 'https://s3.ca-west-1.s4.mega.io'
# access_key_id = ''
# secret_access_key = ''
# path = '/wrf-1k/output/d03'

[remote]
[remote.era5]
type = 's3'
provider = 'Mega'
endpoint = 'https://s3.ca-west-1.s4.mega.io'
access_key_id = ''
secret_access_key = ''
path = '/data/ncar/era5/'

[remote.output]
type = 's3'
provider = 'Mega'
endpoint = 'https://s3.ca-west-1.s4.mega.io'
access_key_id = ''
secret_access_key = ''
path = '/wrf-1k/output/test1'
```

---

## Step 3: Update `params.py`

### Remove
```python
src_wps_nml_path = data_path.joinpath('namelists/namelist.wps')
src_wrf_nml_path = data_path.joinpath('namelists/namelist.input')
```

### Replace field definitions with imports
```python
# Remove these:
geogrid_array_fields = ('parent_id', ...)
geogrid_single_fields = ('dx', ...)
domain_array_fields = ('parent_id', ...)

# Replace with:
from defaults import GEOGRID_ARRAY_FIELDS as geogrid_array_fields
from defaults import GEOGRID_SINGLE_FIELDS as geogrid_single_fields
```

### Keep unchanged
- `wps_nml_path`, `wrf_nml_path` (still write working namelists)
- All path logic, env var overrides, output naming
- `wps_date_format`, `outfile_format`, `history_outname`, etc.

---

## Step 4: Rewrite `set_params.py` (Core Change)

### `check_nml_params(domains)` — Validate TOML instead of namelist files

```python
def check_nml_params(domains):
    """Validate executables and domain configuration from parameters.toml."""

    # Check executables (unchanged from current)
    if not params.wrf_path.exists():
        raise ValueError(f'wrf path does not exist: {params.wrf_path}')
    # ... (same checks for wrf_exe, real_exe, wps_path, geogrid_exe, metgrid_exe)

    # Validate domain config from TOML [domains] section
    if 'domains' not in params.file or not isinstance(params.file['domains'], dict):
        # Note: top-level 'domains' is the run subset (list).
        # We need the [domains] TABLE section. In TOML, if both exist,
        # the table takes precedence as a nested key. We should validate
        # that the [domains] section exists as a dict.
        pass

    domain_config = params.file['domains']  # This is the [domains] TABLE

    parent_ids = utils.to_list(domain_config['parent_id'])
    src_n_domains = len(parent_ids)

    # Validate array fields (same logic, different source)
    for f in params.geogrid_array_fields:
        if f not in domain_config:
            raise ValueError(f'The field {f} is missing from [domains] in parameters.toml.')
        v = utils.to_list(domain_config[f])
        if len(v) != src_n_domains:
            raise ValueError(f'The field {f} must have {src_n_domains} values.')
        if f in ('e_we', 'e_sn'):
            for i in v:
                if i < 100:
                    raise ValueError('Grid points must be >= 100.')

    # Validate single fields (same logic)
    for f in params.geogrid_single_fields:
        if f not in domain_config:
            raise ValueError(f'The field {f} is missing from [domains] in parameters.toml.')
        if isinstance(domain_config[f], list):
            raise ValueError(f'The field {f} must be a single value.')

    # Domain nesting validation (unchanged)
    if domains:
        domains.sort()
        for domain in domains[1:]:
            parent_id = parent_ids[domain - 1]
            if parent_id not in domains:
                raise ValueError(f'Parent {parent_id} not in domains. Parent/child must match.')
    else:
        domains = list(range(1, src_n_domains + 1))

    return src_n_domains, domains
```

**IMPORTANT — TOML key collision**: The top-level `domains = [1, 2]` (list) and `[domains]` (table) share the same key name. In TOML, a key cannot be both a value and a table. **We must rename one of them.**

Options:
- Rename the top-level run subset: `run_domains = [1, 2]`
- Rename the geometry section: `[domain_config]` or `[grid]`

**Recommendation**: Rename the geometry section to `[grid]`. It's more descriptive and avoids breaking the existing `domains` parameter.

**Updated naming convention**:
- `domains = [1, 2]` — which domains to run (unchanged, backward compatible)
- `[grid]` — domain geometry definition (new section)

This change propagates to: `params.file['grid']` in all code references.

### `set_nml_params(domains=None)` — Build from scratch

#### Helper function: `broadcast_field()`

```python
def broadcast_field(value, n_domains, domains, old_n_domains):
    """
    Handle per-domain values from TOML:
    - scalar → [scalar] * n_domains
    - array of old_n_domains → slice to selected domains
    - array of n_domains → pass through
    """
    if not isinstance(value, list):
        return [value] * n_domains
    if len(value) == old_n_domains and old_n_domains != n_domains:
        return [value[d - 1] for d in domains]
    if len(value) == n_domains:
        return value
    raise ValueError(
        f'Array has {len(value)} values, expected {old_n_domains} or {n_domains}'
    )
```

#### Main function structure

```python
def set_nml_params(domains=None):
    import defaults

    # --- Read domain geometry from TOML [grid] section ---
    grid_config = params.file['grid']
    parent_ids = utils.to_list(grid_config['parent_id'])
    old_n_domains = len(parent_ids)

    # Build geogrid dict from TOML
    geogrid = {}
    for field in defaults.GEOGRID_ARRAY_FIELDS:
        geogrid[field] = list(utils.to_list(grid_config[field]))  # copy
    for field in defaults.GEOGRID_SINGLE_FIELDS:
        geogrid[field] = grid_config[field]
    for field, default_val in defaults.GEOGRID_OPTIONAL_DEFAULTS.items():
        geogrid[field] = grid_config.get(field, default_val)
    if 'truelat1' in grid_config:
        geogrid['truelat1'] = grid_config['truelat1']
    if 'truelat2' in grid_config:
        geogrid['truelat2'] = grid_config['truelat2']

    # --- Domain subsetting (same logic as current) ---
    if domains:
        domains.sort()
        _ = utils.update_geogrid(geogrid, domains)
        n_domains = len(domains)
    else:
        domains = list(range(1, old_n_domains + 1))
        n_domains = old_n_domains

    data_path = params.data_path

    # === BUILD WPS NAMELIST ===

    wps_share = dict(defaults.WPS_SHARE_DEFAULTS)
    wps_share['max_dom'] = n_domains
    wps_share['opt_output_from_geogrid_path'] = str(data_path)

    wps_geogrid = dict(geogrid)
    wps_geogrid['geog_data_path'] = str(params.geog_data_path)
    wps_geogrid['opt_geogrid_tbl_path'] = str(
        params.geogrid_exe.parent.joinpath('geogrid')
    )

    wps_ungrib = dict(defaults.WPS_UNGRIB_DEFAULTS)

    wps_metgrid = dict(defaults.WPS_METGRID_DEFAULTS)
    wps_metgrid['fg_name'] = str(data_path.joinpath('ERA5'))
    wps_metgrid['opt_metgrid_tbl_path'] = str(
        params.metgrid_exe.parent.joinpath('metgrid')
    )
    wps_metgrid['opt_output_from_metgrid_path'] = str(data_path)

    # === BUILD WRF NAMELIST ===

    # -- time_control --
    wrf_tc = dict(defaults.WRF_TIME_CONTROL_DEFAULTS)

    # -- domains --
    wrf_dom = dict(defaults.WRF_DOMAINS_DEFAULTS)
    # Merge domain geometry from geogrid
    for k, v in geogrid.items():
        if k not in ('geog_data_path', 'opt_geogrid_tbl_path',
                      'pole_lat', 'pole_lon', 'truelat1', 'truelat2'):
            wrf_dom[k] = v
    wrf_dom['max_dom'] = n_domains

    # e_vert from TOML (scalar or array)
    e_vert = grid_config.get('e_vert', 33)
    wrf_dom['e_vert'] = broadcast_field(e_vert, n_domains, domains, old_n_domains)

    # p_top_requested from TOML (override default)
    wrf_dom['p_top_requested'] = grid_config.get(
        'p_top_requested', defaults.WRF_DOMAINS_DEFAULTS['p_top_requested']
    )

    # parent_time_step_ratio from TOML or derive from parent_grid_ratio
    ptr = grid_config.get('parent_time_step_ratio',
                          geogrid.get('parent_grid_ratio', [1] * n_domains))
    wrf_dom['parent_time_step_ratio'] = broadcast_field(
        ptr, n_domains, domains, old_n_domains
    )
    wrf_dom['parent_time_step_ratio'][0] = 1

    # grid_id: sequential
    wrf_dom['grid_id'] = list(range(1, n_domains + 1))

    # time_step: derived from dx
    wrf_dom['time_step'] = int(wrf_dom['dx'] * 0.001 * 6)

    # max_step_increase_pct: broadcast then force parent=5
    msip = broadcast_field(
        wrf_dom.get('max_step_increase_pct', 51),
        n_domains, domains, old_n_domains
    )
    msip[0] = 5
    wrf_dom['max_step_increase_pct'] = msip

    # Broadcast remaining per-domain domain fields
    for field in defaults.DOMAINS_PER_DOMAIN_FIELDS:
        if field in wrf_dom and field not in ('max_step_increase_pct',
                                                'parent_time_step_ratio', 'e_vert'):
            wrf_dom[field] = broadcast_field(
                wrf_dom[field], n_domains, domains, old_n_domains
            )

    # -- physics: merge defaults + user overrides --
    physics = dict(defaults.PHYSICS_DEFAULTS)
    if 'physics' in params.file:
        physics.update(params.file['physics'])
    for field in defaults.PHYSICS_PER_DOMAIN_FIELDS:
        if field in physics:
            physics[field] = broadcast_field(
                physics[field], n_domains, domains, old_n_domains
            )

    # -- dynamics: merge defaults + user overrides --
    dynamics = dict(defaults.DYNAMICS_DEFAULTS)
    if 'dynamics' in params.file:
        dynamics.update(params.file['dynamics'])
    for field in defaults.DYNAMICS_PER_DOMAIN_FIELDS:
        if field in dynamics:
            dynamics[field] = broadcast_field(
                dynamics[field], n_domains, domains, old_n_domains
            )

    # -- other sections --
    bdy_control = dict(defaults.WRF_BDY_CONTROL_DEFAULTS)
    diags = {}
    namelist_quilt = dict(defaults.WRF_NAMELIST_QUILT_DEFAULTS)

    # -- apply [namelist_overrides] escape hatch --
    if 'namelist_overrides' in params.file:
        section_map = {
            'time_control': wrf_tc,
            'domains': wrf_dom,
            'physics': physics,
            'dynamics': dynamics,
            'bdy_control': bdy_control,
            'diags': diags,
            'namelist_quilt': namelist_quilt,
        }
        for section_name, overrides in params.file['namelist_overrides'].items():
            if section_name in section_map:
                section_map[section_name].update(overrides)
            # For sections not in map (e.g., 'fdda'), they become new sections
            # handled below when assembling the namelist

    # ============================================================
    # TIME / OUTPUT LOGIC (transplanted from current set_nml_params)
    # ============================================================
    # This block is essentially identical to the current lines 154-273.
    # The only difference is writing to `wrf_tc` dict instead of
    # `wrf_nml['time_control']`, and `wps_share` instead of
    # `wps_nml['share']`.
    #
    # - Parse start_date, end_date from params.file['time_control']
    # - Set interval_seconds on both wps_share and wrf_tc
    # - Compute history_interval_nml, frames_per_outfile, history_begin
    # - Handle summary_file (auxhist3) and z_level_file (auxhist22)
    # - Set date arrays (start_year/month/day/hour, end_year/...)
    # - Generate output_files list
    #
    # KEY CHANGE — history_file.interval_hours is now a list instead of a dict:
    #   Old: interval_hours = {1=24, 2=1, 3=1}  (dict keyed by domain number)
    #   New: interval_hours = [24, 1, 1, 1, 1, 1]  (list, one per domain)
    #
    # Old parsing:
    #   history_intervals = {int(domain): int(hi*60) for domain, hi in history_intervals.items() if int(domain) in domains}
    #   history_interval_nml = [history_intervals[i] for i in domains]
    #
    # New parsing (consistent with all other per-domain arrays):
    #   history_intervals = [int(hi * 60) for hi in params.file['time_control']['history_file']['interval_hours']]
    #   history_interval_nml = broadcast_field(history_intervals, n_domains, domains, old_n_domains)
    # ============================================================

    # ... (transplant existing time/output logic here) ...

    # Set prec_acc_dt after history_interval is computed
    physics['prec_acc_dt'] = history_interval_nml

    # Noah-MP symlink (same as current)
    surface_physics = physics.get('sf_surface_physics', 4)
    if not isinstance(surface_physics, list):
        surface_physics = [surface_physics]
    if 4 in surface_physics:
        subprocess.run(
            'ln -sf GEOGRID.TBL.ARW.noahmp GEOGRID.TBL',
            shell=True, check=False,
            cwd=params.wps_path.joinpath('geogrid')
        )

    # ============================================================
    # ASSEMBLE AND WRITE NAMELISTS
    # ============================================================

    wps_nml = f90nml.Namelist({
        'share': wps_share,
        'geogrid': wps_geogrid,
        'ungrib': wps_ungrib,
        'metgrid': wps_metgrid,
    })

    wrf_sections = {
        'time_control': wrf_tc,
        'domains': wrf_dom,
        'physics': physics,
        'fdda': {},
        'dynamics': dynamics,
        'bdy_control': bdy_control,
        'diags': diags,
        'grib2': {},
        'namelist_quilt': namelist_quilt,
    }

    # Add any extra sections from namelist_overrides not in the default set
    if 'namelist_overrides' in params.file:
        for section_name, overrides in params.file['namelist_overrides'].items():
            if section_name not in wrf_sections:
                wrf_sections[section_name] = dict(overrides)

    wrf_nml = f90nml.Namelist(wrf_sections)

    with open(params.wps_nml_path, 'w') as f:
        wps_nml.write(f)
    with open(params.wrf_nml_path, 'w') as f:
        wrf_nml.write(f)

    return (new_start_date.naive(), end_date.naive(),
            int(interval_hours), output_files)
```

### `set_ndown_params()` — No changes needed

This function reads the **working** `wrf_nml_path` (not the source template), so it works unchanged.

---

## Step 5: Update `check_ndown.py`

Replace namelist reading with TOML reading:

```python
# Before:
wps_nml = f90nml.read(params.src_wps_nml_path)
wps_domains = wps_nml['geogrid']
parent_ids = wps_domains['parent_id']

# After:
domain_config = params.file['grid']
parent_ids = utils.to_list(domain_config['parent_id'])
```

Remove `import f90nml`.

---

## Step 6: Update `main.py`

Minimal changes. The domain parsing logic (lines 63-74) stays since it validates the top-level `domains` parameter. No structural changes needed — `check_nml_params()` and `set_nml_params()` have the same signatures and return values.

---

## Step 7: Delete template namelist files

- `git rm namelist.wps`
- `git rm namelist.input`

These are no longer needed since the code generates them from scratch.

---

## TOML Key Naming: Resolving the `domains` Collision

TOML does not allow a key to be both a bare value (`domains = [1, 2]`) and a table header (`[domains]`). We have two options:

### Option A: Rename geometry section to `[grid]` (Recommended)
- `domains = [1, 2]` — which domains to run (backward compatible)
- `[grid]` — domain geometry definition
- Pro: No breaking change on existing `domains` parameter
- Con: WRF users may not immediately recognize "grid" as domain config

### Option B: Rename run subset to `run_domains`
- `run_domains = [1, 2]` — which domains to run
- `[domains]` — domain geometry definition
- Pro: `[domains]` is the natural WRF name
- Con: Breaking change for existing users + env var name changes

**Going with Option A: `[grid]`** — backward compatible and clear enough.

---

## Verification Plan

1. **Diff test**: Before deleting templates, generate namelists with both old (template-based) and new (TOML-based) code using the same configuration. Diff the output — they should be functionally identical.

2. **Domain subsetting**: Configure 6 domains in `[grid]`, set `domains = [3, 4]`. Verify:
   - Arrays are sliced to domains 3 and 4
   - Projection is recalculated (ref_lat, ref_lon, stand_lon updated)
   - Output files renamed from d01/d02 back to d03/d04

3. **Physics override**: Add `[physics]` section with `cu_physics = [16, 16, 0, 0, 0, 0]`. Verify array is written correctly and sliced when using domain subset.

4. **Scalar broadcast**: Set `mp_physics = 8` (scalar). Verify it appears as `8, 8, 8, 8, 8, 8` in the generated namelist.

5. **ndown mode**: Configure `[ndown]` with `domains = [3]`. Verify parent chain resolution reads from `[grid]` correctly.

6. **Escape hatch**: Add `[namelist_overrides.physics]` with `topo_wind = [0, 1, 1, 2, 2, 2]`. Verify it appears in the generated `&physics` section.

7. **Defaults only**: Remove `[physics]` and `[dynamics]` sections entirely. Verify the generated namelist uses all default values.

8. **End-to-end**: Run full pipeline in Docker with the new configuration format.

---

## Summary of User Experience

### Before (3 files)
```
parameters.toml     — time, output, remote storage, runtime
namelist.wps        — domain geometry, projection (Fortran syntax)
namelist.input      — physics, dynamics, domain config (Fortran syntax, mostly duplicated)
```

### After (1 file)
```
parameters.toml     — everything
  [time_control]    — timing and output (unchanged)
  [grid]            — domain geometry (was in namelist.wps)
  [physics]         — optional overrides (was in namelist.input)
  [dynamics]        — optional overrides (was in namelist.input)
  [namelist_overrides.*] — escape hatch for any WRF namelist parameter
  [remote]          — data storage (unchanged)
```

A minimal configuration only requires: `[time_control]`, `[grid]`, and `[remote.era5]`. Physics and dynamics use sensible defaults. The user never needs to understand Fortran namelist syntax.
