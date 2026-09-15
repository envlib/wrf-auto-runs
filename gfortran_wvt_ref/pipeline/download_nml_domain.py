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
import copy

import params
import utils

############################################
### Parameters




###########################################
### Functions


def dl_nml_domain():
    """

    """
    remote = copy.deepcopy(params.file['remote']['project'])

    params.data_path.mkdir(parents=True, exist_ok=True)

    proj_path = pathlib.Path(remote.pop('path'))

    name = 'project'

    config_path = utils.create_rclone_config(name, params.data_path, remote)

    src_str = f'{name}:{proj_path}/'
    cmd_str = f'rclone copy {src_str} {params.data_path} --config={config_path} --include "geo_em.*.nc" --include "namelist.*"'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False)
    # for file_path in params.data_path.iterdir():
    #     if file_path.is_file():
    #         file_path.unlink()
    if p.stderr != '':
        raise ValueError(p.stderr)
    else:
        return True



############################################
### Upload files








