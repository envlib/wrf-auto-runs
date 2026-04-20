#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pull SST + sea ice from the CCI SST v2.1 L4 product on AWS Open Data
(s3://surftemp-sst/data/sst.zarr) and write a SST:YYYY-MM-DD_HH WPS
intermediate file for each WPS timestep in the requested range.

Memory discipline: xarray does not release its read cache when subsets are
del'd. Each timestep's slice is taken with .copy() so it detaches from the
shared cache and can actually be reclaimed when the loop rebinds.
"""
from datetime import timedelta
import os

import numpy as np
import pendulum
import s3fs
import xarray as xr
from wrf_to_int.WPSUtils import IntermediateFile, MapProjection, Projections, write_slab

import params


SURFTEMP_ZARR = 'surftemp-sst/data/sst.zarr'

# WPS intermediate format constants
XLVL_SURFACE = 200100.0
MAP_SOURCE = 'CCI SST v2.1 L4 (AWS surftemp-sst)'
DATE_FMT_FILENAME = '%Y-%m-%d_%H'
DATE_FMT_HDATE = '%Y-%m-%d_%H:%M:%S'


def _wps_timestamps(start_date, end_date, hour_interval):
    """Yield timestamps from start to end, inclusive, stepping hour_interval."""
    curr = start_date.replace(minute=0, second=0, microsecond=0)
    end = end_date.replace(minute=0, second=0, microsecond=0)
    step = timedelta(hours=hour_interval)
    while curr <= end:
        yield curr
        curr += step


def _open_dataset():
    s3 = s3fs.S3FileSystem(anon=True)
    store = s3fs.S3Map(root=SURFTEMP_ZARR, s3=s3, create=False)
    return xr.open_zarr(store, consolidated=True, decode_cf=True)


def _validate_time_coverage(ds, start_date, end_date):
    time_min = pendulum.instance(ds['time'].values[0].astype('M8[ms]').astype(object))
    time_max = pendulum.instance(ds['time'].values[-1].astype('M8[ms]').astype(object))
    if start_date < time_min or end_date > time_max:
        raise ValueError(
            f'surftemp-sst coverage is {time_min.to_iso8601_string()} to '
            f'{time_max.to_iso8601_string()}, but requested range is '
            f'{start_date} to {end_date}.'
        )


def _spatial_subset(ds, min_lon, min_lat, max_lon, max_lat, pad_deg=1.0):
    """Subset by lat/lon with padding. Assumes surftemp's lat is ascending and
    lon is in [-180, 180]. Does not handle antimeridian crossing."""
    if min_lon > max_lon:
        raise ValueError(
            f'min_lon ({min_lon}) > max_lon ({max_lon}); antimeridian-crossing '
            f'domains are not supported by the surftemp SST source yet.'
        )
    return ds[['analysed_sst', 'sea_ice_fraction']].sel(
        lat=slice(min_lat - pad_deg, max_lat + pad_deg),
        lon=slice(min_lon - pad_deg, max_lon + pad_deg),
    )


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


def process_sst_surftemp(start_date, end_date, hour_interval,
                         min_lon, min_lat, max_lon, max_lat):
    """Write SST:YYYY-MM-DD_HH intermediate files for the requested window.

    start_date / end_date: datetime-like
    hour_interval: int hours between WPS records (e.g. 3 or 6)
    min/max lon/lat: domain bounding box from run_geogrid
    """
    out_dir = params.data_path
    orig_cwd = os.getcwd()
    os.chdir(out_dir)
    try:
        ds = _open_dataset()
        _validate_time_coverage(ds, start_date, end_date)

        sub = _spatial_subset(ds, min_lon, min_lat, max_lon, max_lat)

        lat_vals = sub['lat'].values
        lon_vals = sub['lon'].values
        if lat_vals.size < 2 or lon_vals.size < 2:
            raise ValueError(
                f'surftemp subset for bbox ({min_lon}, {min_lat}, {max_lon}, {max_lat}) '
                f'produced only {lat_vals.size} lat x {lon_vals.size} lon points.'
            )
        proj = _build_projection(lat_vals, lon_vals)

        for ts in _wps_timestamps(start_date, end_date, hour_interval):
            day = sub.sel(time=ts.strftime('%Y-%m-%d'), method='nearest').copy()
            sst_slab = np.asarray(day['analysed_sst'].values, dtype=np.float64)
            ice_slab = np.asarray(day['sea_ice_fraction'].values, dtype=np.float64)
            _write_intermediate(ts, sst_slab, ice_slab, proj)
    finally:
        os.chdir(orig_cwd)

    return True
