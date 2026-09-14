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
    'num_metgrid_levels': 38,          # Placeholder -- auto-detected from met_em files before real.exe
    'num_metgrid_soil_levels': 4,      # Placeholder -- auto-detected from met_em files before real.exe
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
    # moist_adv_opt: 4 (WENO positive-definite), NOT WRF's default 1. The WVT tags are advected with
    # tracer_adv_opt = 4 (hard-required by check_a_mundo), and base moisture on a DIFFERENT limiter
    # makes sum_n tr_qX overshoot qX at every sharp cloud edge; WSM6's entry caps then discard the
    # excess. Measured 2026-09-13 at 12 regions: 15% of tagged precipitation returned to vapour and
    # 10% of NZ-land rain untagged with 1, against 0.2% and 1.7% with 4 (wrf-model-eval
    # docs/wvt_wsm6_tagging.md). check_a_mundo refuses 1 with tracer_opt = 4 from image 2.4 on.
    # Every tagged archive produced before 2026-09-13 carries the mismatch.
    'moist_adv_opt': 4,          # WENO positive-definite moisture advection -- must match tracer_adv_opt
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
    # Moisture nudging disabled. Even small gq (~1e-5) drifts the WVT qv_tr/qv
    # ratio over multi-month runs because nudging acts on qv but not on the
    # tracer 4D array. T/wind nudging indirectly constrains moisture transport.
    'gq': 0,                     # Nudging coefficient for moisture
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

# WVT (multi-region) 3D named-member families -- the only per-region WVT fields
# stored as SEPARATE variables per region (region 1 = the unsuffixed base name,
# regions 2..N = base_02..base_0N). The 2D accumulators (TR_RAINNC etc.) instead
# carry a wvt_regions axis on a single variable, so they need no expansion. Matched
# case-insensitively (species are lower-case in wrfout, tr_thum upper-case). When
# output_variables filtering is active and a base name is requested it is auto-
# expanded to all active regions (see utils.resolve_output_variables). All are 3D.
WVT_TRACER_FAMILIES = {
    'qv_tr', 'qc_tr', 'qr_tr', 'qi_tr', 'qs_tr', 'qg_tr',
    'tr_thum_u_phy_dt', 'tr_thum_v_phy_dt',
    # Per-region cumulus vapour tendency. Paired with the base RQVCUTEN it makes the
    # identity sum_n RTRQVCUTEN_n == RQVCUTEN checkable on real output -- the gate that
    # catches a broken tag mirror in the convection scheme.
    'rtrqvcuten',
}

# Named presets: each maps to a set of WRF output variables required by a
# specific downstream tool.  Users select presets via ``output_presets`` in
# parameters.toml; the variables are merged with any explicit
# ``output_variables`` list before ncks filtering is applied.
OUTPUT_PRESETS = {
    'wrf_to_int': {
        # 3D atmospheric (eta-level)
        'T', 'U', 'V', 'P', 'PB', 'PH', 'PHB', 'QVAPOR',
        # Surface (always required by wrf_to_int)
        'PSFC', 'T2', 'HGT', 'TSK', 'U10', 'V10', 'Q2', 'XLAND',
        # Wind rotation (optional in wrf_to_int, but must be present to rotate)
        'COSALPHA', 'SINALPHA',
        # Optional surface fields
        'SEAICE', 'SST', 'SNOW', 'SNOWH',
        # Soil fields
        'DZS', 'SMOIS', 'TSLB',
    },

    # --------------------------------------------------------------------
    # Water-vapour-tracer (WVT) campaigns. Two presets, ADDITIVE not nested:
    #   output_presets = ['wvt_2d']            -> long campaigns (C1: 45 yr)
    #   output_presets = ['wvt_2d', 'wvt_3d']  -> short runs (case studies, S1 events)
    # They union (see params.py), so 'wvt_3d' holds ONLY the 3D additions and the
    # 2D list is never duplicated.
    #
    # Sizing measured on a 12 km d01, 8 regions, 50 levels (2026-08-26): a full
    # unfiltered wrfout day is 4.46 GB, of which 91% is 4D model-level data.
    # 'wvt_2d' alone is ~0.41 GB/day (~6.7 TB over 45 yr); adding 'wvt_3d' takes it
    # to ~0.79 GB/day (~12.9 TB). Full 3D retention for C1 is not feasible.
    # --------------------------------------------------------------------
    'wvt_2d': {
        # -- Total precipitation. The I_* bucket counters are NOT optional: cfdb-ingest
        #    reconstructs the accumulation as var + BUCKET_MM * I_var, so omitting a
        #    counter silently corrupts totals every time the bucket ticks.
        'RAINNC', 'RAINC', 'I_RAINNC', 'I_RAINC',
        # -- Per-interval precipitation, windowed by prec_acc_dt (= each domain's history interval,
        #    set_params.py). Seam-robust where the running totals are not: RAINC/RAINNC restart
        #    from zero at every cold start, so an archive stitched from independent runs loses one
        #    interval per seam. Totals still come from RAINC+RAINNC on a single run (the PREC_ACC
        #    pair is corrupted on a two-way-nested d01 by d02's reset cadence -- feedback=0 here).
        'PREC_ACC_C', 'PREC_ACC_NC',
        # -- Tagged precipitation, per region on a single wvt_regions axis (no expansion
        #    needed -- these are one variable each, unlike the 3D named members).
        'TR_RAINNC', 'TR_RAINC', 'I_TR_RAINNC', 'I_TR_RAINC',
        # -- Tag mass the one-sided caps create/destroy, per region (kg m-2). The New
        #    Tiedtke mirrors leave a structural residual that redistributes composition
        #    BETWEEN regions while conserving the total, so it is invisible to any
        #    conservation check (docs/wvt_cumulus_tagging.md in wrf-model-eval). These
        #    make it a reported quantity per run rather than an assumed-small one.
        #    The I_* counters are as non-optional as the precipitation ones above.
        'TR_CAPCRE', 'TR_CAPDES', 'I_TR_CAPCRE', 'I_TR_CAPDES',
        # -- The MICROPHYSICS equivalents. THREE fields, not two, because WSM6's six caps are of
        #    three kinds and summing them is meaningless: TR_MPRED moves tag mass condensate ->
        #    vapour (total conserved), TR_MPDES discards it, TR_MPCRE invents it at the
        #    positivity floors after sedimentation. TR_MPRED is the leading candidate for tagged
        #    PRECIPITATION being depleted ~2x harder than tagged column water.
        #    Like the cumulus pair, the I_* counters are non-optional: cfdb-ingest probes for
        #    I_<var> in the FIRST file only and silently drops the bucket term if absent.
        'TR_MPCRE', 'TR_MPDES', 'TR_MPRED',
        'I_TR_MPCRE', 'I_TR_MPDES', 'I_TR_MPRED',
        # -- Region mask and the remaining tagged accumulators. TRMASK is not optional: every
        #    per-region field is uninterpretable without knowing which cells each region owns,
        #    and the CS1 gate scripts open it directly. TR_SNOWNC/TR_GRAUPELNC complete the
        #    per-region precipitation bound (TR_SNOWNC is unbucketed, so it needs no counter).
        'TRMASK', 'TR_SNOWNC', 'TR_GRAUPELNC',
        # -- Column diagnostics computed in-model at history-write time
        #    (registry.diag_columns / module_diag_wvt_columns.F). These exist precisely
        #    so the 3D fields need not be retained; formulas match cfdb-ingest.
        'PWAT', 'PWAT_TR', 'VIMF_U', 'VIMF_V', 'VIMF_TR_U', 'VIMF_TR_V', 'IVT',
        # -- Storm identification / cataloguing (MSLP-based tracking, IVT above for ARs)
        'SLP',
        # -- Sea-surface temperature: the FORCING, archived so the output can describe itself.
        #    Added 2026-09-14. No wrfout GLOBAL ATTRIBUTE records which SST product drove a run, so
        #    without this field two archives of different experiments are indistinguishable from
        #    their own contents: a Stage-0 gate comparing all 167 attributes of `cs1_v50_fix8`
        #    against `cs1_v50_era5_fix8` -- a CCI-vs-ERA5 pair that moves NZ-land precipitation by
        #    -7% -- finds NO difference at all. 2-D, so the cost is negligible against 45 years.
        'SST',
        # -- Static fields. HGT is 2D but is only auto-added alongside the 3D coordinate
        #    set, so a 2D-only preset must name it explicitly; LANDMASK is never
        #    auto-added and the NZ landmask derivation depends on it.
        'HGT', 'LANDMASK', 'XLAND',
        # -- Earth-relative wind rotation
        'COSALPHA', 'SINALPHA',
        # -- Surface fields for station validation of the campaign itself
        'T2', 'Q2', 'PSFC', 'U10', 'V10', 'SWDOWN',
    },

    'wvt_3d': {
        # Vertical structure of tagged vs total water vapour. ONLY the base name is
        # listed: resolve_output_variables expands qv_tr -> qv_tr, qv_tr_02 .. qv_tr_0N
        # from the live num_wvt_regions, so this stays correct as the region count
        # changes. P/PB/PH/PHB/HGT are pulled in automatically once any 3D member is
        # present -- do not list them here, and note that this is exactly why the 2D
        # preset must stay free of 3D variables.
        #
        # Deliberately vapour only. The other five tracer species (qc_tr, qr_tr, qi_tr,
        # qs_tr, qg_tr) each add another N-region 3D family -- a ~6x multiplier on the
        # largest term in the archive. Add them per-run when a specific question needs
        # condensate, not by default.
        'qv_tr', 'QVAPOR',
        # -- Per-region cumulus vapour tendency plus its base counterpart, so that
        #    sum_n RTRQVCUTEN_n == RQVCUTEN is checkable on archived output. This is
        #    the identity that detects a broken tag mirror in the convection scheme;
        #    without it the check only exists in a single-column harness.
        #    wvt_3d is a SHORT-RUN preset, so this costs C1 nothing.
        'RTRQVCUTEN', 'RQVCUTEN',
    },
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
    'radt', 'bldt', 'cudt', 'sf_urban_physics', 'prec_acc_dt',
}

# WVT dynamics switches: per-domain (must be broadcast to all domains to prevent tracer
# array size mismatch during nested boundary forcing -- d01 tracer array must match d02),
# but OPTIONAL -- only emitted when tracer_opt=4 is configured, so absent from a non-WVT
# namelist (WRF defaults tracer_opt=0). Named separately so callers/tests can tell them apart.
WVT_DYNAMICS_PER_DOMAIN_FIELDS = {
    'tracer_opt', 'tracer_adv_opt', 'tracer2dsource', 'tracer3dsource', 'tracer3dsink',
}

# Per-domain fields in &dynamics that need broadcasting (always-on defaults + the optional WVT switches)
DYNAMICS_PER_DOMAIN_FIELDS = {
    'diff_opt', 'km_opt', 'diff_6th_opt', 'diff_6th_factor',
    'zdamp', 'dampcoef', 'khdif', 'kvdif',
    'non_hydrostatic', 'moist_adv_opt', 'scalar_adv_opt',
    'gwd_opt', 'epssm',
} | WVT_DYNAMICS_PER_DOMAIN_FIELDS

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
