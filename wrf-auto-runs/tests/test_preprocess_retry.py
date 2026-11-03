"""
The in-job preprocessing retry (main.run_preprocess_with_retries), driven through the real chunk loop
(main.run_chunked_pipeline) with every pipeline step stubbed.

Why it exists: on uc_rch and NeSI a transient ERA5 download fault killed a chunk at metgrid, and the next job's rerun of
the same window always succeeded. A retry inside the job saves the requeue, but it must (a) never let a failed attempt's
files reach the next one, (b) still fail a deterministic fault, after a fixed number of attempts, and (c) never rerun
WRF. The stubs write the files the real steps write, so the cleanup is tested against the files real.exe would link.
"""
import pendulum
import pytest

import params
import utils

CHUNK_START = pendulum.datetime(2005, 2, 10, tz='UTC')
FRAMES = ['2005-02-10_00:00:00', '2005-02-10_03:00:00', '2005-02-10_06:00:00']


@pytest.fixture()
def main(monkeypatch):
    monkeypatch.setattr(params, 'is_sentry', False)   # before the first import: main initialises Sentry at import
    import main as m
    return m


@pytest.fixture()
def world(tmp_path, monkeypatch, main):
    """params pointed at tmp_path, every step stubbed, and a record of what ran."""
    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setattr(params, 'data_path', data)
    monkeypatch.setattr(params, 'run_path', data / 'run')
    monkeypatch.setattr(params, 'is_sentry', False)
    monkeypatch.setattr(params, 'is_wrf_input', False)
    monkeypatch.setattr(params, 'sst_source', 'cci')
    monkeypatch.setattr(params, 'cleanup_inputs', True)
    monkeypatch.setattr(params, 'restart_stop_after_upload', True)
    monkeypatch.setattr(params, 'restart_interval_days', 28)
    monkeypatch.setattr(params, '_original_begin_hours', 0)
    monkeypatch.setattr(params, 'file', {'dynamics': {'tracer_opt': 0}})
    monkeypatch.setattr(params, 'run_window', lambda: (CHUNK_START, CHUNK_START.add(days=365)))
    w = {'calls': [], 'attempt': 0, 'fail': {}, 'real_saw': [], 'restart': None, 'chunk_dates': [], 'sleeps': [],
         'at_start': {}}
    monkeypatch.setattr(main.time, 'sleep', lambda s: w['sleeps'].append(s))

    def rec(name, fn=None):
        def stub(*a, **k):
            w['calls'].append(name)
            if fn is not None:
                out = fn(*a, **k)
            else:
                out = None
            if w['fail'].get(name, 0) >= w['attempt'] and name in w['fail']:
                raise RuntimeError(f'{name} failed (injected, attempt {w["attempt"]})')
            return out
        return stub

    def set_nml(*a, **k):
        return pendulum.datetime(2005, 2, 10, tz='UTC'), pendulum.datetime(2005, 3, 10, tz='UTC'), 3, ['d01']

    def dl_era5(*a, **k):
        f = data / 'era5' / 'e5.oper.an.pl' / '200502' / f'e5.oper.an.pl.128_133_q.ll025sc.att{w["attempt"]}.nc'
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'x' * 10)

    def era5_to_int(*a, **k):
        for fr in FRAMES:
            (data / f'ERA5:{fr[:13]}').write_bytes(b'x' * 100)
        if w['attempt'] == 1:                         # a failed attempt's short extra frame
            (data / 'ERA5:2005-03-01_00').write_bytes(b'x' * 7)

    def metgrid(*a, **k):
        for fr in FRAMES:
            (data / f'met_em.d01.{fr}.nc').write_bytes(b'm')
        (data / 'metgrid.log.0000').write_text('metgrid ok\n')
        if w['attempt'] == 1:                         # a failed attempt leaves a stale extra frame and an ERROR log
            (data / 'met_em.d01.2099-01-01_00:00:00.nc').write_bytes(b'stale')
            (data / 'metgrid.log.0002').write_text('line\nERROR: The mandatory field TT was not found\n')

    def real(*a, **k):
        w['real_saw'].append(sorted(p.name for p in data.glob('met_em.*')))
        params.run_path.mkdir(exist_ok=True)

    def set_chunk_dates(start, end, begin):
        # called once by the chunk loop, then once at the start of every preprocessing attempt
        w['chunk_dates'].append((start, end, begin))
        w['attempt'] = len(w['chunk_dates']) - 1
        # what the new attempt finds on disk (after the previous attempt's cleanup)
        w['at_start'][w['attempt']] = sorted(p.name for p in data.iterdir())

    stubs = {
        'check_ndown_params': lambda d: (False, None, None, None),
        'check_nml_params': lambda d: (1, [1]),
        'set_nml_params': set_nml,
        'run_geogrid': lambda *a, **k: (160.0, -50.0, 190.0, -30.0),
        'create_trmask': None,
        'process_sst_cci': None,
        'dl_era5': dl_era5,
        'run_era5_to_int': era5_to_int,
        'run_metgrid': metgrid,
        'update_metgrid_levels': None,
        'run_real': real,
        'download_wrfrst_to_run_path': None,
        'apply_restart_namelist': None,
        'upload_chunk_namelists': None,
        'monitor_wrf': None,
    }
    for name, fn in stubs.items():
        monkeypatch.setattr(main, name, rec(name, fn))
    monkeypatch.setattr(utils, 'check_input_extent', rec('check_input_extent'))
    monkeypatch.setattr(main, 'detect_remote_restart_state', lambda uuid: w['restart'])
    monkeypatch.setattr(params, 'set_chunk_dates', set_chunk_dates)
    w['main'] = main
    w['data'] = data
    return w


def run(w):
    w['main'].run_chunked_pipeline('test-uuid')


@pytest.mark.parametrize('restart', [False, True], ids=['cold', 'restart'])
@pytest.mark.parametrize('step', ['run_geogrid', 'dl_era5', 'run_era5_to_int', 'run_metgrid', 'run_real',
                                  'download_wrfrst_to_run_path', 'upload_chunk_namelists'])
def test_one_failure_then_success(world, capsys, step, restart):
    if restart:
        world['restart'] = CHUNK_START
    elif step == 'download_wrfrst_to_run_path':
        pytest.skip('a cold start downloads no restart')
    world['fail'] = {step: 1}
    run(world)
    out = capsys.readouterr().out
    assert world['calls'].count('monitor_wrf') == 1
    assert '*** PREPROCESS ATTEMPT 1/3 FAILED' in out and f'{step} failed (injected, attempt 1)' in out
    assert '-- PREPROCESS RECOVERED on attempt 2/3' in out
    assert world['attempt'] == 2
    # every attempt re-applies the chunk's dates (once by the loop, then once per attempt)
    assert len(world['chunk_dates']) == 3 and len(set(world['chunk_dates'])) == 1


def test_a_failed_attempts_files_never_reach_real(world, capsys):
    """Attempt 1 leaves a stale extra met_em frame, a short ERA5 frame, an era5 source file and an ERROR metgrid log,
    then fails at real. real.exe on attempt 2 must see exactly the three frames attempt 2 wrote."""
    world['fail'] = {'run_real': 1}
    run(world)
    expected = sorted(f'met_em.d01.{fr}.nc' for fr in FRAMES)
    assert world['real_saw'][-1] == expected
    data = world['data']
    assert not (data / 'ERA5:2005-03-01_00').exists()
    assert not list(data.glob('era5/**/*att1.nc')) and not (data / 'metgrid.log.0002').exists()


def test_evidence_is_printed_before_the_cleanup(world, capsys):
    world['fail'] = {'run_real': 1}
    run(world)
    out = capsys.readouterr().out
    assert 'era5/: 1 source files' in out and 'att1.nc' in out
    assert 'ERA5:2005-03-01_00  (others are 100)' in out
    assert 'metgrid.log.0002' in out and 'mandatory field TT' in out


def test_three_failures_raise_and_never_run_wrf(world, capsys):
    world['fail'] = {'dl_era5': 3}
    with pytest.raises(RuntimeError, match='dl_era5 failed'):
        run(world)
    out = capsys.readouterr().out
    assert world['attempt'] == 3 and 'monitor_wrf' not in world['calls']
    assert out.count('*** PREPROCESS ATTEMPT') == 3 and 'ATTEMPT 3/3 FAILED' in out
    assert 'RECOVERED' not in out


def test_a_wrf_failure_is_not_retried(world):
    def wrf_fails(*a, **k):
        world['calls'].append('monitor_wrf')
        raise RuntimeError('wrf.exe failed')
    world['main'].monitor_wrf = wrf_fails
    with pytest.raises(RuntimeError, match='wrf.exe failed'):
        run(world)
    assert world['calls'].count('monitor_wrf') == 1 and world['attempt'] == 1


def test_a_config_error_fails_at_once(world):
    def bad(d):
        world['calls'].append('check_nml_params')
        raise ValueError('bl_pbl_physics=0 requires scalar_pblmix=1')
    world['main'].check_nml_params = bad
    with pytest.raises(ValueError, match='scalar_pblmix'):
        run(world)
    assert world['calls'] == ['check_ndown_params', 'check_nml_params'] and world['attempt'] == 0


def test_sst_runs_before_the_era5_download(world):
    run(world)
    calls = world['calls']
    assert calls.index('process_sst_cci') < calls.index('dl_era5') < calls.index('run_era5_to_int')


def test_healthy_chunk_runs_each_step_once(world, capsys):
    run(world)
    out = capsys.readouterr().out
    assert world['attempt'] == 1 and 'PREPROCESS' not in out
    for step in ('run_geogrid', 'dl_era5', 'run_era5_to_int', 'run_metgrid', 'run_real', 'monitor_wrf'):
        assert world['calls'].count(step) == 1, step


# Written out here, not imported from main: dropping a pattern from main._ATTEMPT_LEFTOVERS must fail a test.
LEFTOVERS = {'era5': 'dir', 'sst': 'dir', 'wrfout': 'dir', 'ERA5:2005-02-10_00': 'file', 'SST:2005-02-10_00': 'file',
             'WRF:2005-02-10_00': 'file', 'met_em.d01.2005-02-10_00:00:00.nc': 'file', 'metgrid.log.0001': 'file'}


def test_every_leftover_kind_is_cleaned_and_run_path_is_kept(world):
    """Attempt 1 leaves one of each kind of file a preprocessing attempt writes, plus a file in run/ (in a looping
    run, the previous chunk's only copy after a failed upload), then fails. Attempt 2 must start without any of the
    former, and with run/ untouched: run_real empties run/ itself, at the point it always did."""
    data = world['data']

    def leave_everything(*a, **k):
        world['calls'].append('dl_era5')
        for name, kind in LEFTOVERS.items():
            if kind == 'dir':
                (data / name).mkdir(exist_ok=True)
                (data / name / 'partial.nc').write_bytes(b'x')
            else:
                (data / name).write_bytes(b'x')
        (data / 'run').mkdir(exist_ok=True)
        (data / 'run' / 'wrfout_d01_2005-02-09_00_00_00.nc').write_bytes(b'previous chunk')
        if world['attempt'] == 1:
            raise RuntimeError('dl_era5 failed (injected)')
    world['main'].dl_era5 = leave_everything
    run(world)
    assert world['attempt'] == 2
    left = [n for n in world['at_start'][2] if n in LEFTOVERS]
    assert left == [], f'attempt 2 started with attempt 1\'s {left}'
    assert 'run' in world['at_start'][2] and (data / 'run' / 'wrfout_d01_2005-02-09_00_00_00.nc').exists()


def test_the_retry_pauses_between_attempts_only(world):
    world['fail'] = {'dl_era5': 2}
    run(world)
    assert world['sleeps'] == [world['main'].RETRY_PAUSE_S] * 2 and world['main'].RETRY_PAUSE_S > 0


def test_an_interrupt_is_not_retried(world):
    def interrupted(*a, **k):
        world['calls'].append('dl_era5')
        raise KeyboardInterrupt
    world['main'].dl_era5 = interrupted
    with pytest.raises(KeyboardInterrupt):
        run(world)
    assert world['attempt'] == 1 and world['sleeps'] == []


def test_a_failing_evidence_listing_does_not_stop_the_retry(world, monkeypatch, capsys):
    def broken():
        raise FileNotFoundError('a dangling symlink')
    monkeypatch.setattr(world['main'], '_attempt_evidence', broken)
    world['fail'] = {'dl_era5': 1}
    run(world)
    assert world['attempt'] == 2 and 'evidence listing failed' in capsys.readouterr().out


class _FakeSentry:
    def __init__(self):
        self.captured, self.tags = [], []

    def new_scope(self):
        outer = self

        class Scope:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def set_tags(self, tags):
                outer.tags.append(('scope', dict(tags)))
        return Scope()

    def capture_exception(self, exc):
        self.captured.append((str(exc), self.tags[-1][1] if self.tags else None))

    def set_tags(self, tags):
        self.tags.append(('global', dict(tags)))


def test_sentry_gets_each_retried_attempt_and_tags_the_last(world, monkeypatch):
    """Attempts 1 and 2 are captured with their tags; attempt 3 propagates (Sentry's excepthook reports it), so it is
    not captured here but the scope carries its tags."""
    fake = _FakeSentry()
    monkeypatch.setattr(world['main'], 'sentry_sdk', fake)
    monkeypatch.setattr(params, 'is_sentry', True)
    world['fail'] = {'dl_era5': 3}
    with pytest.raises(RuntimeError):
        run(world)
    assert [t['preprocess_attempt'] for _, t in fake.captured] == [1, 2]
    assert fake.tags[-1] == ('global', {'preprocess_attempt': 3, 'chunk_start': str(CHUNK_START)})
