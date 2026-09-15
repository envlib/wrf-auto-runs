#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 15:41:48 2025

@author: mike
"""
import os
import pathlib
import resource
import shlex
import subprocess
import copy
import sentry_sdk
from time import sleep

import params
import utils

############################################
### Parameters



# out_files_glob = {'wrfout': 'wrfout_d*',
#                   'zlevel': 'wrfzlevels_d*',
#                   'summ': 'wrfxtrm_d*',
#                   }

###########################################
### Functions


def monitor_wrf(outputs, end_date, run_uuid, rename_dict):
    """

    """
    if params.is_remote_output:
        remote = copy.deepcopy(params.file['remote']['output'])

        name = 'output'

        if 'path' in remote:
            out_path = pathlib.Path(remote.pop('path'))
        else:
            out_path = None
    else:
        out_path = None
        name = None

    # output_globs = [out_files_glob[op] for op in outputs]

    run_path = params.run_path

    n_cores = params.file['n_cores']

    resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    cmd_str = f'mpirun -np {n_cores} ./wrf.exe'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.Popen(cmd_list, cwd=run_path)

    check = p.poll()
    while check is None:
        files = utils.query_out_files(run_path, outputs)

        files = utils.select_files_to_ul(files, 1)

        if files and out_path is not None:
            if params.output_variables:
                print('- wrfout variables will be filtered based on the output_variables.')
                utils.filter_variables(files, params.output_variables)
            files = utils.rename_files(files, rename_dict)
            utils.ul_output_files(files, run_path, name, out_path, params.config_path)

        sleep(60)
        check = p.poll()

    wrf_log_path = run_path.joinpath('rsl.out.0000')
    results_str = utils.read_last_line(wrf_log_path)

    if 'SUCCESS COMPLETE WRF' in results_str:
        files = utils.query_out_files(run_path, outputs, True)

        # if end_date.hour == 0:
        #     files = utils.select_files_to_ul(files, 1)
        # else:
        #     files = utils.select_files_to_ul(files, 0)

        files = utils.select_files_to_ul(files, 0)

        if files and out_path is not None:
            if params.output_variables:
                print('- wrfout variables will be filtered based on the output_variables.')
                utils.filter_variables(files, params.output_variables)
            files = utils.rename_files(files, rename_dict)
            utils.ul_output_files(files, run_path, name, out_path, params.config_path)

        return True
    else:
        cmd_str = 'grep cfl rsl.error*'
        cmd_list = shlex.split(cmd_str)
        pe = subprocess.run(cmd_list, capture_output=True, text=True, cwd=run_path)
        if pe.stdout != '':
            results_str = pe.stdout
        # scope = sentry_sdk.get_current_scope()
        # scope.add_attachment(path=wrf_log_path)
        if out_path is not None:
            print(f'-- Uploading WRF log files for run uuid: {run_uuid}')
            dest_str = f'{name}:{out_path}/logs/{run_uuid}/'
            cmd_str = f'rclone copy {params.run_path} {dest_str} --no-check-dest --config={params.config_path} --include "rsl.*" --transfers=8'
            cmd_list = shlex.split(cmd_str)
            p = subprocess.run(cmd_list, capture_output=True, text=True, check=True)

        raise ValueError(f'wrf.exe failed. Look at the logs for details: {results_str}')















































