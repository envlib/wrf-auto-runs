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

import params, utils

############################################
### Parameters



###########################################
### Functions


def dl_era5(start_date, end_date):
    """

    """
    remote = copy.deepcopy(params.file['remote']['era5'])

    era5_path = pathlib.Path(remote.pop('path'))

    name = 'era5'

    config_path = utils.create_rclone_config(name, params.data_path, remote)

    start_date1 = pendulum.instance(start_date)
    end_date1 = pendulum.instance(end_date)
    # end_date1 = pendulum.datetime(2020, 6, 2)

    start_month = start_date1.start_of('month')
    end_month = end_date1.start_of('month')

    src_str = f'{name}:{era5_path}/'

    include_from = ''

    ## sfc
    months = pendulum.interval(start_month, end_month).range('months')
    for month in months:
        month_str = month.format('YYYYMM')
        day_str = month.format('DD')

        include_from += f'e5.oper.an.sfc/{month_str}/*.{month_str}{day_str}00_*.nc\n'

    ## pl
    # months = pendulum.interval(start_month, end_month).range('months')
    days = pendulum.interval(start_date1, end_date1).range('days')

    for day in days:
        month_str = day.format('YYYYMM')
        day_str = day.format('DD')

        include_from += f'e5.oper.an.pl/{month_str}/*.{month_str}{day_str}00_*.nc\n'

    ## invariant
    include_from += 'e5.oper.invariant/197901/*.nc\n'

    ## Download
    cmd_str = f'rclone copy {src_str} {params.data_path}/era5 --transfers=4 --config={config_path} --include-from -'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, input=include_from, capture_output=True, text=True, check=False)

    if p.stderr != '':
        raise ValueError(p.stderr)
    else:
        return True



############################################
### Upload files








