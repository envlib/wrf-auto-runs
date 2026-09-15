#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 10:40:23 2025

@author: mike
"""
import pathlib
import shlex
import os
import resource
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
    # Forward-ported from wrf-auto-runs commit 84c610d (2026-05-04, "fixed heap issue"). Without
    # these, metgrid exhausts the default 8 MB stack on a 278x325 domain and dies; the gfortran
    # runtime then prints its IEEE flag summary, which looks like the error but is the exit note.
    # ⚠ The upstream commit ALSO switched this to `mpirun -n {params.n_cores_metgrid}`. That is
    # deliberately NOT taken: WPS is built serial in this image, and this pipeline's params.py has
    # no n_cores_metgrid.
    resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    os.environ['OMP_STACKSIZE'] = '2G'
    os.environ['KMP_STACKSIZE'] = '2G'

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




