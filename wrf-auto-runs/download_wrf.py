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

    include_from = ''

    days = pendulum.interval(start_date1, end_date1).range('days')

    for day in days:
        datetime_str = day.strftime(params.wps_date_format)
        include_from += f'wrfout_{domain}_{datetime_str}.nc\n'

    ## Download
    src_str = f'{name}:{wrf_path}/'

    cmd_str = f'rclone copy {src_str} {params.data_path}/wrfout --transfers=4 --config={config_path} --include-from -'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, input=include_from, capture_output=True, text=True, check=False)

    if p.stderr != '':
        raise ValueError(p.stderr)
    else:
        return True
