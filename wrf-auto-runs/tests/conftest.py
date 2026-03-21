import atexit
import shutil
import pathlib
import tempfile

import pytest

# ── Bootstrap: ensure parameters.toml exists so `import params` succeeds ──
# If the current parameters.toml targets Docker (no [no_docker] section),
# params.py tries to mkdir /data at import time which fails outside Docker.
# Temporarily inject a [no_docker] section, then restore the original file.
_project_root = pathlib.Path(__file__).resolve().parent.parent
_toml = _project_root / "parameters.toml"
_example = _project_root / "parameters_example.toml"
_original_text = None

if not _toml.exists() and _example.exists():
    shutil.copy(_example, _toml)

_toml_text = _toml.read_text()
_has_no_docker = any(line.strip().startswith('[no_docker]') for line in _toml_text.splitlines())
if not _has_no_docker:
    _original_text = _toml_text
    _test_dir = tempfile.mkdtemp(prefix='wrf_test_')
    _no_docker_block = (
        f'[no_docker]\n'
        f"wps_path = '{_test_dir}'\n"
        f"wrf_path = '{_test_dir}'\n"
        f"data_path = '{_test_dir}'\n"
        f"geog_data_path = '{_test_dir}'\n"
    )
    _toml.write_text(_no_docker_block + '\n' + _toml_text)

import params  # noqa: E402

# Restore original parameters.toml after import
if _original_text is not None:
    _toml.write_text(_original_text)


def _base_toml():
    """Return a minimal 3-domain TOML dict for testing."""
    return {
        'time_control': {
            'start_date': '2020-01-01 00:00:00',
            'duration_hours': 48,
            'interval_hours': 3,
            'history_file': {
                'interval_hours': [1, 1, 1],
                'begin_hours': 0,
            },
            'summary_file': {
                'output': False,
            },
            'z_level_file': {
                'output': False,
            },
        },
        'domains': {
            'dx': 27000,
            'dy': 27000,
            'map_proj': 'lambert',
            'ref_lat': -40.0,
            'ref_lon': 170.0,
            'truelat1': -40.0,
            'truelat2': -40.0,
            'stand_lon': 170.0,
            'parent_id': [1, 1, 2],
            'parent_grid_ratio': [1, 3, 3],
            'i_parent_start': [1, 30, 10],
            'j_parent_start': [1, 20, 10],
            'e_we': [100, 130, 160],
            'e_sn': [100, 130, 160],
            'geog_data_res': ['default', 'default', 'default'],
            'e_vert': 33,
            'p_top_requested': 5000,
            'parent_time_step_ratio': [1, 3, 3],
        },
    }


@pytest.fixture()
def mock_params(tmp_path, monkeypatch):
    """Patch params.* to point at tmp_path and a fresh 3-domain TOML dict."""
    toml_dict = _base_toml()

    monkeypatch.setattr(params, 'file', toml_dict)
    monkeypatch.setattr(params, 'data_path', tmp_path)
    monkeypatch.setattr(params, 'geog_data_path', tmp_path / 'WPS_GEOG')
    monkeypatch.setattr(params, 'wps_path', tmp_path / 'WPS')
    monkeypatch.setattr(params, 'wrf_path', tmp_path / 'WRF')
    monkeypatch.setattr(params, 'geogrid_exe', tmp_path / 'WPS' / 'geogrid.exe')
    monkeypatch.setattr(params, 'metgrid_exe', tmp_path / 'WPS' / 'metgrid.exe')
    monkeypatch.setattr(params, 'wps_nml_path', tmp_path / 'namelist.wps')
    monkeypatch.setattr(params, 'wrf_nml_path', tmp_path / 'namelist.input')

    # Silence the Noah-MP symlink subprocess call in set_nml_params
    import subprocess as _subprocess

    monkeypatch.setattr(_subprocess, 'run', lambda *a, **kw: None)

    return toml_dict
