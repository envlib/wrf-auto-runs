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

if 'duration_hours' in os.environ:
    file['time_control']['duration_hours'] = int(os.environ['duration_hours'])

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

