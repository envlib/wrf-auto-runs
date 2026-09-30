"""
The spectral-nudging window (gfdda_end_h), review round nudging-fix-plan-1.

WRF stops nudging once xtime > gfdda_end_h*60, and xtime counts minutes from the SIMULATION start
(SIMULATION_START_DATE, carried through restarts). Until 2026-09-30 the pipeline counted from the chunk
start (every chunk after the first unnudged) or from start_date (the spin-up left out). Every case here
is driven through the real params.set_chunk_dates / set_nml_params / apply_restart_namelist, as main.py
drives them; wrfrst fixtures carry the global attributes WRF writes, under WRF's colon spelling.

What these tests can and cannot show: they prove the pipeline renders gfdda_end_h from the origin it
READS (the wrfrst attribute, or the namelist start). That the attribute is WRF's real xtime origin is
proved in the Hetzner test family (wrf-runs/projects/tests/nudging_fix), from the xtime WRF prints.
"""
import datetime

import f90nml
import h5netcdf
import numpy as np
import pendulum
import pytest
import scipy.io

import params
import utils
import monitor_wrf
from set_params import apply_restart_namelist, set_nml_params


# ── fixtures ─────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def nudged(mock_params, tmp_path, monkeypatch):
    """The base 3-domain config, spectrally nudged on d01 as C1 configures it, with run_path in tmp."""
    monkeypatch.setattr(params, '_run_window_snapshot', None)
    monkeypatch.setattr(params, '_chunked_mode_active', False)
    monkeypatch.setattr(params, '_original_begin_hours', params._original_begin_hours)  # restored after _window()
    monkeypatch.setattr(params, 'run_path', tmp_path / 'run')
    (tmp_path / 'run').mkdir()
    mock_params['fdda'] = {'grid_fdda': [2, 0, 0], 'xwavenum': [3, 0, 0], 'ywavenum': [4, 0, 0], 'gq': 0}
    return mock_params


def _window(cfg, start, end, begin_hours):
    tc = cfg['time_control']
    tc['start_date'] = start
    tc['end_date'] = end
    tc.pop('duration_hours', None)
    tc['history_file']['begin_hours'] = begin_hours
    params._original_begin_hours = begin_hours


def _end_h(tmp_path):
    return utils.to_list(f90nml.read(tmp_path / 'namelist.input')['fdda']['gfdda_end_h'])


def _write_wrfrst(path, sim_start, classic=False, with_attr=True):
    """A wrfrst as WRF writes it (netCDF-4, or classic for the fallback), global attributes only."""
    attrs = {'START_DATE': '2000-01-01_00:00:00', 'GRID_FDDA': np.int32(2)}
    if with_attr:
        attrs['SIMULATION_START_DATE'] = sim_start
    if classic:
        with scipy.io.netcdf_file(path, 'w') as f:
            f.createDimension('Time', 1)
            for k, v in attrs.items():
                setattr(f, k, v)
    else:
        with h5netcdf.File(path, 'w') as f:
            f.dimensions['Time'] = 1
            f.attrs.update(attrs)


def _restart_chunk(tmp_path, cfg, chunk_start, chunk_end, sim_start_attr, domains=None, **kw):
    """Main.py's restart chunk: set_chunk_dates -> set_nml_params -> (wrfrst downloaded) -> apply_restart_namelist."""
    params.set_chunk_dates(chunk_start, chunk_end, 0)
    set_nml_params(domains)
    (params.run_path / 'namelist.input').write_text((tmp_path / 'namelist.input').read_text())
    _write_wrfrst(utils.wrfrst_path(params.run_path, 1, chunk_start), sim_start_attr, **kw)
    apply_restart_namelist(chunk_start, int((chunk_end - chunk_start).total_minutes()), end_date_override=chunk_end)
    return utils.to_list(f90nml.read(params.run_path / 'namelist.input')['fdda']['gfdda_end_h'])


C1_START, C1_END = '2022-07-01 00:00:00', '2023-07-01 00:00:00'   # the C1 pilot sim-year
C1_REAL_START = pendulum.datetime(2022, 6, 3).naive()               # 672 h of spin-up before it
C1_RUN_HOURS = 9432                                                 # 2022-06-03 -> 2023-07-01 = 393 d


# ── single stage ─────────────────────────────────────────────────────────────────────────────────────

class TestSingleStage:
    def test_no_spinup_is_the_duration(self, nudged, tmp_path):
        """ok_: the forecast shape (begin 0) renders what it always did."""
        _window(nudged, '2023-02-10 00:00:00', '2023-02-16 00:00:00', 0)
        set_nml_params()
        assert _end_h(tmp_path) == [144, 0, 0]

    def test_spinup_is_counted(self, nudged, tmp_path):
        """P1 a_ref_north's shape: 672 h spin-up + 6 d output = 816 h. The old code rendered 144."""
        _window(nudged, '2023-02-10 00:00:00', '2023-02-16 00:00:00', 672)
        set_nml_params()
        assert _end_h(tmp_path) == [816, 0, 0]
        nml = f90nml.read(tmp_path / 'namelist.input')
        start = datetime.datetime(nml['time_control']['start_year'][0], nml['time_control']['start_month'][0],
                                  nml['time_control']['start_day'][0])
        assert start == datetime.datetime(2023, 1, 13)  # the namelist starts the spin-up: 816 h before the end

    def test_three_domains_masked(self, nudged, tmp_path):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        set_nml_params()
        nml = f90nml.read(tmp_path / 'namelist.input')
        assert _end_h(tmp_path) == [72, 0, 0]
        assert nml['fdda']['gfdda_interval_m'] == [180, 0, 0]


# ── chunked (C1) ─────────────────────────────────────────────────────────────────────────────────────

class TestChunked:
    def test_cold_chunk_is_the_whole_run(self, nudged, tmp_path):
        """C1 pilot, single-domain run, first (cold) chunk: the whole run, not the 28-day chunk (was 672)."""
        _window(nudged, C1_START, C1_END, 672)
        real_start, run_end = params.run_window()
        assert real_start == C1_REAL_START
        params.set_chunk_dates(real_start, real_start.add(days=28), 672)
        set_nml_params([1])
        assert _end_h(tmp_path) == [C1_RUN_HOURS]

    def test_restart_chunk_single_domain(self, nudged, tmp_path):
        """C1 pilot, single-domain restart chunk: grid_fdda reads back as an int; 9432 (was 672)."""
        _window(nudged, C1_START, C1_END, 672)
        params.run_window()
        chunk_start = C1_REAL_START.add(days=28)
        got = _restart_chunk(tmp_path, nudged, chunk_start, chunk_start.add(days=28), '2022-06-03_00:00:00', [1])
        assert isinstance(f90nml.read(params.run_path / 'namelist.input')['fdda']['grid_fdda'], int)
        assert got == [C1_RUN_HOURS]

    def test_last_short_chunk(self, nudged, tmp_path):
        """The pilot's final 1-day chunk (the old code rendered 24)."""
        _window(nudged, C1_START, C1_END, 672)
        chunk_start = C1_REAL_START.add(days=14 * 28)
        got = _restart_chunk(tmp_path, nudged, chunk_start, chunk_start.add(days=1), '2022-06-03_00:00:00', [1])
        assert got == [C1_RUN_HOURS]

    def test_seeded_restart_uses_the_wrfrst_origin(self, nudged, tmp_path):
        """S1's shape: this config starts 02-12, but its wrfrst came from a run that started 02-10. WRF's
        xtime counts from 02-10, so 72 h; a value derived from this config would be 24 (unnudged)."""
        _window(nudged, '2023-02-12 00:00:00', '2023-02-13 00:00:00', 0)
        start = pendulum.datetime(2023, 2, 12).naive()
        got = _restart_chunk(tmp_path, nudged, start, start.add(days=1), '2023-02-10_00:00:00')
        assert got == [72, 0, 0]

    def test_invariant_at_every_chunk_end(self, nudged, tmp_path):
        """For every chunk of the pilot, WRF's stop condition read back from what was rendered:
        xtime at the chunk end (from the fixture's origin) <= gfdda_end_h*60. Arithmetic consistency only:
        the origin is the same attribute the fix reads (WRF's real origin is checked on Hetzner)."""
        _window(nudged, C1_START, C1_END, 672)
        real_start, run_end = params.run_window()
        chunk_start = real_start.add(days=28)
        while chunk_start < run_end:
            chunk_end = min(chunk_start.add(days=28), run_end)
            got = _restart_chunk(tmp_path, nudged, chunk_start, chunk_end, '2022-06-03_00:00:00', [1])
            origin = utils.simulation_start_of(utils.wrfrst_path(params.run_path, 1, chunk_start))
            xtime_end = (utils._wall(chunk_end) - origin).total_seconds() / 60
            assert xtime_end <= got[0] * 60, (chunk_start, got)
            utils.preflight_nudging(params.run_path)  # and the realised file passes pre-flight
            chunk_start = chunk_end


# ── pins ─────────────────────────────────────────────────────────────────────────────────────────────

class TestPins:
    def test_ok_the_wvt_restart_pin(self, nudged, tmp_path):
        """ok_: wrf-runs tests/wvt_restart_2023-02 pins [72, 0] over its 72 h window -- kept."""
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda']['gfdda_end_h'] = [72, 0, 0]
        set_nml_params()
        assert _end_h(tmp_path) == [72, 0, 0]

    def test_short_pin_refused_before_preprocessing(self, nudged, tmp_path):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-16 00:00:00', 672)
        nudged['fdda']['gfdda_end_h'] = [144, 0, 0]  # the old value for P1's shape
        with pytest.raises(ValueError, match='stops nudging before the run ends'):
            set_nml_params()

    def test_long_pin_kept_on_restart(self, nudged, tmp_path):
        """A pin is validated on restart, never overwritten (the three-line core used to write 72 over it)."""
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda']['gfdda_end_h'] = [9999, 0, 0]
        start = pendulum.datetime(2023, 2, 11).naive()
        got = _restart_chunk(tmp_path, nudged, start, start.add(days=1), '2023-02-10_00:00:00')
        assert got == [9999, 0, 0]

    def test_pin_short_of_a_seeded_origin_refused_on_restart(self, nudged, tmp_path):
        """A pin that covers this config's window but not the seed's earlier origin: only the wrfrst shows it."""
        _window(nudged, '2023-02-12 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda']['gfdda_end_h'] = [24, 0, 0]
        start = pendulum.datetime(2023, 2, 12).naive()
        with pytest.raises(ValueError, match='stops nudging before the run ends'):
            _restart_chunk(tmp_path, nudged, start, start.add(days=1), '2023-02-10_00:00:00')


# ── the wrfrst read ──────────────────────────────────────────────────────────────────────────────────

class TestWrfrstRead:
    def test_missing_wrfrst_refused(self, nudged, tmp_path):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        start = pendulum.datetime(2023, 2, 11).naive()
        params.set_chunk_dates(start, start.add(days=1), 0)
        set_nml_params()
        (params.run_path / 'namelist.input').write_text((tmp_path / 'namelist.input').read_text())
        with pytest.raises((FileNotFoundError, OSError)):
            apply_restart_namelist(start, 1440, end_date_override=start.add(days=1))

    def test_missing_attribute_refused(self, nudged, tmp_path):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        start = pendulum.datetime(2023, 2, 11).naive()
        with pytest.raises(ValueError, match='SIMULATION_START_DATE'):
            _restart_chunk(tmp_path, nudged, start, start.add(days=1), '2023-02-10_00:00:00', with_attr=False)

    def test_classic_format_read_by_the_fallback(self, nudged, tmp_path):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        start = pendulum.datetime(2023, 2, 11).naive()
        got = _restart_chunk(tmp_path, nudged, start, start.add(days=1), '2023-02-10_00:00:00', classic=True)
        assert got == [72, 0, 0]

    def test_colon_spelling(self, tmp_path):
        """wrf.exe reads WRF's own name; the colon->underscore rename never reaches a wrfrst."""
        path = utils.wrfrst_path(tmp_path, 1, pendulum.datetime(2023, 2, 11).naive())
        assert path.name == 'wrfrst_d01_2023-02-11_00:00:00'


# ── refusals ─────────────────────────────────────────────────────────────────────────────────────────

class TestRefusals:
    @pytest.mark.parametrize('key,value', [('reset_simulation_start', True), ('run_hours', 48), ('run_days', 2)])
    def test_clock_moving_keys(self, nudged, key, value):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['time_control'][key] = value
        with pytest.raises(ValueError, match=key):
            set_nml_params()

    def test_clock_keys_allowed_without_nudging(self, mock_params, monkeypatch):
        monkeypatch.setattr(params, '_run_window_snapshot', None)
        mock_params['time_control']['run_hours'] = 48
        set_nml_params()

    def test_spectral_without_wavenumbers(self, nudged):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        del nudged['fdda']['ywavenum']
        with pytest.raises(ValueError, match='ywavenum'):
            set_nml_params()

    def test_ok_analysis_nudging_needs_no_wavenumbers(self, nudged, tmp_path):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda'] = {'grid_fdda': [1, 0, 0]}
        set_nml_params()
        assert _end_h(tmp_path) == [72, 0, 0]

    def test_interval_mismatch(self, nudged):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda']['gfdda_interval_m'] = [360, 0, 0]
        with pytest.raises(ValueError, match='gfdda_interval_m'):
            set_nml_params()

    def test_scalar_wavenumber_is_broadcast(self, nudged, tmp_path):
        """A scalar xwavenum reaches every domain (else WRF reads it as domain 1 only)."""
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda']['xwavenum'] = 3
        set_nml_params()
        assert f90nml.read(tmp_path / 'namelist.input')['fdda']['xwavenum'] == [3, 3, 3]


# ── pre-flight (every wrf.exe launch) and WRF's own record at delivery ───────────────────────────────

def _nml(path, start, end, end_h, restart=False, grid_fdda=(2, 0)):
    nml = f90nml.Namelist({
        'time_control': {'start_year': [start.year] * 2, 'start_month': [start.month] * 2,
                         'start_day': [start.day] * 2, 'start_hour': [start.hour] * 2,
                         'end_year': [end.year] * 2, 'end_month': [end.month] * 2, 'end_day': [end.day] * 2,
                         'end_hour': [end.hour] * 2, 'restart': restart},
        'fdda': {'grid_fdda': list(grid_fdda), 'gfdda_end_h': list(end_h)},
    })
    nml.write(path / 'namelist.input', force=True)


class TestPreflight:
    def test_cold_ok_and_short(self, tmp_path):
        s, e = datetime.datetime(2023, 2, 10), datetime.datetime(2023, 2, 13)
        _nml(tmp_path, s, e, [72, 0])
        utils.preflight_nudging(tmp_path)
        _nml(tmp_path, s, e, [71, 0])
        with pytest.raises(ValueError, match='refused wrf.exe'):
            utils.preflight_nudging(tmp_path)

    def test_restart_origin_from_the_wrfrst(self, tmp_path):
        """The old pipeline's restart chunk: 24 h from a chunk start -- refused, because the wrfrst says 02-10."""
        s, e = datetime.datetime(2023, 2, 12), datetime.datetime(2023, 2, 13)
        _write_wrfrst(utils.wrfrst_path(tmp_path, 1, s), '2023-02-10_00:00:00')
        _nml(tmp_path, s, e, [24, 0], restart=True)
        with pytest.raises(ValueError, match='before this run ends'):
            utils.preflight_nudging(tmp_path)
        _nml(tmp_path, s, e, [72, 0], restart=True)
        utils.preflight_nudging(tmp_path)

    def test_not_nudged_is_silent(self, tmp_path):
        s, e = datetime.datetime(2023, 2, 10), datetime.datetime(2023, 2, 13)
        _nml(tmp_path, s, e, [0, 0], grid_fdda=(0, 0))
        utils.preflight_nudging(tmp_path)

    def test_monitor_wrf_refuses_before_wrf_exe(self, mock_params, tmp_path, monkeypatch):
        """The pre-flight is wired where every wrf.exe launch passes, before the launch."""
        import resource
        import subprocess
        run_path = tmp_path / 'run'
        run_path.mkdir()
        monkeypatch.setattr(params, 'run_path', run_path)
        monkeypatch.setattr(params, 'is_remote_output', False)
        monkeypatch.setattr(resource, 'setrlimit', lambda *a, **k: None)
        mock_params['n_cores'] = 1

        def popen(*a, **k):
            raise RuntimeError('wrf.exe launched')

        monkeypatch.setattr(subprocess, 'Popen', popen)
        s, e = datetime.datetime(2023, 2, 10), datetime.datetime(2023, 2, 13)
        _nml(run_path, s, e, [48, 0])
        with pytest.raises(ValueError, match='refused wrf.exe'):
            monitor_wrf.monitor_wrf([], e, 'uuid', {})
        _nml(run_path, s, e, [72, 0])
        with pytest.raises(RuntimeError, match='wrf.exe launched'):
            monitor_wrf.monitor_wrf([], e, 'uuid', {})


def _wrfout(path, xtime, end_h, grid_fdda=2):
    with h5netcdf.File(path, 'w') as f:
        f.dimensions['Time'] = len(xtime)
        f.attrs.update({'GRID_FDDA': np.int32(grid_fdda), 'GFDDA_END_H': np.int32(end_h),
                        'IF_RAMPING': np.int32(0), 'DTRAMP_MIN': np.float32(0.0),
                        'SIMULATION_START_DATE': '2023-02-11_00:00:00'})
        v = f.create_variable('XTIME', ('Time',), 'f4')
        v[:] = np.asarray(xtime, 'f4')


class TestDeliveryRecord:
    def test_f_on_restart_day_is_flagged(self, tmp_path):
        """The real defect's signature (plev test f_on day 3): END_H 24, XTIME 2880..4140."""
        path = tmp_path / 'wrfout_d01_2023-02-13_00:00:00'
        _wrfout(path, np.arange(2880, 4141, 180), 24)
        assert 'UNNUDGED' in utils.check_wrfout_nudging(path)

    def test_nudged_and_end_frame_pass(self, tmp_path):
        path = tmp_path / 'wrfout_d01_2023-02-13_00:00:00'
        _wrfout(path, np.arange(2880, 4141, 180), 72)
        assert utils.check_wrfout_nudging(path) is None
        _wrfout(path, [4320.0], 72)  # the 1-frame end file: xtime == END*60 is still nudged (strict >)
        assert utils.check_wrfout_nudging(path) is None

    def test_unnudged_domain_ignored(self, tmp_path):
        path = tmp_path / 'wrfout_d02_2023-02-13_00:00:00'
        _wrfout(path, np.arange(2880, 4141, 180), 0, grid_fdda=0)
        assert utils.check_wrfout_nudging(path) is None

    def test_filter_output_files_alerts(self, mock_params, tmp_path, monkeypatch, capsys):
        """Wired into the delivery seam: an unnudged wrfout is alerted, and still delivered."""
        monkeypatch.setattr(params, 'is_sentry', False)
        monkeypatch.setattr(utils, '_nudging_alerted', set())
        path = tmp_path / 'wrfout_d01_2023-02-13_00:00:00'
        _wrfout(path, np.arange(2880, 4141, 180), 24)
        delivered = utils.filter_output_files([str(path)])
        assert delivered == [str(path)]
        assert 'UNNUDGED' in capsys.readouterr().out


# ── killers for the mutants review round nudging-fix-code-1 found alive (Fable + Sonnet), each named ──────────

D = lambda *a: pendulum.datetime(*a).naive()


def _rst_chunk(nudged, tmp_path, cfg_start, origin, pin=None, nd=1):
    """One restart chunk 02-12 -> 02-13 whose wrfrst says `origin`, with 1 or 2 nudged domains."""
    if nd == 2:
        nudged['fdda'] = {'grid_fdda': [2, 2, 0], 'xwavenum': [3, 3, 0], 'ywavenum': [4, 4, 0], 'gq': 0}
    _window(nudged, cfg_start, '2023-02-13 00:00:00', 0)
    if pin is not None:
        nudged['fdda']['gfdda_end_h'] = pin
    s = D(2023, 2, 12)
    params.set_chunk_dates(s, s.add(days=1), 0)
    set_nml_params()
    (params.run_path / 'namelist.input').write_text((tmp_path / 'namelist.input').read_text())
    for d in range(1, nd + 1):
        _write_wrfrst(utils.wrfrst_path(params.run_path, d, s), origin)
    apply_restart_namelist(s, 1440, end_date_override=s.add(days=1))
    return utils.to_list(f90nml.read(params.run_path / 'namelist.input')['fdda']['gfdda_end_h'])


class TestReviewKillers:
    def test_cold_chunk_through_apply_restart_namelist(self, nudged, tmp_path):
        """main.py calls apply_restart_namelist(None, ...) on chunk 1 of every chunked run (Sonnet S10)."""
        _window(nudged, C1_START, C1_END, 672)
        rs, _ = params.run_window()
        params.set_chunk_dates(rs, rs.add(days=28), 672)
        set_nml_params([1])
        (params.run_path / 'namelist.input').write_text((tmp_path / 'namelist.input').read_text())
        apply_restart_namelist(None, 28 * 1440, end_date_override=rs.add(days=28))
        assert utils.to_list(f90nml.read(params.run_path / 'namelist.input')['fdda']['gfdda_end_h']) == [C1_RUN_HOURS]
        utils.preflight_nudging(params.run_path)

    def test_restart_pin_equal_to_need_accepted(self, nudged, tmp_path):
        """The wvt_restart pin shape on the restart path: equal is enough (Sonnet S6)."""
        assert _rst_chunk(nudged, tmp_path, '2023-02-10 00:00:00', '2023-02-10_00:00:00', pin=[72, 0, 0])[0] == 72

    def test_restart_pin_one_hour_short_refused(self, nudged, tmp_path):
        with pytest.raises(ValueError, match='stops nudging'):
            _rst_chunk(nudged, tmp_path, '2023-02-12 00:00:00', '2023-02-10_00:00:00', pin=[71, 0, 0])

    def test_seeded_restart_every_nudged_domain(self, nudged, tmp_path):
        """Two nudged domains, each from its own wrfrst (Fable D / Sonnet S8)."""
        assert _rst_chunk(nudged, tmp_path, '2023-02-12 00:00:00', '2023-02-10_00:00:00', nd=2) == [72, 72, 0]

    def test_seed_origin_later_than_config(self, nudged, tmp_path):
        """The value is the wrfrst's, not max(config, wrfrst) (Sonnet S7)."""
        assert _rst_chunk(nudged, tmp_path, '2023-02-10 00:00:00', '2023-02-11_00:00:00') == [48, 0, 0]

    def test_preflight_every_domain_and_print_and_reset(self, tmp_path, capsys):
        """Fable F / Sonnet U9, U11, U13; and a short list is 0 on the missing domain, as in WRF (Sonnet C1)."""
        s, e = datetime.datetime(2023, 2, 10), datetime.datetime(2023, 2, 13)
        _nml(tmp_path, s, e, [72, 48], grid_fdda=(2, 2))
        with pytest.raises(ValueError, match='d02'):
            utils.preflight_nudging(tmp_path)
        _nml(tmp_path, s, e, [72], grid_fdda=(2, 2))
        with pytest.raises(ValueError, match='d02'):
            utils.preflight_nudging(tmp_path)
        _nml(tmp_path, s, e, [72, 72], grid_fdda=(2, 2))
        utils.preflight_nudging(tmp_path)
        assert 'xtime origin 2023-02-10 00:00:00' in capsys.readouterr().out
        nml = f90nml.read(tmp_path / 'namelist.input')
        nml['time_control']['reset_simulation_start'] = True
        nml.write(tmp_path / 'namelist.input', force=True)
        with pytest.raises(ValueError, match='reset_simulation_start'):
            utils.preflight_nudging(tmp_path)

    def test_monitor_wrf_preflight_in_chunked_mode(self, mock_params, tmp_path, monkeypatch):
        """The wiring holds in chunked mode too (Sonnet MM3)."""
        import resource
        import subprocess
        run_path = tmp_path / 'run'
        run_path.mkdir()
        monkeypatch.setattr(params, 'run_path', run_path)
        monkeypatch.setattr(params, 'is_remote_output', False)
        monkeypatch.setattr(params, 'restart_enable', True)
        monkeypatch.setattr(resource, 'setrlimit', lambda *a, **k: None)
        mock_params['n_cores'] = 1

        def popen(*a, **k):
            raise RuntimeError('wrf.exe launched')

        monkeypatch.setattr(subprocess, 'Popen', popen)
        s, e = datetime.datetime(2023, 2, 10), datetime.datetime(2023, 2, 13)
        _nml(run_path, s, e, [48, 0])
        with pytest.raises(ValueError, match='refused wrf.exe'):
            monitor_wrf.monitor_wrf([], e, 'uuid', {}, chunk_end=e)

    @pytest.mark.parametrize('key', ['run_minutes', 'run_seconds'])
    def test_every_clock_key_refused(self, nudged, key):
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['time_control'][key] = 30
        with pytest.raises(ValueError, match=key):
            set_nml_params()

    def test_valid_pins_and_scalars_pass(self, nudged, tmp_path):
        """A VALID interval pin passes; scalar ywavenum/gph broadcast (Sonnet S5, S15)."""
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda']['gfdda_interval_m'] = [180, 0, 0]
        for f in ('ywavenum', 'gph'):
            nudged['fdda'][f] = 3
        set_nml_params()
        nml = f90nml.read(tmp_path / 'namelist.input')['fdda']
        assert nml['gfdda_interval_m'] == [180, 0, 0] and nml['ywavenum'] == [3, 3, 3] and nml['gph'] == [3, 3, 3]

    def test_wavenumber_of_a_second_domain_checked(self, nudged):
        """The refusal reads each nudged domain's own wavenumber (Sonnet S4)."""
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda'] = {'grid_fdda': [2, 2, 0], 'xwavenum': [3, 3, 0], 'ywavenum': [4, 0, 0], 'gq': 0}
        with pytest.raises(ValueError, match='domain 2'):
            set_nml_params()

    def test_short_wavenumber_list_padded_not_refused(self, nudged, tmp_path):
        """REGRESSION (Sonnet B2): 18 real config entries write xwavenum = [3, 0] on 4-domain setups. WRF's default
        for an unlisted domain is 0, so a short list is padded, never refused."""
        _window(nudged, '2023-02-10 00:00:00', '2023-02-13 00:00:00', 0)
        nudged['fdda']['xwavenum'] = [3, 0]
        nudged['fdda']['ywavenum'] = [4]
        set_nml_params()
        nml = f90nml.read(tmp_path / 'namelist.input')['fdda']
        assert nml['xwavenum'] == [3, 0, 0] and nml['ywavenum'] == [4, 0, 0]

    def test_window_edges(self, mock_params, monkeypatch):
        """Empty window refused; ceil not floor; the duration_hours branch (Sonnet U1, U2)."""
        monkeypatch.setattr(params, '_run_window_snapshot', None)
        monkeypatch.setattr(params, '_chunked_mode_active', False)
        with pytest.raises(ValueError):
            utils.nudging_end_hours(datetime.datetime(2023, 2, 10), datetime.datetime(2023, 2, 10))
        assert utils.nudging_end_hours(datetime.datetime(2023, 2, 9, 23, 30), datetime.datetime(2023, 2, 13)) == 73
        s, e = params.run_window()                  # the fixture config: duration_hours 48, begin 0
        assert (e - s).total_hours() == 48

    def test_run_window_refuses_chunk_mode_without_snapshot(self, mock_params, monkeypatch):
        """Sonnet C3: chunked mode without the snapshot would silently return the CHUNK window."""
        monkeypatch.setattr(params, '_run_window_snapshot', None)
        monkeypatch.setattr(params, '_chunked_mode_active', True)
        with pytest.raises(RuntimeError, match='snapshot'):
            params.run_window()


class TestDeliveryCheckEdges:
    def _file(self, path, end_h=72, xtime=(4320.0,), ramp=0, dtramp=0.0, classic=False, with_xtime=True):
        attrs = {'GRID_FDDA': np.int32(2), 'GFDDA_END_H': np.int32(end_h), 'IF_RAMPING': np.int32(ramp),
                 'DTRAMP_MIN': np.float32(dtramp)}
        if classic:
            with scipy.io.netcdf_file(path, 'w') as f:
                f.createDimension('Time', len(xtime))
                for k, v in attrs.items():
                    setattr(f, k, v)
                v = f.createVariable('XTIME', 'f4', ('Time',))
                v[:] = np.asarray(xtime, 'f4')
            return
        with h5netcdf.File(path, 'w') as f:
            f.dimensions['Time'] = len(xtime)
            f.attrs.update(attrs)
            if with_xtime:
                f.create_variable('XTIME', ('Time',), 'f4')[:] = np.asarray(xtime, 'f4')

    @pytest.mark.parametrize('xt,dtramp,flagged', [(4500.0, 360.0, False), (4800.0, 360.0, True),
                                                   (4500.0, -360.0, True), (4320.0, -360.0, False)])
    def test_ramp_follows_wrf(self, tmp_path, xt, dtramp, flagged):
        """WRF extends the window only when dtramp_min > 0 (Fable E / Sonnet U14, U14b, A2)."""
        p = tmp_path / 'wrfout_d01_2023-02-13_00:00:00'
        self._file(p, xtime=(xt,), ramp=1, dtramp=dtramp)
        assert (utils.check_wrfout_nudging(p) is not None) == flagged

    def test_unreadable_is_reported(self, tmp_path):
        """Fable S / Sonnet U15: an unreadable file is a message, never a silent pass."""
        bad = tmp_path / 'wrfout_d01_2023-02-13_00:00:00'
        bad.write_bytes(b'not netcdf')
        assert 'unreadable' in utils.check_wrfout_nudging(bad)

    def test_no_xtime_is_not_checked(self, tmp_path):
        p = tmp_path / 'wrfout_d01_2023-02-14_00:00:00'
        self._file(p, end_h=24, with_xtime=False)
        assert utils.check_wrfout_nudging(p) is None

    def test_classic_format(self, tmp_path):
        """Fable C: a classic-format wrfout is read through the scipy fallback, not reported unreadable."""
        p = tmp_path / 'wrfout_d01_2023-02-13_00:00:00'
        self._file(p, end_h=24, xtime=(2880.0,), classic=True)
        assert 'UNNUDGED' in utils.check_wrfout_nudging(p)

    def test_alert_once_per_written_file_at_error_level(self, mock_params, tmp_path, monkeypatch):
        """Sonnet U17, U19, C2: one Sentry ERROR per written file; a REWRITE of the same name alerts again."""
        import os
        import sentry_sdk
        calls = []
        monkeypatch.setattr(params, 'is_sentry', True)
        monkeypatch.setattr(sentry_sdk, 'capture_message', lambda m, level=None: calls.append(level))
        monkeypatch.setattr(utils, '_nudging_alerted', set())
        p = tmp_path / 'wrfout_d01_2023-02-15_00:00:00'
        _wrfout(p, np.arange(2880, 4141, 180), 24)
        utils.filter_output_files([str(p)])
        utils.filter_output_files([str(p)])
        assert calls == ['error']
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))   # the next chunk rewrote it
        utils.filter_output_files([str(p)])
        assert calls == ['error', 'error']
