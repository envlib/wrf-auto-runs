#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Upload / download / cleanup helpers for the per-run "inputs" prefix on S3.

The inputs/<run_uuid>/ prefix holds everything the WRF stage needs to start
running wrf.exe: namelist.input, namelist.wps (archival), wrfinput_d*,
wrfbdy_d*, trmask_d*, and optionally wrffdda_d* / wrflowinp_d*. The preprocess
stage uploads all of these in one shot via upload_run_inputs after real.exe
completes; the wrf_only stage downloads them via download_run_inputs;
cleanup_run_inputs purges the prefix after a successful WRF run when
params.cleanup_inputs is True.
"""
import pathlib
import shlex
import subprocess
import copy

import params, utils


def _resolve_remote_output():
    """Return (out_path, name, config_path) or None if remote.output isn't configured."""
    if not params.is_remote_output:
        return None

    remote = copy.deepcopy(params.file['remote']['output'])
    if 'path' not in remote:
        return None

    out_path = pathlib.Path(remote.pop('path'))
    name = 'output'
    config_path = utils.create_rclone_config(name, params.data_path, remote)
    return out_path, name, config_path


def upload_run_inputs(run_uuid):
    """Upload everything the WRF stage needs (namelist.input/wps, wrf*_d*, trmask_d*) to inputs/<run_uuid>/."""
    resolved = _resolve_remote_output()
    if resolved is None:
        return
    out_path, name, config_path = resolved

    dest_str = f'{name}:{out_path}/inputs/{run_uuid}/'
    # --copy-links: follow symlinks. trmask_d* in run_path are symlinks to ../trmask_d*
    # (created by run_real); without -L, rclone silently skips them.
    cmd_str = (
        f'rclone copy {params.run_path} {dest_str} '
        f'--config={config_path} --no-traverse --no-check-dest --copy-links '
        f'--include "wrfinput_d*" --include "wrfbdy_d*" '
        f'--include "wrffdda_d*" --include "wrflowinp_d*" '
        f'--include "trmask_d*" '
        f'--include "namelist.input" --include "namelist.wps" --transfers=4'
    )
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False)

    if p.returncode != 0:
        raise ValueError(p.stderr)
    return True


def download_run_inputs(run_uuid):
    """Download namelist.input + wrf*_d* + trmask_d* from inputs/<run_uuid>/ into params.run_path.

    Sets up params.run_path with the same layout run_real produces (symlinks to /WRF/run/*),
    minus the met_em symlinks (real.exe is the only consumer of those — wrf.exe doesn't read
    met_em, but it does read trmask_d* directly for WVT runs).
    """
    resolved = _resolve_remote_output()
    if resolved is None:
        raise ValueError('wrf_only mode requires [remote.output] to be configured in parameters.toml.')
    out_path, name, config_path = resolved

    if params.run_path.exists():
        import shutil
        shutil.rmtree(params.run_path)
    params.run_path.mkdir(parents=True)

    wrf_run_path = params.wrf_path.joinpath('run')
    cmd_str = f'ln -sf {wrf_run_path}/* .'
    subprocess.run(cmd_str, shell=True, capture_output=False, text=False, check=False, cwd=params.run_path)

    # Remove the namelist.input symlink that came in via /WRF/run/* — we'll get the real one from S3.
    nml_in_run = params.run_path.joinpath('namelist.input')
    if nml_in_run.exists() or nml_in_run.is_symlink():
        nml_in_run.unlink()

    src_str = f'{name}:{out_path}/inputs/{run_uuid}/'
    cmd_str = (
        f'rclone copy {src_str} {params.run_path} '
        f'--config={config_path} --no-check-dest '
        f'--include "wrfinput_d*" --include "wrfbdy_d*" '
        f'--include "wrffdda_d*" --include "wrflowinp_d*" '
        f'--include "trmask_d*" '
        f'--include "namelist.input" --transfers=4'
    )
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False)

    if p.returncode != 0:
        raise ValueError(f'download_run_inputs failed:\n{p.stderr}')

    # Sanity check — wrfinput_d01 must be present, otherwise wrf.exe will crash with a confusing error.
    if not any(params.run_path.glob('wrfinput_d*')):
        raise ValueError(f'No wrfinput_d* files downloaded from {src_str} — preprocess stage may not have uploaded them.')

    return True


def cleanup_run_inputs(run_uuid):
    """Purge inputs/<run_uuid>/ from S3. Best-effort; failures are logged, not raised."""
    if not params.cleanup_inputs:
        return
    resolved = _resolve_remote_output()
    if resolved is None:
        return
    out_path, name, config_path = resolved

    dest_str = f'{name}:{out_path}/inputs/{run_uuid}/'
    cmd_str = f'rclone purge {dest_str} --config={config_path}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False)

    if p.returncode != 0:
        print(f'-- cleanup_run_inputs: warning, rclone purge returned {p.returncode}: {p.stderr.strip()}')
    return p.returncode == 0
