#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 10:40:23 2025

@author: mike
"""
import os
import pathlib
import shlex
import subprocess
import pendulum
import shutil
import copy
import f90nml

import params

############################################
### Parameters


###########################################
### Functions


def run_ndown(run_uuid, del_old=True):
    """

    """
    # new_top_domain = params.file['ndown']['new_top_domain']

    ## Prep files
    os.rename(params.run_path.joinpath('wrfinput_d02'), params.run_path.joinpath('wrfndi_d02'))

    # cmd_str = 'ln -sf ../wrfout_* .'
    # p = subprocess.run(cmd_str, shell=True, capture_output=False, text=False, check=False, cwd=params.run_path)

    wrf_nml = f90nml.read(params.wrf_nml_path)
    wrf_nml['time_control']['io_form_auxinput2'] = 2
    wrf_nml['time_control']['fine_input_stream'] = [0, 2] # Is this needed?
    ndown_interval = wrf_nml['time_control']['history_interval'][0] * 60
    wrf_nml['time_control']['interval_seconds'] = ndown_interval

    with open(params.wrf_nml_path, 'w') as nml_file:
       wrf_nml.write(nml_file)

    cmd_str = f'mpirun -n 4 --map-by core {params.ndown_exe}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=False, text=False, check=False, cwd=params.run_path)

    real_log_path = params.run_path.joinpath('rsl.out.0000')
    with open(real_log_path, 'rt') as f:
        f.seek(0, os.SEEK_END)
        f.seek(f.tell() - 40, os.SEEK_SET)
        results_str = f.read()

    if 'SUCCESS COMPLETE NDOWN_EM INIT' in results_str:
        if del_old:
            for path in params.run_path.glob('wrfout_*.nc'):
                path.unlink()

            params.run_path.joinpath('wrfndi_d02').unlink()

        for file_path in params.run_path.glob('*_d01'):
            file_path.unlink()

        for file_path in params.run_path.glob('*_d02'):
            file_part = file_path.name.split('_')[0]
            new_file = file_part + '_d01'
            new_file_path = params.run_path.joinpath(new_file)
            os.rename(file_path, new_file_path)

        return ndown_interval
    else:
        # scope = sentry_sdk.get_current_scope()
        # scope.add_attachment(path=real_log_path)

        if params.is_remote_output:
            remote = copy.deepcopy(params.file['remote']['output'])

            name = 'output'

            if 'path' in remote:
                out_path = pathlib.Path(remote.pop('path'))
            else:
                out_path = None

            if out_path is not None:
                print(f'-- Uploading ndown.exe log files for run uuid: {run_uuid}')
                dest_str = f'{name}:{out_path}/logs/{run_uuid}/'
                cmd_str = f'rclone copy {params.run_path} {dest_str} --config={params.config_path} --include "rsl.*" --transfers=8'
                cmd_list = shlex.split(cmd_str)
                p = subprocess.run(cmd_list, capture_output=True, text=True, check=True)

        raise ValueError(f'ndown.exe failed. Look at the logs for details: {results_str}')





