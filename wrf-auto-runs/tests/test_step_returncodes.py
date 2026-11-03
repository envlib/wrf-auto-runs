"""
The ERA5 steps judge their tools by RETURN CODE, and put the tools' own messages in the job log.

The C1 metgrid failures (2026-11) passed through here: era5_to_int < 0.5.0 reported a missing input file on STDOUT and
exited 1, and run_era5_to_int (which checked only stderr) read that as success, so metgrid ran on a truncated set.
These tests run the real step functions against fake executables on PATH.
"""
import os
import sys

import pendulum
import pytest

import params
import download_era5
import run_era5_to_int
import run_metgrid


def _fake(bin_dir, name, body):
    p = bin_dir / name
    p.write_text(f'#!{sys.executable}\nimport sys, pathlib\n{body}\n')
    p.chmod(0o755)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setattr(params, 'data_path', data)
    monkeypatch.setattr(params, 'sst_source', 'cci')
    monkeypatch.setattr(params, 'is_sentry', False)
    return bin_dir, data


def test_era5_to_int_exit1_with_stdout_only_raises(env):
    """The 0.4.0 signature: the message on stdout, nothing on stderr, exit 1."""
    bin_dir, data = env
    (data / 'era5').mkdir()
    _fake(bin_dir, 'era5_to_int', "print('Processing time record 2005-03-01_00')\n"
                                  "print('Error: Could not find file e5.oper.an.sfc.128_134_sp.ll025sc.x.nc')\n"
                                  "sys.exit(1)")
    with pytest.raises(RuntimeError, match='era5_to_int failed \\(exit 1\\)') as e:
        run_era5_to_int.run_era5_to_int('2005-02-10 00:00:00', '2005-03-10 00:00:00', 3)
    assert '128_134_sp' in str(e.value)
    assert (data / 'era5').is_dir(), 'the ERA5 source must be kept when the conversion fails'


def test_era5_to_int_stderr_on_success_is_a_notice(env, capsys):
    bin_dir, data = env
    (data / 'era5').mkdir()
    _fake(bin_dir, 'era5_to_int', "sys.stderr.write('a harmless warning\\n')")
    assert run_era5_to_int.run_era5_to_int('2005-02-10 00:00:00', '2005-03-10 00:00:00', 3) is True
    assert '-- NOTICE: era5_to_int exited 0 but wrote to stderr' in capsys.readouterr().out
    assert not (data / 'era5').exists()


def test_era5_dl_output_reaches_the_job_log(env, capsys, monkeypatch):
    bin_dir, data = env
    monkeypatch.setattr(params, 'file', {'remote': {'era5': {'type': 's3', 'path': 'x/'}}})
    _fake(bin_dir, 'era5_dl', "print('NOTICE download e5.oper.an.pl/200503/k.nc: ERROR : Attempt 1/3 failed')\n"
                              "print('Completed!')")
    download_era5.dl_era5(_dt(), _dt(), 160, -50, 190, -30)
    assert 'NOTICE download e5.oper.an.pl/200503/k.nc' in capsys.readouterr().out


def test_era5_dl_failure_raises_after_logging(env, capsys, monkeypatch):
    bin_dir, data = env
    monkeypatch.setattr(params, 'file', {'remote': {'era5': {'type': 's3', 'path': 'x/'}}})
    _fake(bin_dir, 'era5_dl', "sys.stderr.write('FAILED download e5.oper.an.sfc/200503/sp.nc rc=1: boom\\n')\n"
                              "sys.exit(1)")
    with pytest.raises(RuntimeError, match='era5_dl failed \\(exit 1\\)'):
        download_era5.dl_era5(_dt(), _dt(), 160, -50, 190, -30)
    assert 'FAILED download e5.oper.an.sfc/200503/sp.nc' in capsys.readouterr().out


def _dt():
    return pendulum.datetime(2005, 3, 1, tz='UTC')


@pytest.fixture()
def metgrid_env(env, monkeypatch):
    bin_dir, data = env
    monkeypatch.setattr(params, 'n_cores_metgrid', 4)
    monkeypatch.setattr(params, 'metgrid_exe', bin_dir / 'metgrid.exe')
    monkeypatch.setattr(params, 'input_prefix', None)
    monkeypatch.setattr(run_metgrid.resource, 'setrlimit', lambda *a: None)
    return bin_dir, data


def test_metgrid_failure_prints_the_rank_log_holding_the_error(metgrid_env, capsys):
    """With more than one rank metgrid writes metgrid.log.0000 ..., never metgrid.log."""
    bin_dir, data = metgrid_env
    _fake(bin_dir, 'mpirun', "for r in range(4):\n"
                             "    msg = 'ERROR: The mandatory field TT was not found' if r == 2 else 'fine'\n"
                             "    pathlib.Path(f'metgrid.log.{r:04d}').write_text(f'line a\\n{msg}\\n')\n"
                             "sys.exit(1)")
    with pytest.raises(ValueError, match='metgrid failed'):
        run_metgrid.run_metgrid()
    out = capsys.readouterr().out
    assert '---- metgrid.log.0002' in out and 'mandatory field TT' in out
    assert 'metgrid.log.0000' not in out


def test_metgrid_success_cleans_the_intermediates(metgrid_env):
    bin_dir, data = metgrid_env
    (data / 'ERA5:2005-03-01_00').write_bytes(b'x')
    _fake(bin_dir, 'mpirun', "print('*** Successful completion of metgrid.  ***')")
    assert run_metgrid.run_metgrid() is True
    assert not (data / 'ERA5:2005-03-01_00').exists()
