#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep 23 15:16:12 2025

@author: mike
"""
import subprocess
import os
import pathlib
import h5netcdf
import numpy as np

import params

####################################################
### Geogrid

# os.chdir(params.wps_path)

# os.symlink(params.geogrid_exe, params.data_path.joinpath('geogrid.exe'))
# os.symlink(params.wps_path.joinpath('geogrid'), params.data_path.joinpath('geogrid'))

# p = subprocess.run(['./geogrid.exe'], cwd=params.wps_path, check=True)
# p = subprocess.run([str(params.geogrid_exe)], cwd=params.wps_nml_path.parent, check=True)

# p = subprocess.Popen([str(params.geogrid_exe)], cwd=params.data_path)

def run_geogrid(src_n_domains, domains, rm_existing=True):
    # f = os.open('/home/mike/data/wrf/tests/geogrid.log', os.O_WRONLY)

    if rm_existing:
        for file in params.data_path.glob('geo_em*.nc'):
            file.unlink()

    p = subprocess.Popen(
            [str(params.geogrid_exe)],
            cwd=params.wps_nml_path.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    # response = p.poll()

    stdout, stderr = p.communicate()

    if len(stderr) > 0:
        raise ValueError(stderr)

    # print(stdout)

    ## Remove and rename files if needed
    if len(domains) < src_n_domains:
        for src_domain in range(1, src_n_domains + 1):
            if src_domain not in domains:
                file_path = params.data_path.joinpath(f'geo_em.d{src_domain:02d}.nc')
                if file_path.exists():
                    file_path.unlink()
    
        for i, domain in enumerate(domains):
            src_file_path = params.data_path.joinpath(f'geo_em.d{domain:02d}.nc')
            dst_file_path = params.data_path.joinpath(f'geo_em.d{i+1:02d}.nc')
    
            os.rename(src_file_path, dst_file_path)

    with h5netcdf.File(params.data_path.joinpath('geo_em.d01.nc')) as f:
        corner_lats = f.attrs['corner_lats']
        corner_lons = f.attrs['corner_lons']

    corner_lons = [lon if lon > 0 else 360 + lon for lon in corner_lons]

    min_lon = np.floor(np.min(corner_lons))
    max_lon = np.ceil(np.max(corner_lons))
    min_lat = np.floor(np.min(corner_lats))
    max_lat = np.ceil(np.max(corner_lats))

    return min_lon, min_lat, max_lon, max_lat























































