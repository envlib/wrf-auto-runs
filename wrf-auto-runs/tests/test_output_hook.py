"""
[hooks].on_output_file: a command run per uploaded output file, after rclone succeeded and before the
local delete; non-fatal; SIGTERM-then-SIGKILL on timeout. The rclone call is faked; the hook commands
are real processes so the signal path is exercised for real.
"""

import os
import sys
import textwrap
import types

import pytest

import params
import utils


def _hook(command, **kw):
    return {'command': command, 'match': kw.get('match', '*'), 'timeout_seconds': kw.get('timeout_seconds', 900),
            'grace_seconds': kw.get('grace_seconds', 60), 'delete_on_success': kw.get('delete_on_success', True)}


def _script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return f'{sys.executable} {p}'


@pytest.fixture
def no_sentry(monkeypatch):
    monkeypatch.setattr(params, 'is_sentry', False)


# ---------------------------------------------------------------- run_output_hook


def test_hook_receives_the_path_while_the_file_exists(tmp_path, no_sentry):
    target = tmp_path / 'wrfout_d02_2026-09-19_00_00_00.nc'
    target.write_text('data')
    log = tmp_path / 'seen.txt'
    cmd = _script(tmp_path, 'hook.py', f'''
        import os, sys
        p = sys.argv[1]
        open({str(log)!r}, 'w').write(f"{{p}} {{os.path.exists(p)}}")
    ''') + ' {path}'
    assert utils.run_output_hook(_hook(cmd), str(target)) is True
    assert log.read_text() == f'{target} True'


def test_hook_failure_is_reported_not_raised(tmp_path, no_sentry, capsys):
    cmd = _script(tmp_path, 'hook.py', '''
        import sys
        print('something went wrong', file=sys.stderr)
        sys.exit(3)
    ''') + ' {path}'
    assert utils.run_output_hook(_hook(cmd), str(tmp_path / 'wrfout_d02_x.nc')) is False
    out = capsys.readouterr().out
    assert 'output hook FAILED' in out and 'exited 3' in out and 'something went wrong' in out


def test_hook_that_cannot_start_is_reported(tmp_path, no_sentry, capsys):
    assert utils.run_output_hook(_hook('/no/such/binary {path}'), str(tmp_path / 'wrfout_d02_x.nc')) is False
    assert 'could not run' in capsys.readouterr().out


def test_hook_never_raises(tmp_path, no_sentry, capsys):
    """Review ifs-forecast-cycle-code-1: a stray brace in the command or non-UTF-8 output must not kill main.py."""
    assert utils.run_output_hook(_hook('sh -c "echo {}" {path}'), str(tmp_path / 'wrfout_x')) is False
    assert 'could not run' in capsys.readouterr().out
    cmd = _script(tmp_path, 'hook.py', "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe bytes'); sys.stderr.buffer.write(b'\\xff warn')") + ' {path}'
    assert utils.run_output_hook(_hook(cmd), str(tmp_path / 'wrfout_x')) is True
    assert 'hook stderr' in capsys.readouterr().out  # stderr tail is printed on success too


def test_hook_path_with_spaces_is_one_argument(tmp_path, no_sentry):
    log = tmp_path / 'seen.txt'
    cmd = _script(tmp_path, 'hook.py', f"import sys; open({str(log)!r}, 'w').write(str(len(sys.argv)))") + ' {path}'
    assert utils.run_output_hook(_hook(cmd), str(tmp_path / 'dir with space' / 'wrfout_x')) is True
    assert log.read_text() == '2'


def test_grandchild_holding_the_pipe_does_not_stall(tmp_path, no_sentry):
    """SIGKILL reaches the process group: a grandchild inheriting the pipes cannot hold the poll loop."""
    import time
    cmd = _script(tmp_path, 'hook.py', """
        import signal, subprocess, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        subprocess.Popen(['sleep', '30'])
        time.sleep(30)
    """) + ' {path}'
    t0 = time.monotonic()
    assert utils.run_output_hook(_hook(cmd, timeout_seconds=1, grace_seconds=1), str(tmp_path / 'wrfout_x')) is False
    assert time.monotonic() - t0 < 8


def test_match_filters_by_basename(tmp_path, no_sentry):
    log = tmp_path / 'seen.txt'
    cmd = _script(tmp_path, 'hook.py', f'''
        import sys
        open({str(log)!r}, 'a').write(sys.argv[1] + chr(10))
    ''') + ' {path}'
    hook = _hook(cmd, match='wrfout_*')
    utils.run_output_hook(hook, str(tmp_path / 'wrfxtrm_d02_2026-09-19_00_00_00.nc'))
    utils.run_output_hook(hook, str(tmp_path / 'wrfout_d02_2026-09-19_00_00_00.nc'))
    assert log.read_text().splitlines() == [str(tmp_path / 'wrfout_d02_2026-09-19_00_00_00.nc')]


def test_timeout_sends_sigterm_first(tmp_path, no_sentry, capsys):
    """A hook that handles SIGTERM gets to unwind; the outcome names the timeout, not a kill."""
    log = tmp_path / 'unwound.txt'
    cmd = _script(tmp_path, 'hook.py', f'''
        import signal, sys, time
        def bye(signum, frame):
            open({str(log)!r}, 'w').write('released the lock')
            sys.exit(143)
        signal.signal(signal.SIGTERM, bye)
        time.sleep(30)
    ''') + ' {path}'
    assert utils.run_output_hook(_hook(cmd, timeout_seconds=1, grace_seconds=5), str(tmp_path / 'wrfout_x')) is False
    assert log.read_text() == 'released the lock'
    out = capsys.readouterr().out
    assert 'timed out after 1 s' in out and 'killed' not in out


def test_timeout_then_sigkill_when_sigterm_is_ignored(tmp_path, no_sentry, capsys):
    cmd = _script(tmp_path, 'hook.py', '''
        import signal, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(30)
    ''') + ' {path}'
    assert utils.run_output_hook(_hook(cmd, timeout_seconds=1, grace_seconds=1), str(tmp_path / 'wrfout_x')) is False
    assert 'ignored SIGTERM; killed' in capsys.readouterr().out


def test_failure_goes_to_sentry_as_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(params, 'is_sentry', True)
    captured = []
    monkeypatch.setattr(utils.sentry_sdk, 'capture_message', lambda msg, level=None: captured.append((msg, level)))
    cmd = _script(tmp_path, 'hook.py', 'import sys; sys.exit(1)') + ' {path}'
    utils.run_output_hook(_hook(cmd), str(tmp_path / 'wrfout_x'))
    assert len(captured) == 1 and captured[0][1] == 'warning' and 'exited 1' in captured[0][0]


# ---------------------------------------------------------------- ul_output_files wiring


def _fake_rclone(returncode):
    def fake(cmd_list, **kw):
        assert cmd_list[0] == 'rclone'
        return types.SimpleNamespace(returncode=returncode, stdout='', stderr='rclone said no' if returncode else '')
    return fake


def test_ul_output_files_hooks_each_file_before_deleting(tmp_path, monkeypatch, no_sentry):
    files = []
    for name in ('wrfout_d02_2026-09-19_00_00_00.nc', 'wrfxtrm_d02_2026-09-19_00_00_00.nc'):
        p = tmp_path / name
        p.write_text('x')
        files.append(str(p))
    log = tmp_path / 'seen.txt'
    cmd = _script(tmp_path, 'hook.py', f'''
        import os, sys
        open({str(log)!r}, 'a').write(f"{{os.path.basename(sys.argv[1])}} {{os.path.exists(sys.argv[1])}}" + chr(10))
    ''') + ' {path}'
    monkeypatch.setattr(params, 'output_hook', _hook(cmd, match='wrfout_*'))
    monkeypatch.setattr(utils.subprocess, 'run', _fake_rclone(0))
    utils.ul_output_files(files, str(tmp_path), 'output', '/out', str(tmp_path / 'rclone.config'))
    assert log.read_text().splitlines() == ['wrfout_d02_2026-09-19_00_00_00.nc True']
    assert not any(os.path.exists(f) for f in files)  # deleted after the hook, hooked or not


def test_ul_output_files_hook_failure_still_deletes(tmp_path, monkeypatch, no_sentry):
    p = tmp_path / 'wrfout_d02_2026-09-19_00_00_00.nc'
    p.write_text('x')
    monkeypatch.setattr(params, 'output_hook', _hook(_script(tmp_path, 'hook.py', 'import sys; sys.exit(2)') + ' {path}'))
    monkeypatch.setattr(utils.subprocess, 'run', _fake_rclone(0))
    utils.ul_output_files([str(p)], str(tmp_path), 'output', '/out', str(tmp_path / 'rclone.config'))
    assert not p.exists()


def test_no_hook_on_upload_failure_and_without_config(tmp_path, monkeypatch, no_sentry):
    p = tmp_path / 'wrfout_d02_2026-09-19_00_00_00.nc'
    p.write_text('x')
    log = tmp_path / 'seen.txt'
    cmd = _script(tmp_path, 'hook.py', f"open({str(log)!r}, 'w').write('ran')") + ' {path}'
    monkeypatch.setattr(params, 'output_hook', _hook(cmd))
    monkeypatch.setattr(utils.subprocess, 'run', _fake_rclone(1))
    utils.ul_output_files([str(p)], str(tmp_path), 'output', '/out', str(tmp_path / 'rclone.config'))
    assert p.exists() and not log.exists()  # upload failed: nothing hooked, nothing deleted
    monkeypatch.setattr(params, 'output_hook', None)
    monkeypatch.setattr(utils.subprocess, 'run', _fake_rclone(0))
    utils.ul_output_files([str(p)], str(tmp_path), 'output', '/out', str(tmp_path / 'rclone.config'))
    assert not p.exists() and not log.exists()  # no [hooks]: plain delete


# ---------------------------------------------------------------- params validation


def test_hook_config_validation():
    import params as p
    assert p.output_hook is None or '{path}' in p.output_hook['command']
    # the parser is the module-level block; exercise its rules on a dict the same way
    with pytest.raises(ValueError, match='placeholder'):
        p._parse_hooks({'on_output_file': 'wrf-fc ingest-file'})
    with pytest.raises(ValueError, match='timeout_seconds'):
        p._parse_hooks({'on_output_file': 'x {path}', 'timeout_seconds': 0})
    assert p._parse_hooks({}) is None
    assert p._parse_hooks({'on_output_file': 'x {path}'}) == {
        'command': 'x {path}', 'match': '*', 'timeout_seconds': 900, 'grace_seconds': 60, 'delete_on_success': True}


# ---------------------------------------------------------------- hook-only delivery (no [remote.output])


def _ok_hook(tmp_path, log):
    return _hook(_script(tmp_path, 'hook.py', f"import os, sys; open({str(log)!r}, 'a').write(os.path.basename(sys.argv[1]) + chr(10))") + ' {path}',
                 match='wrfout_*')


def test_hook_output_files_consumes_matching_files_and_keeps_the_rest(tmp_path, monkeypatch, no_sentry):
    names = ['wrfout_d02_2026-09-19_00_00_00.nc', 'wrfxtrm_d02_2026-09-19_00_00_00.nc']
    files = [str(tmp_path / n) for n in names]
    for f in files:
        open(f, 'w').write('x')
    log = tmp_path / 'seen.txt'
    monkeypatch.setattr(params, 'output_hook', _ok_hook(tmp_path, log))
    kept = utils.hook_output_files(files)
    assert log.read_text().splitlines() == [names[0]]
    assert not os.path.exists(files[0]) and os.path.exists(files[1])  # consumed vs not the hook's
    assert kept == [files[1]]


def test_hook_output_files_keeps_a_file_whose_hook_failed(tmp_path, monkeypatch, no_sentry):
    f = str(tmp_path / 'wrfout_d02_2026-09-19_00_00_00.nc')
    open(f, 'w').write('x')
    monkeypatch.setattr(params, 'output_hook', _hook(_script(tmp_path, 'hook.py', 'import sys; sys.exit(1)') + ' {path}'))
    assert utils.hook_output_files([f]) == [f] and os.path.exists(f)
    monkeypatch.setattr(params, 'output_hook', _hook(_script(tmp_path, 'ok.py', 'pass') + ' {path}', delete_on_success=False))
    assert utils.hook_output_files([f]) == [f] and os.path.exists(f)  # delete_on_success=false keeps it too


def test_deliver_output_files_routes_by_configuration(tmp_path, monkeypatch, no_sentry):
    """monitor_wrf.deliver_output_files: remote -> upload path; hook only -> hook path; neither -> untouched."""
    import monitor_wrf
    f = str(tmp_path / 'wrfout_d02_2026-09-19_00:00:00')
    open(f, 'w').write('x')
    monkeypatch.setattr(params, 'output_variables', [])
    log = tmp_path / 'seen.txt'
    # neither: file stays, under its on-disk name
    monkeypatch.setattr(params, 'output_hook', None)
    monitor_wrf.deliver_output_files([f], str(tmp_path), {':': '_'}, None, None)
    assert os.path.exists(f)
    # hook only: renamed (colon rule) then hooked then deleted
    monkeypatch.setattr(params, 'output_hook', _ok_hook(tmp_path, log))
    monitor_wrf.deliver_output_files([f], str(tmp_path), {':': '_'}, None, None)
    assert log.read_text().strip() == 'wrfout_d02_2026-09-19_00_00_00'
    assert not os.path.exists(f) and not (tmp_path / 'wrfout_d02_2026-09-19_00_00_00').exists()
    # remote: goes through ul_output_files (rclone faked)
    g = str(tmp_path / 'wrfout_d02_2026-09-20_00:00:00')
    open(g, 'w').write('x')
    calls = []
    monkeypatch.setattr(utils, 'ul_output_files', lambda files, *a: calls.append(files))
    monitor_wrf.deliver_output_files([g], str(tmp_path), {':': '_'}, 'output', '/out')
    assert calls == [[str(tmp_path / 'wrfout_d02_2026-09-20_00_00_00')]]


def test_failed_file_is_not_rehooked_every_poll_but_once_post_run(tmp_path, monkeypatch, no_sentry):
    """Review ifs-forecast-cycle-code-2: a persistently failing hook must not block every 60 s poll."""
    f = str(tmp_path / 'wrfout_d02_2026-09-19_00_00_00.nc')
    open(f, 'w').write('x')
    log = tmp_path / 'calls.txt'
    cmd = _script(tmp_path, 'hook.py', f"open({str(log)!r}, 'a').write('x'); import sys; sys.exit(1)") + ' {path}'
    monkeypatch.setattr(params, 'output_hook', _hook(cmd))
    monkeypatch.setattr(utils, '_hook_failed', set())
    for _ in range(3):  # three polls
        assert utils.hook_output_files([f]) == [f]
    assert log.read_text() == 'x'  # hooked once, then skipped
    assert utils.hook_output_files([f], retry_failed=True) == [f]  # the post-run pass retries once
    assert log.read_text() == 'xx' and os.path.exists(f)
