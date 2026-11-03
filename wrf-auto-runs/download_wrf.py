#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Mar 22 2026

@author: mike
"""
import pathlib
import shlex
import subprocess
import pendulum
import copy

import params, utils

############################################
### Parameters



###########################################
### Functions


def dl_wrf(start_date, end_date):
    """
    Download wrfout files from remote storage for use as WRF boundary conditions.
    """
    remote = copy.deepcopy(params.file['remote']['wrf'])

    wrf_path = pathlib.Path(remote.pop('path'))
    domain = remote.pop('domain')

    name = 'wrf'

    config_path = utils.create_rclone_config(name, params.data_path, remote)

    start_date1 = pendulum.instance(start_date).start_of('day')
    end_date1 = pendulum.instance(end_date).start_of('day')

    days = list(pendulum.interval(start_date1, end_date1).range('days'))
    day_count = len(days)

    # Request both time spellings; the archive's spelling is the SOURCE run's, not ours.
    include_from = '\n'.join(utils.dl_include_names(f'wrfout_{domain}_{{date}}.nc', days)) + '\n'

    ## Check that all required files exist on remote
    src_str = f'{name}:{wrf_path}/'

    cmd_str = f'rclone lsf {src_str} --config={config_path} --max-depth 1 --files-only --include-from -'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, input=include_from, capture_output=True, text=True, check=False)

    # Collapse any date present under both spellings to one file BEFORE counting, then drive the
    # download from the deduped list -- reusing the both-spellings include_from here would copy
    # duplicates down and wrf_to_int would ingest the timestep twice with no error.
    file_list = utils.dedupe_dl_listing(p.stdout.split('\n')[:-1])

    if len(file_list) != day_count:
        file_list_str = '\n'.join(file_list)
        raise ValueError(f"Expected {day_count} wrfout files on remote but found {len(file_list)}:\n{file_list_str}")

    download_from = '\n'.join(file_list) + '\n'

    ## Download
    cmd_str = f'rclone copy {src_str} {params.data_path}/wrfout --transfers=4 --config={config_path} --include-from -'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, input=download_from, capture_output=True, text=True, check=False)
    utils.check_returncode(p, 'rclone copy of the wrfout input')

    return True
