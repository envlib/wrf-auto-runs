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
import sentry_sdk

import params



############################################
### Parameters


###########################################
### Functions


def run_metgrid(del_old=True):
    """

    """
    cmd_str = f'{params.metgrid_exe}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False, cwd=params.data_path)

    if 'Successful completion of metgrid.' in p.stdout:
        if del_old:
            for path in params.data_path.glob('ERA5:*'):
                path.unlink()
            for path in params.data_path.glob('WRF:*'):
                path.unlink()
            for path in params.data_path.glob('SST:*'):
                path.unlink()
        return True
    else:
        if params.is_sentry:
            scope = sentry_sdk.get_current_scope()
            scope.add_attachment(path=params.data_path.joinpath('metgrid.log'))
        raise ValueError(f'metgrid failed. Look at the metgrid.log file for details: {p.stderr}')




