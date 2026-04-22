import numpy as np
import h5netcdf
import pendulum
import pytest
import scipy.io.netcdf as nc3

from create_trmask import create_trmask


# Deterministic small grid: 10x10, left half land (x < 5), right half ocean.
# Lats span -45 to -36, lons span 170 to 179 (inclusive endpoints).
SN, WE = 10, 10
LAT_1D = np.linspace(-45.0, -36.0, SN, dtype=np.float32)
LON_1D = np.linspace(170.0, 179.0, WE, dtype=np.float32)
LAT_2D, LON_2D = np.meshgrid(LAT_1D, LON_1D, indexing='ij')
LANDMASK_2D = np.zeros((SN, WE), dtype=np.float32)
LANDMASK_2D[:, :5] = 1.0  # left half is land


def _write_fake_geo_em(path, e_vert=10):
    """Write a minimal geo_em.d01.nc that create_trmask can read."""
    with h5netcdf.File(path, 'w') as f:
        f.dimensions['Time'] = 1
        f.dimensions['south_north'] = SN
        f.dimensions['west_east'] = WE

        lat_var = f.create_variable('XLAT_M', ('Time', 'south_north', 'west_east'), dtype='f4')
        lat_var[0, :, :] = LAT_2D
        lon_var = f.create_variable('XLONG_M', ('Time', 'south_north', 'west_east'), dtype='f4')
        lon_var[0, :, :] = LON_2D
        lm_var = f.create_variable('LANDMASK', ('Time', 'south_north', 'west_east'), dtype='f4')
        lm_var[0, :, :] = LANDMASK_2D

        f.attrs['MMINLU'] = 'MODIFIED_IGBP_MODIS_NOAH'
        f.attrs['NUM_LAND_CAT'] = np.int32(21)


def _read_trmask(path):
    """Read TRMASK (2D) from a generated trmask file."""
    with nc3.netcdf_file(str(path), 'r', mmap=False) as f:
        return np.array(f.variables['TRMASK'][0, :, :])


def _read_trmask3d(path):
    with nc3.netcdf_file(str(path), 'r', mmap=False) as f:
        return np.array(f.variables['TRMASK3D'][0, :, :, :])


def _configure(mock_params, wvt, dynamics_extra=None):
    """Set [wvt] and [dynamics] on the in-memory TOML dict used by params."""
    mock_params['wvt'] = wvt
    dynamics = {'tracer_opt': 4, 'tracer2dsource': 1, 'tracer3dsource': 0}
    if dynamics_extra:
        dynamics.update(dynamics_extra)
    mock_params['dynamics'] = dynamics


START = pendulum.datetime(2020, 1, 1, 0, 0, 0)


class TestCreateTrmaskMaskTypes:
    def test_land_no_bbox_no_relax(self, mock_params, tmp_path):
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        _configure(mock_params, {'mask_type': 'land', 'relax_width': 0})

        create_trmask([1], START)

        mask = _read_trmask(tmp_path / 'trmask_d01')
        np.testing.assert_array_equal(mask, LANDMASK_2D)

    def test_ocean_no_bbox_no_relax(self, mock_params, tmp_path):
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        _configure(mock_params, {'mask_type': 'ocean', 'relax_width': 0})

        create_trmask([1], START)

        mask = _read_trmask(tmp_path / 'trmask_d01')
        np.testing.assert_array_equal(mask, 1.0 - LANDMASK_2D)

    def test_all_no_bbox_no_relax(self, mock_params, tmp_path):
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        _configure(mock_params, {'mask_type': 'all', 'relax_width': 0})

        create_trmask([1], START)

        mask = _read_trmask(tmp_path / 'trmask_d01')
        np.testing.assert_array_equal(mask, np.ones((SN, WE), dtype=np.float32))


class TestCreateTrmaskBbox:
    def test_ocean_plus_bbox_intersects(self, mock_params, tmp_path):
        """Bbox covers rows 2..6 across the full x range -- ocean cells
        inside that stripe should be 1, everything else 0."""
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        # rows 2..6 correspond to lat indices 2..6 (LAT_1D is ascending)
        _configure(
            mock_params,
            {
                'mask_type': 'ocean',
                'relax_width': 0,
                'min_lat': float(LAT_1D[2]),
                'max_lat': float(LAT_1D[6]),
                'min_lon': float(LON_1D[0]),
                'max_lon': float(LON_1D[-1]),
            },
        )

        create_trmask([1], START)

        mask = _read_trmask(tmp_path / 'trmask_d01')

        expected = np.zeros((SN, WE), dtype=np.float32)
        expected[2:7, 5:] = 1.0  # rows 2..6 AND ocean half (cols 5..9)
        np.testing.assert_array_equal(mask, expected)

    def test_all_plus_bbox_reproduces_old_bbox(self, mock_params, tmp_path):
        """mask_type='all' + bbox should equal the old bbox-only behavior."""
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        _configure(
            mock_params,
            {
                'mask_type': 'all',
                'relax_width': 0,
                'min_lat': float(LAT_1D[3]),
                'max_lat': float(LAT_1D[8]),
                'min_lon': float(LON_1D[1]),
                'max_lon': float(LON_1D[7]),
            },
        )

        create_trmask([1], START)

        mask = _read_trmask(tmp_path / 'trmask_d01')

        expected = np.zeros((SN, WE), dtype=np.float32)
        expected[3:9, 1:8] = 1.0
        np.testing.assert_array_equal(mask, expected)


class TestCreateTrmaskRelaxZone:
    def test_relax_width_applied_last(self, mock_params, tmp_path):
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        _configure(mock_params, {'mask_type': 'all', 'relax_width': 2})

        create_trmask([1], START)

        mask = _read_trmask(tmp_path / 'trmask_d01')

        expected = np.zeros((SN, WE), dtype=np.float32)
        expected[2:-2, 2:-2] = 1.0
        np.testing.assert_array_equal(mask, expected)


class TestCreateTrmask3D:
    def test_3d_mask_replicates_2d(self, mock_params, tmp_path):
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        mock_params['domains']['e_vert'] = 10
        _configure(
            mock_params,
            {'mask_type': 'land', 'relax_width': 0},
            dynamics_extra={'tracer3dsource': 1, 'tracer2dsource': 0},
        )

        create_trmask([1], START)

        mask3d = _read_trmask3d(tmp_path / 'trmask_d01')
        assert mask3d.shape == (9, SN, WE)  # e_vert - 1
        for k in range(mask3d.shape[0]):
            np.testing.assert_array_equal(mask3d[k], LANDMASK_2D)


class TestCreateTrmaskErrors:
    def test_bbox_mask_type_raises_migration_error(self, mock_params, tmp_path):
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        _configure(mock_params, {'mask_type': 'bbox'})

        with pytest.raises(ValueError, match='no longer supported'):
            create_trmask([1], START)

    def test_partial_bbox_raises(self, mock_params, tmp_path):
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        _configure(
            mock_params,
            {
                'mask_type': 'ocean',
                'min_lat': -42.0,
                'max_lat': -38.0,
                # min_lon / max_lon missing
            },
        )

        with pytest.raises(ValueError, match='min_lon'):
            create_trmask([1], START)

    def test_unknown_mask_type_raises(self, mock_params, tmp_path):
        _write_fake_geo_em(tmp_path / 'geo_em.d01.nc')
        _configure(mock_params, {'mask_type': 'desert'})

        with pytest.raises(ValueError, match='Unknown mask_type'):
            create_trmask([1], START)
