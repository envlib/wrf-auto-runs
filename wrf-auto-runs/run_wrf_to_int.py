#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar 22 2026

@author: mike
"""
import shlex
import subprocess
import shutil

import params


############################################
### Parameters


###########################################
### Functions


def run_wrf_to_int(start_date, end_date, hour_interval, del_old=True):
    """
    Convert wrfout files to WPS intermediate format using wrf_to_int.
    """
    wrfout_path = params.data_path.joinpath('wrfout')
    domain = params.file['remote']['wrf']['domain']

    cmd_str = f'wrf_to_int {wrfout_path} -s "{start_date}" -e "{end_date}" -h {hour_interval} -d {domain}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False, cwd=params.data_path)

    if len(p.stderr) > 0:
        raise ValueError(p.stderr)
    else:
        if del_old:
            shutil.rmtree(wrfout_path)
        return True
