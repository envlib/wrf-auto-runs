#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 15:41:48 2025

@author: mike
"""
import os
import pathlib
import shlex
import subprocess
import pendulum
import copy
import sentry_sdk
from time import sleep

import params
import utils

############################################
### Parameters



out_files_glob = ('wrfout_*',)


###########################################
### Functions


def upload_wrfout():
    """

    """
    remote = copy.deepcopy(params.file['remote']['output'])

    out_path = pathlib.Path(remote.pop('path'))

    name = 'output'
    config_path = params.create_rclone_config(name, params.data_path, remote)

    run_path = params.data_path.joinpath('run')

    wrf_log_path = run_path.joinpath('rsl.out.0000')
    results_str = utils.read_last_line(wrf_log_path)

    if 'SUCCESS COMPLETE WRF' in results_str:
        out_files = utils.query_out_files(run_path)

        files = utils.select_files_to_dl(out_files, 0)

        if files:
            files_str = ', '.join([p.split('/')[-1] for p in files])
            print(f'-- Uploading files: {files_str}')
            utils.dl_files(files, run_path, name, out_path, config_path)
            print('-- Upload successful')

        return True
    else:
        cmd_str = 'grep cfl rsl.error*'
        cmd_list = shlex.split(cmd_str)
        pe = subprocess.run(cmd_list, capture_output=True, text=True, cwd=run_path)
        if pe.stdout != '':
            results_str = pe.stdout
        scope = sentry_sdk.get_current_scope()
        scope.add_attachment(path=wrf_log_path)
        raise ValueError(f'wrf.exe failed. Look at the rsl.out.0000 file for details: {results_str}')















































