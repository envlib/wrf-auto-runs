#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep 23 15:16:12 2025

@author: mike
"""
import hashlib
import subprocess
import os
import pathlib
import h5netcdf
import numpy as np

import params

####################################################
### Geogrid

GEOGRID_SUCCESS = 'Successful completion of geogrid'
# Largest fraction of LANDMASK land cells allowed at or below 0 m, calibrated on the C1 grid with
# the production geogrid (wrf-model-eval lab/2026-10_terrain_fallback; record docs/terrain_fallback.md):
#   healthy  fixed table d01 0 of 2,901 (0 %); S1 3 km nest 4 of 29,960 (0.013 %, coastal cells)
#   defects  the flat table 1,005 of 2,901 (34.6 %); ONE GMTED tile missing (Tasmania) 188 (6.5 %)
# 1 % sits 75x above the worst healthy case and 6.5x below the smallest defect measured. A missing tile
# that covers less than 1 % of a domain's land passes, and since the fallback a missing LINZ tile is filled from
# GMTED, not flattened; wrf-model-eval lab/2026-10_terrain_fallback/geog_manifest.sh covers both, per cluster.
MAX_FLAT_LAND_FRACTION = 0.01


def check_terrain(geo_em_path, label):
    """
    Refuse a geo_em whose land is flat. geogrid exits 0 and reports success when a terrain source's
    tiles are absent, writing fill_missing (0 m) instead, so the only place the fault is visible is the
    output. Counts land AT OR BELOW 0 m, so a negative fill value cannot hide the same fault. Prints one
    line per domain, with a hash of HGT_M that identifies the terrain a run used.
    """
    with h5netcdf.File(geo_em_path, 'r') as f:
        hgt = np.asarray(f.variables['HGT_M'][0])
        land = np.asarray(f.variables['LANDMASK'][0]) > 0.5
    n_land = int(land.sum())
    n_flat = int((hgt[land] <= 0).sum())
    sha = hashlib.sha256(np.ascontiguousarray(hgt).tobytes()).hexdigest()
    frac = n_flat / n_land if n_land else 0.0
    print(f'-- geogrid terrain {label}: {n_land} land cells, HGT_M <= 0 on {n_flat} ({frac:.2%}); HGT_M sha256 {sha}')
    if frac > MAX_FLAT_LAND_FRACTION:
        raise ValueError(f'geogrid terrain {label}: HGT_M <= 0 on {n_flat} of {n_land} land cells ({frac:.1%}, limit '
                         f'{MAX_FLAT_LAND_FRACTION:.1%}). A terrain source is missing or does not cover the domain '
                         f'-- check GEOGRID.TBL HGT_M and geog_data_path.')
    return sha

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
    if GEOGRID_SUCCESS not in stdout:
        raise ValueError(f'geogrid did not report "{GEOGRID_SUCCESS}" (it exits 0 even when a source directory '
                         f'is missing). The end of its output:\n{stdout[-2000:]}')

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

            if src_file_path != dst_file_path:
                os.rename(src_file_path, dst_file_path)

    for i in range(1, len(domains) + 1):
        check_terrain(params.data_path.joinpath(f'geo_em.d{i:02d}.nc'), f'd{i:02d}')

    # Use the full XLAT_M / XLONG_M arrays (not just the 4 corner_lats / corner_lons
    # attributes) because for Lambert conformal and other conic projections, the extreme
    # lat/lon points sit on the edges between corners, not at the corners themselves.
    # Computing bounds from only corners under-estimates the actual domain extent and
    # causes downstream ERA5/met_em to be clipped short of the WRF grid, producing
    # "missing values" failures in metgrid for the cells just outside the requested bbox.
    with h5netcdf.File(params.data_path.joinpath('geo_em.d01.nc')) as f:
        all_lats = np.asarray(f['XLAT_M'][0])
        all_lons = np.asarray(f['XLONG_M'][0])

    # Normalize to 0-360 convention (matches the existing corner-based logic).
    all_lons = np.where(all_lons < 0, all_lons + 360, all_lons)

    min_lon = np.floor(np.min(all_lons))
    max_lon = np.ceil(np.max(all_lons))
    min_lat = np.floor(np.min(all_lats))
    max_lat = np.ceil(np.max(all_lats))

    return min_lon, min_lat, max_lon, max_lat























































