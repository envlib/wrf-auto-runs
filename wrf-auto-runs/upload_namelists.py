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

import pendulum

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
    # --copy-links: follow symlinks. namelist.input/wps and trmask_d* in run_path are symlinks
    # to data_path (created by run_real); without -L, rclone silently skips them.
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
        f'--include "wrfrst_d*" '
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


def parse_wrfrst_timestamp(file_name):
    """Parse YYYY-MM-DD_HH:MM:SS out of a wrfrst_d<NN>_<TIMESTAMP> filename. Returns naive pendulum datetime."""
    # Format: wrfrst_d01_2023-01-02_00:00:00 — split on underscores, last two parts are <DATE>_<TIME>
    base = pathlib.Path(file_name).stem if '.' in file_name else file_name
    parts = base.split('_')
    if len(parts) < 4:
        raise ValueError(f'Unexpected wrfrst filename format: {file_name}')
    ts_str = f'{parts[-2]}_{parts[-1]}'  # YYYY-MM-DD_HH:MM:SS
    # Naive datetime to match set_nml_params() return values used elsewhere in the pipeline.
    return pendulum.from_format(ts_str, 'YYYY-MM-DD_HH:mm:ss').naive()


def detect_restart_state():
    """Scan params.run_path for wrfrst_d* files. Return latest pendulum timestamp, or None if none present."""
    wrfrst_files = sorted(params.run_path.glob('wrfrst_d*'))
    if not wrfrst_files:
        return None
    timestamps = [parse_wrfrst_timestamp(f.name) for f in wrfrst_files]
    return max(timestamps)


def upload_wrfrst(run_uuid, file_paths):
    """Upload a list of local wrfrst files to inputs/<run_uuid>/. No rename — kept in internal d01 numbering."""
    if not file_paths:
        return
    resolved = _resolve_remote_output()
    if resolved is None:
        return
    out_path, name, config_path = resolved

    dest_str = f'{name}:{out_path}/inputs/{run_uuid}/'
    # Use --files-from-raw with the basenames; rclone copy resolves relative to the source dir.
    files_from = '\n'.join(pathlib.Path(p).name for p in file_paths)
    cmd_str = (
        f'rclone copy {params.run_path} {dest_str} '
        f'--config={config_path} --no-traverse --no-check-dest --copy-links '
        f'--files-from-raw - --transfers=4'
    )
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, input=files_from, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise ValueError(f'upload_wrfrst failed:\n{p.stderr}')
    return True


def cleanup_prior_wrfrst(run_uuid, keep_timestamp):
    """Delete wrfrst_d* on S3 with timestamp < keep_timestamp. Best-effort — logs but doesn't raise."""
    resolved = _resolve_remote_output()
    if resolved is None:
        return
    out_path, name, config_path = resolved

    list_cmd = f'rclone lsf {name}:{out_path}/inputs/{run_uuid}/ --include "wrfrst_d*" --config={config_path}'
    p = subprocess.run(shlex.split(list_cmd), capture_output=True, text=True, check=False)
    if p.returncode != 0:
        print(f'-- cleanup_prior_wrfrst: lsf failed ({p.returncode}): {p.stderr.strip()}')
        return

    for line in p.stdout.splitlines():
        fname = line.strip()
        if not fname:
            continue
        try:
            ts = parse_wrfrst_timestamp(fname)
        except ValueError:
            continue
        if ts < keep_timestamp:
            del_cmd = f'rclone deletefile {name}:{out_path}/inputs/{run_uuid}/{fname} --config={config_path}'
            dp = subprocess.run(shlex.split(del_cmd), capture_output=True, text=True, check=False)
            if dp.returncode != 0:
                print(f'-- cleanup_prior_wrfrst: warning, failed to delete {fname}: {dp.stderr.strip()}')
            else:
                print(f'-- cleanup_prior_wrfrst: deleted {fname} (older than {keep_timestamp})')


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
