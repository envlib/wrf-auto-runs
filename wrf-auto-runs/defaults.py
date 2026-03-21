#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Default namelist values for WPS and WRF.

Hardcoded values are fixed by the project scope (ERA5 input, Docker container, ARW core).
Physics and dynamics defaults are sensible starting points that users can override
via [physics] and [dynamics] sections in parameters.toml.
"""

# ============================================================
# WPS Namelist Defaults (all hardcoded -- not user-configurable)
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
# WRF Namelist Defaults -- Hardcoded sections
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
    'max_step_increase_pct': 51,
    'starting_time_step': -1,
    'max_time_step': -1,
    'min_time_step': -1,
    'adaptation_domain': 1,
    'time_step_fract_num': 0,
    'time_step_fract_den': 1,
    'p_top_requested': 5000,
    'num_metgrid_levels': 38,
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
# WRF Physics Defaults -- User can override via [physics] in TOML
# ============================================================

PHYSICS_DEFAULTS = {
    'mp_physics': 6,             # WSM6 (WRF Single-Moment 6-class)
    'cu_physics': 16,            # New Tiedtke
    'ra_lw_physics': 4,          # RRTMG longwave
    'ra_sw_physics': 4,          # RRTMG shortwave
    'bl_pbl_physics': 0,         # No PBL scheme (use with km_opt = 5 for SMS-3DTKE)
    'sf_sfclay_physics': 1,      # Revised MM5 Monin-Obukhov
    'sf_surface_physics': 4,     # Noah-MP
    'sf_ocean_physics': 0,       # No ocean model (SST from input)
    'radt': 30,                  # Radiation calling interval (minutes)
    'bldt': 0,                   # PBL calling interval (0 = every time step)
    'cudt': 5,                   # Cumulus calling interval (minutes)
    'icloud': 1,                 # Cloud fraction method for radiation
    'num_land_cat': 21,          # Land-use categories (MODIS)
    'sf_urban_physics': 0,       # No urban model
    'sst_update': 1,             # Update SST from wrflowinp
    'usemonalb': True,           # Use monthly albedo from geogrid
    'sst_skin': 0,               # No skin SST calculation
}

# ============================================================
# WRF Dynamics Defaults -- User can override via [dynamics] in TOML
# ============================================================

DYNAMICS_DEFAULTS = {
    'hybrid_opt': 2,             # Hybrid vertical coordinate
    'w_damping': 1,              # Vertical velocity damping on
    'diff_opt': 2,               # Full diffusion (metric terms on slopes)
    'km_opt': 5,                 # SMS-3DTKE (scale-adaptive 3D TKE, since V4.2)
    'diff_6th_opt': 0,           # 6th-order horizontal diffusion off
    'diff_6th_factor': 0.12,     # 6th-order diffusion scaling factor
    'base_temp': 290.0,          # Base state temperature (K)
    'damp_opt': 3,               # Implicit gravity-wave damping layer
    'zdamp': 5000.0,             # Damping depth from model top (m)
    'dampcoef': 0.2,             # Damping coefficient
    'khdif': 0,                  # Horizontal diffusion constant (m^2/s)
    'kvdif': 0,                  # Vertical diffusion constant (m^2/s)
    'non_hydrostatic': True,     # Non-hydrostatic mode
    'moist_adv_opt': 1,          # Positive-definite moisture advection
    'scalar_adv_opt': 1,         # Positive-definite scalar advection
    'gwd_opt': 1,                # Gravity wave drag
    'epssm': 0.5,                # Time off-centering for sound waves
}

# ============================================================
# WRF FDDA Defaults -- Applied per-domain where grid_fdda > 0
# ============================================================

# Per-domain defaults (masked by grid_fdda: non-zero only where nudging is on)
FDDA_PER_DOMAIN_DEFAULTS = {
    'gfdda_end_h': 0,            # Populated at runtime from simulation duration
    'gfdda_interval_m': 0,       # Populated at runtime from interval_hours
    'fgdt': 0,                   # Apply nudging every time step
    'if_no_pbl_nudging_uv': 1,   # Don't nudge wind in PBL
    'if_no_pbl_nudging_t': 1,    # Don't nudge temperature in PBL
    'if_no_pbl_nudging_ph': 1,   # Don't nudge geopotential in PBL
    'if_no_pbl_nudging_q': 1,    # Don't nudge moisture in PBL
    'guv': 0.0003,               # Nudging coefficient for wind (3e-4 s^-1)
    'gt': 0.0003,                # Nudging coefficient for temperature
    'gq': 0.00001,               # Nudging coefficient for moisture
}

# Per-domain fields in &fdda that need broadcasting/masking by grid_fdda
FDDA_PER_DOMAIN_FIELDS = {
    'grid_fdda', 'gfdda_end_h', 'gfdda_interval_m', 'fgdt',
    'if_no_pbl_nudging_uv', 'if_no_pbl_nudging_t',
    'if_no_pbl_nudging_ph', 'if_no_pbl_nudging_q',
    'guv', 'gt', 'gq',
}

# ============================================================
# Output Variable Filtering
# ============================================================

# Variables auto-included when output_variables filtering is active
COORD_VARS_2D = {'Times', 'XLAT', 'XLONG', 'XTIME'}

COORD_VARS_3D = {'P', 'PB', 'PH', 'PHB', 'HGT'}

# WRF variables with a vertical (eta-level) dimension
VARS_3D = {
    'T', 'U', 'V', 'W',
    'P', 'PB', 'PH', 'PHB',
    'QVAPOR', 'QCLOUD', 'QRAIN', 'QICE', 'QSNOW', 'QGRAUP', 'QHAIL',
    'TKE_PBL',
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

# Fields from geogrid that should be copied into WRF &domains
WRF_DOMAIN_GEOGRID_FIELDS = {
    'parent_id', 'parent_grid_ratio', 'i_parent_start',
    'j_parent_start', 'e_we', 'e_sn', 'dx', 'dy',
}

# Per-domain fields in &domains that need broadcasting (scalar -> array)
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

# ============================================================
# Pipeline Key Sets
# ============================================================

# Keys in [domains] consumed by the pipeline (not passed to WRF &domains)
DOMAINS_PIPELINE_KEYS = (
    {'run', 'truelat1', 'truelat2', 'e_vert', 'p_top_requested', 'parent_time_step_ratio'}
    | set(GEOGRID_ARRAY_FIELDS)
    | set(GEOGRID_SINGLE_FIELDS)
    | set(GEOGRID_OPTIONAL_DEFAULTS)
)

# Keys in [time_control] consumed by the pipeline (not passed to WRF &time_control)
TIME_CONTROL_PIPELINE_KEYS = {
    'start_date', 'end_date', 'duration_hours', 'interval_hours',
    'history_file', 'summary_file', 'z_level_file',
}
