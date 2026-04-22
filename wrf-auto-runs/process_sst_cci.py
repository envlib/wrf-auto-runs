#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pull per-day SST + sea ice NetCDFs from the user's CCI SST v3 mirror (Mega S3
populated by cci-sst-dl from CEDA) and write a SST:YYYY-MM-DD_HH WPS
intermediate file for each WPS timestep in the requested range.

Mirror layout expected at {remote.sst.path}:
    {path}/{YYYY}/{YYYYMMDD}120000-ESACCI-L4_GHRSST-SSTdepth-OSTIA-GLOB_{CDR3.0|ICDR3.0}-v02.0-fv01.0.nc

Reading: h5netcdf directly (no xarray). CF decoding (scale_factor, add_offset,
_FillValue) is done manually — it's about half a dozen lines.
"""
import copy
import shlex
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import h5netcdf
import numpy as np
from wrf_to_int.WPSUtils import IntermediateFile, MapProjection, Projections, write_slab

import params
import utils


# WPS intermediate format constants
XLVL_SURFACE = 200100.0
MAP_SOURCE = 'CCI SST v3 L4 (CEDA via mirror)'
DATE_FMT_FILENAME = '%Y-%m-%d_%H'
DATE_FMT_HDATE = '%Y-%m-%d_%H:%M:%S'

# CCI v3 filename convention (from cci-sst-dl)
FILENAME_TMPL = (
    '{yyyymmdd}120000-ESACCI-L4_GHRSST-SSTdepth-OSTIA-GLOB_'
    '{variant}3.0-v02.0-fv01.0.nc'
)
CDR_LAST_YEAR = 2021  # year <= this → CDR3.0, else ICDR3.0


def _expected_variants(year):
    primary = 'CDR' if year <= CDR_LAST_YEAR else 'ICDR'
    fallback = 'ICDR' if primary == 'CDR' else 'CDR'
    return primary, fallback


def _filename_for(date, variant):
    return FILENAME_TMPL.format(yyyymmdd=date.strftime('%Y%m%d'), variant=variant)


def _wps_timestamps(start_date, end_date, hour_interval):
    """Yield timestamps from start to end, inclusive, stepping hour_interval."""
    curr = start_date.replace(minute=0, second=0, microsecond=0)
    end = end_date.replace(minute=0, second=0, microsecond=0)
    step = timedelta(hours=hour_interval)
    while curr <= end:
        yield curr
        curr += step


def _rclone_copy(src, dst, config_path):
    """rclone copyto from remote → local. Returns (ok, stderr)."""
    cmd = [
        'rclone', 'copyto', src, str(dst),
        '--config', str(config_path),
        '--retries', '3',
        '--low-level-retries', '5',
        '--contimeout', '30s',
        '--timeout', '5m',
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode == 0 and Path(dst).exists():
        return True, None
    return False, (p.stderr or p.stdout or f'exit code {p.returncode}').strip()


def _download_day(date, remote_path, local_dir, config_path):
    """Try primary variant first, fall back to the other on failure.

    Returns the Path to the downloaded NetCDF.
    Raises ValueError if neither variant is on the mirror.
    """
    primary, fallback = _expected_variants(date.year)
    errors = {}
    for variant in (primary, fallback):
        filename = _filename_for(date, variant)
        src = f'sst:{remote_path.rstrip("/")}/{date.year}/{filename}'
        dst = local_dir / filename
        ok, err = _rclone_copy(src, dst, config_path)
        if ok:
            return dst
        errors[variant] = err
    detail = '; '.join(f'{v}: {msg[:200]}' for v, msg in errors.items())
    raise ValueError(
        f'CCI SST mirror has no file for {date} (tried {"/".join(errors)}). '
        f'Extend the mirror with cci-sst-dl and retry. Details: {detail}'
    )


def _bbox_indices(nc_path, min_lon, min_lat, max_lon, max_lat, pad_deg=1.0):
    """Compute lat/lon slice indices for the bbox + pad. Also returns the
    subset lat/lon coord arrays (used to build MapProjection)."""
    with h5netcdf.File(str(nc_path), 'r') as f:
        lat = np.asarray(f.variables['lat'][:])
        lon = np.asarray(f.variables['lon'][:])

    lat_lo = int(np.searchsorted(lat, min_lat - pad_deg, side='left'))
    lat_hi = int(np.searchsorted(lat, max_lat + pad_deg, side='right'))
    lon_lo = int(np.searchsorted(lon, min_lon - pad_deg, side='left'))
    lon_hi = int(np.searchsorted(lon, max_lon + pad_deg, side='right'))

    if lat_hi - lat_lo < 2 or lon_hi - lon_lo < 2:
        raise ValueError(
            f'CCI subset for bbox ({min_lon}, {min_lat}, {max_lon}, {max_lat}) '
            f'produced only {lat_hi - lat_lo} lat × {lon_hi - lon_lo} lon points.'
        )

    return (lat_lo, lat_hi, lon_lo, lon_hi), lat[lat_lo:lat_hi], lon[lon_lo:lon_hi]


def _read_day_slab(nc_path, var_name, idx):
    """Read a single 2-D slab with CF decoding (scale_factor, add_offset, _FillValue)."""
    lat_lo, lat_hi, lon_lo, lon_hi = idx
    with h5netcdf.File(str(nc_path), 'r') as f:
        var = f.variables[var_name]
        raw = np.asarray(var[0, lat_lo:lat_hi, lon_lo:lon_hi])
        scale = float(var.attrs['scale_factor']) if 'scale_factor' in var.attrs else 1.0
        offset = float(var.attrs['add_offset']) if 'add_offset' in var.attrs else 0.0
        fill = var.attrs.get('_FillValue')

    data = raw.astype(np.float64) * scale + offset
    if fill is not None:
        data[raw == fill] = np.nan
    return data


def _build_projection(lat_vals, lon_vals):
    delta_lat = float(lat_vals[1] - lat_vals[0])
    delta_lon = float(lon_vals[1] - lon_vals[0])
    return MapProjection(
        Projections.LATLON,
        startLat=float(lat_vals[0]),
        startLon=float(lon_vals[0]),
        startI=1.0,
        startJ=1.0,
        deltaLat=delta_lat,
        deltaLon=delta_lon,
    )


def _write_intermediate(ts, sst_slab, ice_slab, proj):
    datestr = ts.strftime(DATE_FMT_FILENAME)
    hdate = ts.strftime(DATE_FMT_HDATE)

    intfile = IntermediateFile('SST', datestr)
    try:
        write_slab(intfile, sst_slab, XLVL_SURFACE, proj,
                   'SST', hdate, 'K', MAP_SOURCE, 'Sea-Surface Temperature')
        write_slab(intfile, ice_slab, XLVL_SURFACE, proj,
                   'SEAICE', hdate, 'fraction', MAP_SOURCE, 'Sea-Ice Fraction')
    finally:
        intfile.close()


def process_sst_cci(start_date, end_date, hour_interval,
                    min_lon, min_lat, max_lon, max_lat):
    """Pull per-day CCI SST NetCDFs from the mirror and write SST:* intermediates.

    start_date / end_date: datetime-like
    hour_interval: int hours between WPS records (e.g. 3 or 6)
    min/max lon/lat: domain bounding box from run_geogrid
    """
    sst_dir = params.data_path.joinpath('sst')
    sst_dir.mkdir(exist_ok=True)

    # Build rclone config for the 'sst:' remote (reuse the [remote.sst] TOML section)
    remote_cfg = copy.deepcopy(params.file['remote']['sst'])
    remote_path = remote_cfg.pop('path', '')
    config_path = utils.create_rclone_config('sst', params.data_path, remote_cfg)

    # Collect unique dates needed (one NetCDF per date, reused across sub-daily slots)
    timestamps = list(_wps_timestamps(start_date, end_date, hour_interval))
    unique_dates = sorted({ts.date() for ts in timestamps})

    # Download each day once; map date -> local NetCDF path
    nc_by_date = {}
    for d in unique_dates:
        nc_by_date[d] = _download_day(d, remote_path, sst_dir, config_path)

    # Build projection + bbox indices from the first file (all share the same grid)
    first_nc = nc_by_date[unique_dates[0]]
    idx, lat_sub, lon_sub = _bbox_indices(first_nc, min_lon, min_lat, max_lon, max_lat)
    proj = _build_projection(lat_sub, lon_sub)

    # Write one WPS intermediate per timestamp, reading each NetCDF only once
    orig_cwd = Path.cwd()
    import os
    os.chdir(params.data_path)
    try:
        slab_cache = {}  # date -> (sst_slab, ice_slab)
        for ts in timestamps:
            d = ts.date()
            if d not in slab_cache:
                nc_path = nc_by_date[d]
                sst_slab = _read_day_slab(nc_path, 'analysed_sst', idx)
                ice_slab = _read_day_slab(nc_path, 'sea_ice_fraction', idx)
                slab_cache[d] = (sst_slab, ice_slab)
            sst_slab, ice_slab = slab_cache[d]
            _write_intermediate(ts, sst_slab, ice_slab, proj)
    finally:
        os.chdir(orig_cwd)

    # Cleanup downloaded NetCDFs
    shutil.rmtree(sst_dir)
    return True
