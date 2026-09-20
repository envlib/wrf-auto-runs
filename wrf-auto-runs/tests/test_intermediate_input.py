"""
[input].kind = 'intermediate': pre-staged WPS intermediate files in data_path (an external tool
wrote <prefix>:<date>), so the pipeline skips download/convert and only points metgrid at them.
Also [upload_end_frame], the post-run selection toggle a single-stage forecast needs.
"""

import datetime
import types

import f90nml
import pytest

import params
import utils
from run_metgrid import run_metgrid
from set_params import set_nml_params


# ---------------------------------------------------------------- params: _resolve_input_kind


def test_default_is_era5_and_remote_wrf_implies_wrf():
    assert params._resolve_input_kind({}) == ('era5', None)
    assert params._resolve_input_kind({'remote': {'wrf': {'path': '/x'}}}) == ('wrf', None)
    assert params._resolve_input_kind({'input': {'kind': 'wrf'}, 'remote': {'wrf': {}}}) == ('wrf', None)


def test_intermediate_requires_a_bare_prefix():
    assert params._resolve_input_kind({'input': {'kind': 'intermediate', 'prefix': 'IFS'}}) == ('intermediate', 'IFS')
    for bad in ({'kind': 'intermediate'}, {'kind': 'intermediate', 'prefix': ''},
                {'kind': 'intermediate', 'prefix': 'IFS:'}, {'kind': 'intermediate', 'prefix': '/data/IFS'}):
        with pytest.raises(ValueError, match='prefix'):
            params._resolve_input_kind({'input': bad})


def test_intermediate_refusals():
    base = {'input': {'kind': 'intermediate', 'prefix': 'IFS'}}
    with pytest.raises(ValueError, match=r'\[remote.wrf\] is present'):
        params._resolve_input_kind({**base, 'remote': {'wrf': {}}})
    with pytest.raises(ValueError, match=r'\[sst\].source must be omitted'):
        params._resolve_input_kind({**base, 'sst': {'source': 'cci'}})
    with pytest.raises(ValueError, match='single-stage only'):
        params._resolve_input_kind({**base, 'restart': {'enable': True}})
    with pytest.raises(ValueError, match='kind must be'):
        params._resolve_input_kind({'input': {'kind': 'grib'}})
    with pytest.raises(ValueError, match=r'requires a \[remote.wrf\]'):
        params._resolve_input_kind({'input': {'kind': 'wrf'}})


# ---------------------------------------------------------------- set_params: fg_name


@pytest.fixture
def intermediate_mode(monkeypatch):
    # module flags are set at import from the real parameters.toml; pin every one the branch reads
    monkeypatch.setattr(params, 'input_kind', 'intermediate')
    monkeypatch.setattr(params, 'input_prefix', 'IFS')
    monkeypatch.setattr(params, 'is_wrf_input', False)
    monkeypatch.setattr(params, 'sst_source', 'era5')


def test_fg_name_is_the_staged_prefix(mock_params, intermediate_mode, tmp_path):
    set_nml_params()
    wps = f90nml.read(tmp_path / 'namelist.wps')
    assert wps['metgrid']['fg_name'] == str(tmp_path / 'IFS')


def test_fg_name_unchanged_for_era5(mock_params, monkeypatch, tmp_path):
    monkeypatch.setattr(params, 'input_kind', 'era5')
    monkeypatch.setattr(params, 'input_prefix', None)
    monkeypatch.setattr(params, 'is_wrf_input', False)
    monkeypatch.setattr(params, 'sst_source', 'era5')
    set_nml_params()
    wps = f90nml.read(tmp_path / 'namelist.wps')
    assert wps['metgrid']['fg_name'] == str(tmp_path / 'ERA5')


# ---------------------------------------------------------------- run_metgrid: cleanup glob


def test_metgrid_cleanup_removes_the_staged_prefix(mock_params, intermediate_mode, tmp_path, monkeypatch):
    for name in ('IFS:2026-09-19_00', 'IFS:2026-09-19_03', 'ERA5:2020-01-01_00', 'keep.txt'):
        (tmp_path / name).write_text('x')
    monkeypatch.setattr(utils.subprocess, 'run', lambda *a, **kw: types.SimpleNamespace(
        returncode=0, stdout='Successful completion of metgrid.', stderr=''))
    import run_metgrid as rm
    monkeypatch.setattr(rm.subprocess, 'run', lambda *a, **kw: types.SimpleNamespace(
        returncode=0, stdout='Successful completion of metgrid.', stderr=''))
    monkeypatch.setattr(params, 'metgrid_exe', tmp_path / 'metgrid.exe')
    assert run_metgrid(del_old=True) is True
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ['keep.txt']


def test_metgrid_cleanup_keeps_files_without_del_old(mock_params, intermediate_mode, tmp_path, monkeypatch):
    (tmp_path / 'IFS:2026-09-19_00').write_text('x')
    import run_metgrid as rm
    monkeypatch.setattr(rm.subprocess, 'run', lambda *a, **kw: types.SimpleNamespace(
        returncode=0, stdout='Successful completion of metgrid.', stderr=''))
    monkeypatch.setattr(params, 'metgrid_exe', tmp_path / 'metgrid.exe')
    run_metgrid(del_old=False)
    assert (tmp_path / 'IFS:2026-09-19_00').exists()


# ---------------------------------------------------------------- upload_end_frame


def _run_files(tmp_path, start, n_days):
    """The wrfout names WRF writes for a 24 h-per-file run of n_days plus the end frame."""
    names = []
    for d in range(n_days + 1):
        t = start + datetime.timedelta(days=d)
        names.append(f'wrfout_d01_{t:%Y-%m-%d_%H:%M:%S}')
    for n in names:
        (tmp_path / n).write_text('x')
    return names


@pytest.mark.parametrize('cycle_hour, toggle, expect_end_frame', [
    (0, False, False),   # 00z + 144 h ends at midnight: skipped by default (hindcast clobber guard)
    (0, True, True),     # ... uploaded with the toggle
    (12, False, True),   # 12z + 144 h ends at noon: always uploaded
    (12, True, True),
])
def test_end_frame_selection(tmp_path, cycle_hour, toggle, expect_end_frame):
    start = datetime.datetime(2026, 9, 19, cycle_hour)
    names = _run_files(tmp_path, start, 6)
    end = start + datetime.timedelta(hours=144)
    files = utils.query_out_files(tmp_path, include_xtrm=True)
    selected = utils.select_files_to_ul(files, utils.end_frame_min_files(end, toggle))
    picked = sorted(p.split('/')[-1] for p in selected)
    assert (names[-1] in picked) is expect_end_frame
    assert len(picked) == (7 if expect_end_frame else 6)


def test_monitor_wrf_post_run_selection_honours_the_toggle(tmp_path, monkeypatch):
    """The wiring, not just the helper: monitor_wrf.post_run_files reads params.upload_end_frame."""
    import monitor_wrf
    start = datetime.datetime(2026, 9, 19, 0)
    names = _run_files(tmp_path, start, 6)
    end = start + datetime.timedelta(hours=144)
    monkeypatch.setattr(params, 'upload_end_frame', False)
    assert names[-1] not in {p.split('/')[-1] for p in monitor_wrf.post_run_files(tmp_path, end)}
    monkeypatch.setattr(params, 'upload_end_frame', True)
    assert names[-1] in {p.split('/')[-1] for p in monitor_wrf.post_run_files(tmp_path, end)}
