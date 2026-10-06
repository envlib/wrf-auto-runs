"""
run_geogrid refuses a geogrid run whose terrain is flat on land, or that did not report success.

Why (2026-10-05): the NZ geogrid table read HGT_M from a NZ-only DEM with no fallback, so every land cell
outside New Zealand was written as 0 m in every 12 km run from 2026-03-17 on. geogrid exits 0 and prints
"Successful completion" when a source's tiles are absent. When a whole source directory is absent it exits 0
without the success line; the Intel dmpar build then writes MPI_Abort to stderr, but a serial build leaves
stderr EMPTY, so the success line is the only portable signal. Review rounds terrain-fallback-1 (plan) and
terrain-fallback-code-1 (code); record: wrf-model-eval docs/terrain_fallback.md.

The cases use the production numbers (gate in wrf-model-eval lab/2026-10_terrain_fallback): one missing GMTED
tile flattened 188 of 2,901 land cells, the healthy 3 km nest has 4 of 29,960 coastal cells at 0 m. They run
against the module's own MAX_FLAT_LAND_FRACTION, so changing the limit without revisiting them fails here.

Each case drives the real run_geogrid() with a stand-in geogrid.exe that prints what geogrid prints and
writes a geo_em file built here.
"""
import hashlib

import h5netcdf
import numpy as np
import pytest

import params
import run_geogrid as rg

SUCCESS = ' Successful completion of geogrid.        \n'


def write_geo_em(path, hgt, land):
    ny, nx = hgt.shape
    lat = np.linspace(-50, -30, ny)[:, None] * np.ones((1, nx))
    lon = np.linspace(150, 180, nx)[None, :] * np.ones((ny, 1))
    with h5netcdf.File(path, 'w') as f:
        f.dimensions = {'Time': 1, 'south_north': ny, 'west_east': nx}
        for name, arr in (('HGT_M', hgt), ('LANDMASK', land.astype('f4')), ('XLAT_M', lat), ('XLONG_M', lon)):
            v = f.create_variable(name, ('Time', 'south_north', 'west_east'), 'f4')
            v[...] = arr[None].astype('f4')


@pytest.fixture()
def geogrid(mock_params, tmp_path):
    """Install a stand-in geogrid.exe; returns a function that sets what it prints and which geo_em it writes."""
    exe = params.geogrid_exe
    exe.parent.mkdir(parents=True, exist_ok=True)
    src = tmp_path / 'src'
    src.mkdir()

    def set_run(stdout, geo_ems, stderr=''):
        for f in src.glob('geo_em.d*.nc'):
            f.unlink()
        for i, (hgt, land) in enumerate(geo_ems, start=1):
            if hgt is not None:
                write_geo_em(src / f'geo_em.d{i:02d}.nc', hgt, land)
        (src / 'stdout.txt').write_text(stdout)
        (src / 'stderr.txt').write_text(stderr)
        exe.write_text(f'#!/bin/bash\ncat {src}/stdout.txt\ncat {src}/stderr.txt >&2\n'
                       f'cp {src}/geo_em.d*.nc {params.data_path}/ 2>/dev/null\nexit 0\n')
        exe.chmod(0o755)

    return set_run


def land_with_flat(n_land, n_flat, shape=(20, 20)):
    land = np.zeros(shape, bool)
    land.flat[:n_land] = True
    hgt = np.where(land, 350.0, 0.0)
    hgt.flat[:n_flat] = 0.0
    return hgt, land


def test_flat_land_outside_nz_is_refused(geogrid):
    """The production defect's own signature: a third of the land at exactly 0 m (1,005 of 2,901 on C1)."""
    geogrid(SUCCESS, [land_with_flat(290, 100)])
    with pytest.raises(ValueError, match='HGT_M <= 0'):
        rg.run_geogrid(1, [1])


def test_real_terrain_passes_and_reports(geogrid, capsys):
    geogrid(SUCCESS, [land_with_flat(290, 0)])
    rg.run_geogrid(1, [1])
    out = capsys.readouterr().out
    assert 'geogrid terrain d01: 290 land cells, HGT_M <= 0 on 0' in out
    assert 'HGT_M sha256 ' in out


def test_missing_success_line_is_refused_even_with_a_geo_em(geogrid):
    """geogrid with an absent source directory: 'ERROR: Could not open', exit 0, empty stderr."""
    geogrid(' ERROR: Could not open /WPS_GEOG/topo_gmted2010_30s/index\n', [land_with_flat(290, 0)])
    with pytest.raises(ValueError, match='Successful completion'):
        rg.run_geogrid(1, [1])


def test_every_domain_is_checked(geogrid):
    geogrid(SUCCESS, [land_with_flat(290, 0), land_with_flat(290, 100)])
    with pytest.raises(ValueError, match='d02'):
        rg.run_geogrid(2, [1, 2])


def test_all_ocean_domain_passes(geogrid):
    geogrid(SUCCESS, [land_with_flat(0, 0)])
    rg.run_geogrid(1, [1])


@pytest.mark.parametrize('n_flat, refused', [(0, False), (1, False), (2, True)])
def test_threshold_boundary(geogrid, monkeypatch, n_flat, refused):
    """At the limit passes, one cell over refuses: 1 of 100 land cells = 1 %, the limit set here."""
    monkeypatch.setattr(rg, 'MAX_FLAT_LAND_FRACTION', 0.01)
    geogrid(SUCCESS, [land_with_flat(100, n_flat)])
    if refused:
        with pytest.raises(ValueError, match='HGT_M <= 0'):
            rg.run_geogrid(1, [1])
    else:
        rg.run_geogrid(1, [1])


# ── production numbers, against the module's own limit (no monkeypatch) ─────────────────────────────────

def test_one_missing_gmted_tile_is_refused(geogrid):
    """Gate mutant M3: one GMTED tile (Tasmania) absent -> 188 of 2,901 land cells at 0 m (6.5 %)."""
    geogrid(SUCCESS, [land_with_flat(2901, 188, (324, 277))])
    with pytest.raises(ValueError, match='188 of 2901'):
        rg.run_geogrid(1, [1])


def test_healthy_nest_coastal_zeros_pass(geogrid):
    """The S1 3 km nest, fixed table: 4 of 29,960 land cells at exactly 0 m (coastal). Must pass."""
    geogrid(SUCCESS, [land_with_flat(29960, 4, (557, 357))])
    rg.run_geogrid(1, [1])


def test_negative_fill_counts_as_flat(geogrid):
    """A table with fill_missing = -9999 must not hide the defect: land at or below 0 m is counted."""
    hgt, land = land_with_flat(290, 0)
    hgt.flat[:100] = -9999.0
    geogrid(SUCCESS, [(hgt, land)])
    with pytest.raises(ValueError, match='HGT_M <= 0 on 100'):
        rg.run_geogrid(1, [1])


def test_reported_hash_is_the_float32_hgt_m(geogrid, capsys):
    """The logged hash is what record.py and the measurement compare: sha256 of the float32 HGT_M bytes."""
    hgt, land = land_with_flat(290, 0)
    geogrid(SUCCESS, [(hgt, land)])
    rg.run_geogrid(1, [1])
    want = hashlib.sha256(np.ascontiguousarray(hgt.astype('f4')).tobytes()).hexdigest()
    assert f'HGT_M sha256 {want}' in capsys.readouterr().out


# ── domain subsets: the check must see the RENAMED files main.py will use ────────────────────────────────

def test_subset_checks_the_renamed_nest_not_the_dropped_parent(geogrid, capsys):
    """run = [2] of 2 domains: geogrid writes d01 + d02, the parent is dropped and d02 renamed to d01."""
    geogrid(SUCCESS, [land_with_flat(290, 100), land_with_flat(290, 0)])   # flat parent, healthy nest
    rg.run_geogrid(2, [2])
    out = capsys.readouterr().out
    assert out.count('geogrid terrain d') == 1 and 'HGT_M <= 0 on 0' in out


def test_subset_refuses_a_flat_kept_domain(geogrid):
    """Three source domains, keep [1, 3]: the third (renamed d02) is flat and must be refused."""
    geogrid(SUCCESS, [land_with_flat(290, 0), land_with_flat(290, 0), land_with_flat(290, 100)])
    with pytest.raises(ValueError, match='d02'):
        rg.run_geogrid(3, [1, 3])


# ── output provenance ───────────────────────────────────────────────────────────────────────────────────

def test_stale_geo_em_is_not_checked_in_place_of_a_missing_one(geogrid):
    """A previous run's healthy geo_em must be removed before geogrid runs: if this run writes none, refuse."""
    hgt, land = land_with_flat(290, 0)
    write_geo_em(params.data_path / 'geo_em.d01.nc', hgt, land)          # stale, from an earlier chunk
    geogrid(SUCCESS, [(None, None)])                                     # this run writes nothing
    with pytest.raises((FileNotFoundError, OSError)):
        rg.run_geogrid(1, [1])


def test_real_missing_source_stdout_is_refused(geogrid):
    """The production Intel geogrid's stdout with the GMTED directory absent (gate run M4), verbatim."""
    real = ('Parsed 68 entries in GEOGRID.TBL\nProcessing domain 1 of 1\n'
            'ERROR: Could not open /WPS_GEOG/topo_gmted2010_30s/index\n')
    geogrid(real, [land_with_flat(290, 0)])
    with pytest.raises(ValueError, match='Successful completion'):
        rg.run_geogrid(1, [1])


def test_stderr_is_refused(geogrid):
    """The Intel dmpar build's abort line goes to stderr; any stderr refuses (the pre-existing guard)."""
    geogrid(SUCCESS, [land_with_flat(290, 0)],
            stderr='Abort(0) on node 0 (rank 0 in comm 0): application called MPI_Abort(MPI_COMM_WORLD, 0)\n')
    with pytest.raises(ValueError, match='MPI_Abort'):
        rg.run_geogrid(1, [1])


def test_low_but_real_terrain_is_not_flat(geogrid):
    """Coastal and lowland cells can be a fraction of a metre high; only <= 0 m is the fill. 100 of 290 at 0.5 m pass."""
    hgt, land = land_with_flat(290, 0)
    hgt.flat[:100] = 0.5
    geogrid(SUCCESS, [(hgt, land)])
    rg.run_geogrid(1, [1])


def test_stale_nest_geo_em_is_removed(geogrid):
    """A previous run's healthy d02 must not stand in for this run's: geogrid writes only d01 here, so refuse."""
    hgt, land = land_with_flat(290, 0)
    write_geo_em(params.data_path / 'geo_em.d02.nc', hgt, land)          # stale nest from an earlier run
    geogrid(SUCCESS, [land_with_flat(290, 0)])                           # this run writes d01 only
    with pytest.raises((FileNotFoundError, OSError)):
        rg.run_geogrid(2, [1, 2])
