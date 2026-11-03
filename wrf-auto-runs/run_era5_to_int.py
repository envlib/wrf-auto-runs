#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 10:40:23 2025

@author: mike
"""
import pathlib
import shlex
import subprocess
import pendulum
import shutil

import params
import utils



############################################
### Parameters


###########################################
### Functions


def run_era5_to_int(start_date, end_date, hour_interval, del_old=True):
    """

    """
    era5_path = params.data_path.joinpath('era5')

    cmd_str = f'era5_to_int -h {hour_interval} {era5_path} "{start_date}" "{end_date}"'
    if params.sst_source == 'cci':
        cmd_str += ' --skip-vars SST,SEAICE'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False, cwd=params.data_path)
    # By return code: era5_to_int < 0.5.0 reported a missing input file on stdout and exited 1, which a stderr check
    # read as success, so metgrid ran on a truncated set (the C1 metgrid failures, 2026-11).
    utils.check_returncode(p, 'era5_to_int')

    if del_old:
        shutil.rmtree(era5_path)
    return True




