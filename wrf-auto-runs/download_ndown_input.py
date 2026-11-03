#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 29 15:06:19 2025

@author: mike
"""
# import s3func
# import concurrent.futures
import pathlib
import shlex
import subprocess
import pendulum
import copy
import os

import params, utils

############################################
### Parameters



###########################################
### Functions


def dl_ndown_input(new_top_domain, start_date, end_date):
    """

    """
    remote = copy.deepcopy(params.file['ndown']['input'])

    input_path = pathlib.Path(remote.pop('path'))

    name = 'ndown'

    config_path = utils.create_rclone_config(name, params.data_path, remote)

    start_date1 = pendulum.instance(start_date).start_of('day')
    end_date1 = pendulum.instance(end_date).start_of('day')
    # end_date1 = pendulum.datetime(2020, 6, 2)

    # start_month = start_date1.start_of('month')
    # end_month = end_date1.start_of('month')

    days = list(pendulum.interval(start_date1, end_date1).range('days'))
    day_count = len(days)

    # Request both time spellings; the archive's spelling is the SOURCE run's, not ours.
    include_from = '\n'.join(
        utils.dl_include_names(f'wrfout_d{new_top_domain:02d}_{{date}}.nc', days)
    ) + '\n'

    ## Check for the files
    src_str = f'{name}:{input_path}/'

    cmd_str = f'rclone lsf {src_str} --config={config_path} --max-depth 1 --files-only --include-from -'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, input=include_from, capture_output=True, text=True, check=False)

    # Collapse any date present under both spellings BEFORE counting, then drive the download
    # from the deduped list. Duplicates would otherwise become two d01 files for one timestep
    # after the rename below, which ndown's sequential file walk would read twice.
    file_list = utils.dedupe_dl_listing(p.stdout.split('\n')[:-1])

    if len(file_list) != day_count:
        file_list_str = '\n'.join(file_list)
        raise ValueError(f"Total number of files to download for ndown should be {day_count}, but there are {len(file_list)} in the remote:\n{file_list_str}")

    download_from = '\n'.join(file_list) + '\n'

    ## Download
    cmd_str = f'rclone copy {src_str} {params.data_path} --transfers=4 --config={config_path} --include-from -'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, input=download_from, capture_output=True, text=True, check=False)
    utils.check_returncode(p, 'rclone copy of the ndown input')

    for file in file_list:
        file_path = params.data_path.joinpath(file)
        new_file = 'wrfout_d01' + file[10:]
        new_file_path = params.data_path.joinpath(new_file)
        os.rename(file_path, new_file_path)

    return True



############################################
### Upload files








