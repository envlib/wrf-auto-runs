#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep 23 15:03:38 2025

@author: mike
"""
import tomllib
import os
import pathlib

from defaults import GEOGRID_ARRAY_FIELDS as geogrid_array_fields
from defaults import GEOGRID_SINGLE_FIELDS as geogrid_single_fields
from defaults import OUTPUT_PRESETS

############################################
### Read params file

base_path = pathlib.Path(os.path.realpath(os.path.dirname(__file__)))

with open(base_path.joinpath("parameters.toml"), "rb") as f:
    file = tomllib.load(f)

if 'no_docker' in file:
    no_docker = file['no_docker']
    data_path = pathlib.Path(no_docker['data_path'])
    geog_data_path = pathlib.Path(no_docker['geog_data_path'])
    wrf_path = pathlib.Path(no_docker['wrf_path'])
    wps_path = pathlib.Path(no_docker['wps_path'])
else:
    no_docker = None
    data_path = pathlib.Path('/data')
    geog_data_path = pathlib.Path('/WPS_GEOG')
    wrf_path = pathlib.Path('/WRF')
    wps_path = pathlib.Path('/WPS')

if 'start_date' in os.environ:
    file['time_control']['start_date'] = os.environ['start_date']

if 'end_date' in os.environ:
    file['time_control']['end_date'] = os.environ['end_date']

if 'domains' in os.environ:
    domains = os.environ['domains']
    if isinstance(domains, int):
        domains = [domains]
    elif isinstance(domains, str):
        domains = [int(s) for s in domains.split(',')]
    else:
        raise ValueError('domains env variable should be either an int or a string of ints with commas separating them.')

    file.setdefault('domains', {})['run'] = domains

if 'n_cores' in os.environ:
    file['n_cores'] = int(os.environ['n_cores'])

if 'n_cores_preprocess' in os.environ:
    file['n_cores_preprocess'] = int(os.environ['n_cores_preprocess'])

if 'n_cores_metgrid' in os.environ:
    file['n_cores_metgrid'] = int(os.environ['n_cores_metgrid'])

if 'duration_hours' in os.environ:
    file['time_control']['duration_hours'] = int(os.environ['duration_hours'])

if 'preprocess_only' in os.environ:
    file['preprocess_only'] = os.environ['preprocess_only'].lower() in ('true', '1', 'yes')

if 'cleanup_inputs' in os.environ:
    file['cleanup_inputs'] = os.environ['cleanup_inputs'].lower() in ('true', '1', 'yes')

if 'restart_enable' in os.environ:
    file.setdefault('restart', {})['enable'] = os.environ['restart_enable'].lower() in ('true', '1', 'yes')

if 'restart_interval_days' in os.environ:
    file.setdefault('restart', {})['interval_days'] = int(os.environ['restart_interval_days'])

if 'restart_stop_after_upload' in os.environ:
    file.setdefault('restart', {})['stop_after_upload'] = os.environ['restart_stop_after_upload'].lower() in ('true', '1', 'yes')

## Resolve output presets + user variables into a single list
_preset_vars = set()
if 'output_presets' in file:
    _raw_presets = file['output_presets']
    if isinstance(_raw_presets, str):
        _raw_presets = [_raw_presets]
    for _p in _raw_presets:
        if _p not in OUTPUT_PRESETS:
            raise ValueError(f"Unknown output preset: '{_p}'. Available presets: {sorted(OUTPUT_PRESETS.keys())}")
        _preset_vars.update(OUTPUT_PRESETS[_p])

_user_vars = set(file['output_variables']) if 'output_variables' in file else set()

_combined = _preset_vars | _user_vars
output_variables = sorted(_combined) if _combined else None

run_path = data_path.joinpath('run')

is_sentry = 'sentry' in file
is_remote_output = 'remote' in file and 'output' in file.get('remote', {})
is_wrf_input = 'remote' in file and 'wrf' in file.get('remote', {})

preprocess_only = file.get('preprocess_only', False)
cleanup_inputs = file.get('cleanup_inputs', True)
n_cores_preprocess = int(file.get('n_cores_preprocess', 4))
# metgrid.exe is I/O-bound and scales poorly; high MPI rank counts amplify an intermittent
# SIGSEGV (over-decomposition / ASLR-sensitive out-of-bounds — "fails then passes on rerun").
# Decoupled from n_cores_preprocess (which still drives real.exe / ndown.exe). Defaults to a
# safe low value (4) regardless of n_cores_preprocess, so existing configs get stable metgrid
# without changes; raise it (up to ~8) only if you specifically need faster metgrid.
n_cores_metgrid = int(file.get('n_cores_metgrid', 4))

# [restart] section — config for chunked WRF runs.
_restart_cfg = file.get('restart', {})
restart_enable = bool(_restart_cfg.get('enable', False))
restart_interval_days = _restart_cfg.get('interval_days')
restart_stop_after_upload = bool(_restart_cfg.get('stop_after_upload', False))

if restart_enable and restart_interval_days is None:
    raise ValueError('[restart].interval_days is required when [restart].enable = true')
if restart_stop_after_upload and not restart_enable:
    raise ValueError('[restart].stop_after_upload requires [restart].enable = true')
if restart_interval_days is not None:
    restart_interval_days = int(restart_interval_days)

# Capture original begin_hours before any chunk-driven mutation. Used by run_chunked_pipeline
# to compute per-chunk remaining spin-up. _chunked_mode_active is flipped by set_chunk_dates
# and gates the start_date pull-back in set_params.set_nml_params (which only applies in
# single-stage / preprocess-only modes — chunked mode handles spin-up via chunk windows).
_original_begin_hours = int(file['time_control']['history_file']['begin_hours'])
_chunked_mode_active = False


def set_chunk_dates(chunk_start, chunk_end, remaining_begin_hours):
    """Override start/end/begin_hours in params.file for the current chunk window.

    Used by the unified per-chunk pipeline. The next set_nml_params() call picks up
    these values; downstream functions (dl_era5, run_metgrid, etc.) read the return
    values, so a single set_nml_params call after this is sufficient.

    remaining_begin_hours: spin-up hours still pending at chunk_start, computed by
    main.py as max(0, _original_begin_hours - elapsed_hours_since_real_sim_start).
    Becomes the namelist's history_begin_h_<n>, suppressing wrfout for chunks that
    fall inside the spin-up window.
    """
    global _chunked_mode_active
    file['time_control']['start_date'] = chunk_start.strftime('%Y-%m-%d %H:%M:%S')
    file['time_control']['end_date'] = chunk_end.strftime('%Y-%m-%d %H:%M:%S')
    file['time_control']['history_file']['begin_hours'] = int(remaining_begin_hours)
    # Clear duration_hours if set, so end_date takes precedence in set_nml_params.
    file['time_control'].pop('duration_hours', None)
    _chunked_mode_active = True

sst_source = file.get('sst', {}).get('source', 'era5')
if sst_source not in ('era5', 'cci'):
    raise ValueError(f"[sst].source must be 'era5' or 'cci', got {sst_source!r}")
if sst_source == 'cci' and 'sst' not in file.get('remote', {}):
    raise ValueError("[sst].source = 'cci' requires a [remote.sst] section pointing at the CCI SST mirror.")

if not data_path.exists():
    data_path.mkdir(exist_ok=True)


##############################################
### Assign executables

wrf_exe = wrf_path.joinpath('main/wrf.exe')
real_exe = wrf_path.joinpath('main/real.exe')
ndown_exe = wrf_path.joinpath('main/ndown.exe')
geogrid_exe = wps_path.joinpath('geogrid.exe')
metgrid_exe = wps_path.joinpath('metgrid.exe')


###########################################
### WPS

wps_nml_path = data_path.joinpath('namelist.wps')

wps_date_format = '%Y-%m-%d_%H:%M:%S'

outfile_format = '{prefix}_d{domain:02}_{date}.nc'

########################################
### WRF

wrf_nml_path = data_path.joinpath('namelist.input')

history_outname = "wrfout_d<domain>_<date>.nc"
summ_outname = "wrfxtrm_d<domain>_<date>.nc"
zlevel_outname = 'wrfzlevels_d<domain>_<date>.nc'

# wrf_nml_one_first_fields = ('parent_time_step_ratio',)

###########################################
### Others

config_path = data_path.joinpath('rclone.config')

wrf_sphere_radius = 6370000

