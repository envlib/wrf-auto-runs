#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate WRF-WVT tracer mask files (trmask_d<domain>) from geo_em files.

Called automatically by main.py when tracer_opt=4 is set in the [dynamics]
section of parameters.toml. Configuration comes from the [wvt] section.

Multi-region (v2.0): when [[wvt.regions]] is defined, one disjoint mask is
written per region into a region-dimensioned TRMASK(Time, wvt_regions, sn, we);
region order = WRF region index (region 1 = the unsuffixed tracer fields). The
flat single-region form ([wvt] mask_type at the top level, no regions array)
remains supported and produces a single region (wvt_regions = 1). The v2.0
multi-region registry declares TRMASK as i{wvtreg}j, so the multi-region image
carries the region axis even for a one-region run.

TRMASK layout (region axis vs flat 2D) is selected by the WVT_TRMASK_2D env var:
the frozen single-region image (registry TRMASK = 2D ij) sets WVT_TRMASK_2D=1 in
its pipeline Dockerfile, which writes a flat TRMASK(Time, sn, we) it can read;
otherwise (default) TRMASK is region-dimensioned. WVT_TRMASK_2D=1 requires exactly
one region.

Generates TRMASK (2D source) when tracer2dsource=1 and/or TRMASK3D (3D source,
single-region only) when tracer3dsource=1.
"""
import os

import numpy as np
import scipy.io.netcdf as nc3
import h5netcdf

import params


def normalize_wvt_regions(wvt_config):
    """Return an ordered list of per-region config dicts from the [wvt] section.

    Supports two equivalent forms:
      * flat single-region: ``mask_type``/``bbox_deg``/``bbox_ij`` at the [wvt] top
        level (backward compatible) -> one region.
      * multi-region: an array of tables ``[[wvt.regions]]``; each region may set its
        own ``name``/``mask_type``/``bbox_deg``/``bbox_ij`` and inherits the top-level
        ``mask_type`` as its default. List order = WRF region index (1 = first).

    Returns a list of dicts with keys: name, mask_type, bbox_deg, bbox_ij.
    """
    default_mask_type = wvt_config.get('mask_type', 'land')
    regions_raw = wvt_config.get('regions')

    if regions_raw is None:
        # Flat single-region form.
        return [{
            'name': wvt_config.get('name', 'region_01'),
            'mask_type': default_mask_type,
            'bbox_deg': wvt_config.get('bbox_deg'),
            'bbox_ij': wvt_config.get('bbox_ij'),
        }]

    if not isinstance(regions_raw, list) or not regions_raw:
        raise ValueError(
            '[wvt] regions must be a non-empty array of tables, e.g. [[wvt.regions]]'
        )

    regions = []
    for i, r in enumerate(regions_raw):
        if not isinstance(r, dict):
            raise ValueError(f'[wvt] regions[{i}] must be a table, got {r!r}')
        regions.append({
            'name': r.get('name', f'region_{i + 1:02d}'),
            'mask_type': r.get('mask_type', default_mask_type),
            'bbox_deg': r.get('bbox_deg'),
            'bbox_ij': r.get('bbox_ij'),
        })
    return regions


def num_wvt_regions(wvt_config):
    """Number of WVT source regions defined in the [wvt] section (>= 1)."""
    return len(normalize_wvt_regions(wvt_config))


def _check4(name, region_name, v):
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        raise ValueError(f'[wvt] region {region_name!r}: {name} must be a list of 4 values, got {v!r}')


def _reject_legacy_bbox_keys(d, ctx):
    """Raise if the deprecated scalar bbox keys (min_lat/...) appear in a config table."""
    legacy = [k for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon') if k in d]
    if legacy:
        raise ValueError(
            f'{ctx}: {legacy} are no longer supported. Use bbox_deg = '
            '[min_lat, max_lat, min_lon, max_lon] or bbox_ij = [i_min, i_max, j_min, j_max].'
        )


def _validate_region(reg):
    """Validate one region's mask_type + bbox config (raises ValueError)."""
    name = reg['name']
    mask_type = reg['mask_type']

    if mask_type == 'bbox':
        raise ValueError(
            f'[wvt] region {name!r}: mask_type = "bbox" is no longer supported. '
            'Use mask_type = "all" together with bbox_deg or bbox_ij.'
        )
    if mask_type not in ('land', 'ocean', 'all'):
        raise ValueError(f'[wvt] region {name!r}: Unknown mask_type {mask_type!r}. Use land, ocean, or all.')

    bbox_deg = reg['bbox_deg']
    bbox_ij = reg['bbox_ij']
    if bbox_deg is not None and bbox_ij is not None:
        raise ValueError(f'[wvt] region {name!r}: set only one of bbox_deg or bbox_ij, not both.')

    if bbox_deg is not None:
        _check4('bbox_deg', name, bbox_deg)
        min_lat, max_lat, min_lon, max_lon = (float(x) for x in bbox_deg)
        if min_lat > max_lat:
            raise ValueError(f'[wvt] region {name!r}: bbox_deg needs min_lat <= max_lat; got {min_lat} > {max_lat}')
        # min_lon > max_lon is allowed (antimeridian-crossing arc).
    if bbox_ij is not None:
        _check4('bbox_ij', name, bbox_ij)
        i_min, i_max, j_min, j_max = (int(x) for x in bbox_ij)
        if i_min > i_max or j_min > j_max:
            raise ValueError(f'[wvt] region {name!r}: bbox_ij needs i_min <= i_max and j_min <= j_max; got {bbox_ij}')
        if i_min < 0 or j_min < 0:
            raise ValueError(f'[wvt] region {name!r}: bbox_ij indices must be >= 0; got {bbox_ij}')


def _build_region_mask(reg, lat, lon, landmask, relax_width, we, sn, domain_idx):
    """Build the 2D float32 source mask for one region on one domain grid.

    mask = (mask_type selection) intersected with the optional bbox, with the
    lateral relaxation zone zeroed (tracers are not conserved there).
    """
    mask_type = reg['mask_type']
    if mask_type == 'land':
        mask = landmask.copy()
    elif mask_type == 'ocean':
        mask = 1.0 - landmask
    else:  # 'all'
        mask = np.ones_like(lat)
    mask = mask.astype('f4')

    bbox_deg = reg['bbox_deg']
    bbox_ij = reg['bbox_ij']
    if bbox_deg is not None:
        min_lat, max_lat, min_lon, max_lon = (float(x) for x in bbox_deg)
        lat_in = (lat >= min_lat) & (lat <= max_lat)
        if min_lon <= max_lon:
            lon_in = (lon >= min_lon) & (lon <= max_lon)
        else:
            # Antimeridian-crossing box: lon >= min_lon (east up to +180) OR lon <= max_lon (across -180).
            lon_in = (lon >= min_lon) | (lon <= max_lon)
        mask = mask * np.where(lat_in & lon_in, 1.0, 0.0).astype('f4')
    elif bbox_ij is not None:
        i_min, i_max, j_min, j_max = (int(x) for x in bbox_ij)
        # i = west-east (axis 1), j = south-north (axis 0); inclusive bounds.
        if i_max > we - 1 or j_max > sn - 1:
            raise ValueError(
                f"[wvt] region {reg['name']!r}: bbox_ij exceeds domain d{domain_idx:02d} grid "
                f'({we}x{sn}): i_max={i_max}, j_max={j_max} (valid max i={we - 1}, j={sn - 1})'
            )
        box = np.zeros_like(mask)
        box[j_min:j_max + 1, i_min:i_max + 1] = 1.0
        mask = mask * box

    if relax_width > 0:
        mask[:relax_width, :] = 0
        mask[-relax_width:, :] = 0
        mask[:, :relax_width] = 0
        mask[:, -relax_width:] = 0

    return mask


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

    # relax_width defaults to spec_bdy_width (from [bdy_control]) to match the WRF
    # lateral boundary relaxation zone. Shared by all regions; override in [wvt].
    bdy_control = params.file.get('bdy_control', {})
    default_relax = bdy_control.get('spec_bdy_width', 5)
    relax_width = wvt_config.get('relax_width', default_relax)

    do_2d = dynamics.get('tracer2dsource', 0) == 1
    do_3d = dynamics.get('tracer3dsource', 0) == 1

    if not do_2d and not do_3d:
        print('   WARNING: tracer_opt=4 but neither tracer2dsource nor tracer3dsource is enabled')
        return

    # Resolve + validate the region list up front, before any file I/O.
    _reject_legacy_bbox_keys(wvt_config, '[wvt]')
    for i, r in enumerate(wvt_config.get('regions') or []):
        if isinstance(r, dict):
            _reject_legacy_bbox_keys(r, f'[wvt] regions[{i}]')
    regions = normalize_wvt_regions(wvt_config)
    for reg in regions:
        _validate_region(reg)
    n_reg = len(regions)

    # TRMASK layout: region-dimensioned (Time, wvt_regions, sn, we) for the v2.0 multi-region
    # registry (TRMASK = i{wvtreg}j), or flat 2-D (Time, sn, we) for the frozen single-region image
    # whose registry declares TRMASK = ij. The single-region image opts in via WVT_TRMASK_2D=1 (set in
    # its pipeline Dockerfile); default is region-dimensioned. 2-D has no region axis -> single region only.
    region_axis = os.environ.get('WVT_TRMASK_2D') != '1'
    if not region_axis and n_reg != 1:
        raise ValueError(
            f'WVT_TRMASK_2D=1 selects the flat 2-D TRMASK layout (single-region image) but [wvt] defines '
            f'{n_reg} regions. The 2-D layout has no region axis; define exactly one region, or unset '
            'WVT_TRMASK_2D to use the region-dimensioned layout.'
        )

    if n_reg > 1 and do_3d:
        raise ValueError(
            '[wvt] multiple regions ([[wvt.regions]]) require tracer3dsource=0 '
            '(the 3D atmospheric source is single-region only).'
        )

    # Get e_vert for the 3D mask vertical dimension.
    n_vert = None
    if do_3d:
        e_vert_raw = params.file['domains'].get('e_vert', 33)
        e_vert = e_vert_raw[0] if isinstance(e_vert_raw, list) else e_vert_raw
        n_vert = e_vert - 1  # full levels = stagger points - 1

    # Format the Times string as WRF expects: "YYYY-MM-DD_HH:MM:SS".
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

        # Read grid info from geo_em.
        with h5netcdf.File(geo_em_path) as geo:
            lat = np.array(geo['XLAT_M'][0, :, :])
            lon = np.array(geo['XLONG_M'][0, :, :])
            landmask = np.array(geo['LANDMASK'][0, :, :])
            mminlu = geo.attrs.get('MMINLU', 'MODIFIED_IGBP_MODIS_NOAH')
            num_land_cat = geo.attrs.get('NUM_LAND_CAT', 21)
            if isinstance(mminlu, bytes):
                mminlu = mminlu.decode()

        sn, we = lat.shape

        # Build one mask per region.
        masks = np.zeros((n_reg, sn, we), dtype='f4')
        for k, reg in enumerate(regions):
            masks[k] = _build_region_mask(reg, lat, lon, landmask, relax_width, we, sn, domain_idx)
            if masks[k].sum() == 0:
                raise ValueError(
                    f"[wvt] region {reg['name']!r} has an empty mask on d{domain_idx:02d} "
                    f"(mask_type={reg['mask_type']!r}, bbox_deg={reg['bbox_deg']}, bbox_ij={reg['bbox_ij']}). "
                    'Check the mask_type / bbox selects source cells inside the relaxation zone.'
                )

        # Disjointness: every source cell must belong to at most one region, or the
        # per-region attribution double-counts and Sum(regions) != single-run total.
        coverage = (masks > 0).sum(axis=0)
        n_overlap = int((coverage > 1).sum())
        if n_overlap > 0:
            jj, ii = np.where(coverage > 1)
            raise ValueError(
                f'[wvt] regions overlap on {n_overlap} cell(s) of d{domain_idx:02d} '
                f'(first at j={jj[0]}, i={ii[0]}); regions must be disjoint. '
                'Adjust the bboxes/mask_types so no cell is tagged by two regions.'
            )

        _write_trmask(trmask_path, lat, lon, masks, times_str, mminlu, num_land_cat,
                      do_2d=do_2d, do_3d=do_3d, n_vert=n_vert, region_axis=region_axis)

        parts = []
        if do_2d:
            parts.append(f'TRMASK ({n_reg} region{"s" if n_reg > 1 else ""})')
        if do_3d:
            parts.append(f'TRMASK3D ({n_vert} levels)')
        per_region = ', '.join(
            f"{reg['name']}={int(masks[k].sum())}" for k, reg in enumerate(regions)
        )
        print(
            f'   Created {trmask_path.name} ({we}x{sn}, relax_width={relax_width}, '
            f'vars: {", ".join(parts)}; source cells: {per_region})'
        )


def _write_trmask(path, lat, lon, masks, times_str, mminlu, num_land_cat,
                  do_2d=True, do_3d=False, n_vert=None, region_axis=True):
    """Write a trmask NetCDF3 classic file in the format WRF expects.

    masks : (n_reg, south_north, west_east) float array -- one mask per WVT region.
    region_axis True  -> TRMASK(Time, wvt_regions, sn, we) for the multi-region registry (i{wvtreg}j).
    region_axis False -> flat TRMASK(Time, sn, we) for the single-region registry (ij); n_reg must be 1.
    """
    n_reg, sn, we = masks.shape

    f = nc3.netcdf_file(str(path), 'w', version=1)

    # Dimensions
    f.createDimension('Time', None)  # unlimited
    if region_axis:
        f.createDimension('wvt_regions', n_reg)
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

    # TRMASK source mask. region_axis True: i{wvtreg}j layout read by auxinput8 into grid%trmask(i, n, j).
    # region_axis False: flat ij layout (single-region image) read into grid%trmask(i, j); region 0 only.
    if do_2d:
        if region_axis:
            v = f.createVariable('TRMASK', 'f4', ('Time', 'wvt_regions', 'south_north', 'west_east'))
            v[0, :, :, :] = masks.astype(np.float32)
            v.MemoryOrder = 'XYZ'
            v.description = 'Tracer Source Mask (1 FOR SOURCE), per WVT region'
        else:
            v = f.createVariable('TRMASK', 'f4', ('Time', 'south_north', 'west_east'))
            v[0, :, :] = masks[0].astype(np.float32)
            v.MemoryOrder = 'XY'
            v.description = 'Tracer Source Mask (1 FOR SOURCE)'
        v.FieldType = np.int32(104)
        v.units = ''
        v.stagger = ''
        v.coordinates = 'XLONG XLAT'

    # TRMASK3D (3D source mask -- region 1's 2D mask extruded to all levels). The 3D
    # source is single-region only (enforced in create_trmask), so region 1 is used.
    if do_3d:
        v = f.createVariable('TRMASK3D', 'f4', ('Time', 'bottom_top', 'south_north', 'west_east'))
        mask_3d = np.tile(masks[0].astype(np.float32)[np.newaxis, :, :], (n_vert, 1, 1))
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
