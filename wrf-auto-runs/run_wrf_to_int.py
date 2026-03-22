#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar 22 2026

@author: mike
"""
import shlex
import subprocess
import shutil

import numpy as np
import h5netcdf

import params


############################################
### Parameters


###########################################
### Functions


def _compute_pressure_levels(wrfout_path):
    """
    Generate log-spaced pressure levels matching the source wrfout vertical resolution.

    Reads the number of eta levels and P_TOP from the first wrfout file, then
    produces the same number of integer pressure levels evenly spaced in
    log-pressure from 1000 hPa down to P_TOP.
    """
    wrfout_files = sorted(wrfout_path.glob('wrfout_*.nc'))
    if not wrfout_files:
        raise FileNotFoundError(f'No wrfout files found in {wrfout_path}')

    with h5netcdf.File(str(wrfout_files[0]), 'r') as nc:
        n_eta = nc.dimensions['bottom_top'].size
        p_top_hpa = float(np.asarray(nc['P_TOP'][0])) / 100.0

    log_levels = np.linspace(np.log(1000), np.log(max(p_top_hpa, 1)), n_eta)
    levels_hpa = sorted(set(np.round(np.exp(log_levels)).astype(int)), reverse=True)

    return ','.join(str(l) for l in levels_hpa)


def run_wrf_to_int(start_date, end_date, hour_interval, del_old=True):
    """
    Convert wrfout files to WPS intermediate format using wrf_to_int.
    """
    wrfout_path = params.data_path.joinpath('wrfout')
    domain = params.file['remote']['wrf']['domain']

    pressure_levels = _compute_pressure_levels(wrfout_path)

    cmd_str = f'wrf_to_int {wrfout_path} -s "{start_date}" -e "{end_date}" -h {hour_interval} -d {domain} -l {pressure_levels}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False, cwd=params.data_path)

    if len(p.stderr) > 0:
        raise ValueError(p.stderr)
    else:
        if del_old:
            shutil.rmtree(wrfout_path)
        return True
