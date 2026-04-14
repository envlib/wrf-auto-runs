#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate WRF-WVT tracer mask files (trmask_d<domain>) from geo_em files.

Called automatically by main.py when tracer_opt=4 is set in the [dynamics]
section of parameters.toml. Configuration comes from the [wvt] section.

Generates TRMASK (2D) when tracer2dsource=1 and/or TRMASK3D (3D) when
tracer3dsource=1. Both variables can coexist in the same file.
"""
import numpy as np
import scipy.io.netcdf as nc3
import h5netcdf
import pathlib

import params


def create_trmask(domains, start_date):
    """
    Generate trmask_d<domain> files for each active domain.

    Parameters
    ----------
    domains : list of int
        Domain numbers to create masks for (e.g. [1, 2]).
    start_date : str
        Simulation start date in 'YYYY-MM-DD HH:MM:SS' format.
    """
    wvt_config = params.file.get('wvt', {})
    dynamics = params.file.get('dynamics', {})
    mask_type = wvt_config.get('mask_type', 'land')

    # relax_width defaults to spec_bdy_width (from [bdy_control]) to match the
    # WRF lateral boundary relaxation zone. Can be overridden in [wvt].
    bdy_control = params.file.get('bdy_control', {})
    default_relax = bdy_control.get('spec_bdy_width', 5)
    relax_width = wvt_config.get('relax_width', default_relax)

    do_2d = dynamics.get('tracer2dsource', 0) == 1
    do_3d = dynamics.get('tracer3dsource', 0) == 1

    if not do_2d and not do_3d:
        print('   WARNING: tracer_opt=4 but neither tracer2dsource nor tracer3dsource is enabled')
        return

    # Get e_vert for 3D mask vertical dimension
    e_vert = None
    if do_3d:
        e_vert_raw = params.file['domains'].get('e_vert', 33)
        if isinstance(e_vert_raw, list):
            e_vert = e_vert_raw[0]
        else:
            e_vert = e_vert_raw
        n_vert = e_vert - 1  # full levels = stagger points - 1

    # bbox parameters (only used when mask_type == 'bbox')
    min_lat = wvt_config.get('min_lat')
    max_lat = wvt_config.get('max_lat')
    min_lon = wvt_config.get('min_lon')
    max_lon = wvt_config.get('max_lon')

    if mask_type == 'bbox' and any(v is None for v in (min_lat, max_lat, min_lon, max_lon)):
        raise ValueError('[wvt] mask_type = "bbox" requires min_lat, max_lat, min_lon, max_lon')

    # Format the Times string as WRF expects: "YYYY-MM-DD_HH:MM:SS"
    if hasattr(start_date, 'format'):
        times_str = start_date.format('YYYY-MM-DD_HH:mm:ss')
    else:
        times_str = str(start_date).replace(' ', '_')

    for i, domain in enumerate(domains):
        domain_idx = i + 1
        geo_em_path = params.data_path / f'geo_em.d{domain_idx:02d}.nc'
        trmask_path = params.data_path / f'trmask_d{domain_idx:02d}'

        if not geo_em_path.exists():
            raise FileNotFoundError(f'geo_em file not found: {geo_em_path}')

        # Read grid info from geo_em
        with h5netcdf.File(geo_em_path) as geo:
            lat = np.array(geo['XLAT_M'][0, :, :])
            lon = np.array(geo['XLONG_M'][0, :, :])
            landmask = np.array(geo['LANDMASK'][0, :, :])
            mminlu = geo.attrs.get('MMINLU', 'MODIFIED_IGBP_MODIS_NOAH')
            num_land_cat = geo.attrs.get('NUM_LAND_CAT', 21)
            if isinstance(mminlu, bytes):
                mminlu = mminlu.decode()

        sn, we = lat.shape

        # Build the 2D mask
        if mask_type == 'land':
            mask = landmask.copy()
        elif mask_type == 'ocean':
            mask = 1.0 - landmask
        elif mask_type == 'bbox':
            mask = np.where(
                (lat >= min_lat) & (lat <= max_lat) &
                (lon >= min_lon) & (lon <= max_lon),
                1.0, 0.0
            )
        elif mask_type == 'all':
            mask = np.ones_like(lat)
        else:
            raise ValueError(f'Unknown mask_type: {mask_type}. Use land, ocean, bbox, or all.')

        # Zero out relaxation zone
        if relax_width > 0:
            mask[:relax_width, :] = 0
            mask[-relax_width:, :] = 0
            mask[:, :relax_width] = 0
            mask[:, -relax_width:] = 0

        # Write trmask NetCDF file
        _write_trmask(trmask_path, lat, lon, mask, times_str, mminlu, num_land_cat,
                      do_2d=do_2d, do_3d=do_3d, n_vert=n_vert if do_3d else None)

        parts = []
        if do_2d:
            parts.append('TRMASK')
        if do_3d:
            parts.append(f'TRMASK3D ({n_vert} levels)')
        print(f'   Created {trmask_path.name} ({mask_type} mask, {we}x{sn}, relax_width={relax_width}, vars: {", ".join(parts)})')


def _write_trmask(path, lat, lon, mask, times_str, mminlu, num_land_cat,
                  do_2d=True, do_3d=False, n_vert=None):
    """Write a trmask NetCDF3 classic file in the format WRF expects."""
    sn, we = lat.shape

    f = nc3.netcdf_file(str(path), 'w', version=1)

    # Dimensions
    f.createDimension('Time', None)  # unlimited
    f.createDimension('south_north', sn)
    f.createDimension('west_east', we)
    f.createDimension('DateStrLen', 19)
    if do_3d:
        f.createDimension('bottom_top', n_vert)

    # XLAT
    v = f.createVariable('XLAT', 'f4', ('south_north', 'west_east'))
    v[:] = lat.astype(np.float32)
    v.FieldType = np.int32(104)
    v.MemoryOrder = 'XY '
    v.description = 'LATITUDE SOUTH IS NEGATIVE'
    v.units = 'degree_north'
    v.stagger = ''

    # XLONG
    v = f.createVariable('XLONG', 'f4', ('south_north', 'west_east'))
    v[:] = lon.astype(np.float32)
    v.FieldType = np.int32(104)
    v.MemoryOrder = 'XY '
    v.description = 'LONGITUDE WEST IS NEGATIVE'
    v.units = 'degree_east'
    v.stagger = ''

    # TRMASK (2D source mask)
    if do_2d:
        v = f.createVariable('TRMASK', 'f4', ('Time', 'south_north', 'west_east'))
        v[0, :, :] = mask.astype(np.float32)
        v.FieldType = np.int32(104)
        v.MemoryOrder = 'XY'
        v.description = 'Tracer Source Mask (1 FOR SOURCE)'
        v.units = ''
        v.stagger = ''
        v.coordinates = 'XLONG XLAT'

    # TRMASK3D (3D source mask -- 2D mask extruded to all vertical levels)
    if do_3d:
        v = f.createVariable('TRMASK3D', 'f4', ('Time', 'bottom_top', 'south_north', 'west_east'))
        mask_3d = np.tile(mask.astype(np.float32)[np.newaxis, :, :], (n_vert, 1, 1))
        v[0, :, :, :] = mask_3d
        v.FieldType = np.int32(104)
        v.MemoryOrder = 'XYZ'
        v.description = '3D SOURCE MASK FOR MOISTURE TRACERS'
        v.units = ''
        v.stagger = ''
        v.coordinates = 'XLONG XLAT'

    # Times -- byte-by-byte for scipy.io.netcdf NC_CHAR compatibility
    v = f.createVariable('Times', 'c', ('Time', 'DateStrLen'))
    time_str_19 = times_str[:19].ljust(19, ' ')
    for i, char in enumerate(time_str_19):
        v[0, i] = char.encode('ascii')

    # Global attributes
    f.TITLE = 'OUTPUT FROM WVT TRACER MASK GENERATOR V4.0'
    f.START_DATE = times_str
    f.MMINLU = mminlu
    f.NUM_LAND_CAT = np.int32(num_land_cat)

    f.close()
