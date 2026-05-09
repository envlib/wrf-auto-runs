#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S3 helpers for the per-run "inputs" prefix used by the unified chunked pipeline.

The inputs/<run_uuid>/ prefix holds the per-chunk namelist archive and the
wrfrst handoff files between chunks. namelists are uploaded via
upload_chunk_namelists; wrfrst files via upload_wrfrst; chunk-position
detection via detect_remote_restart_state; restart download via
download_wrfrst_to_run_path.
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


def upload_wrfrst(run_uuid, file_paths):
    """Upload a list of local wrfrst files to inputs/<run_uuid>/. No rename — kept in internal d01 numbering."""
    if not file_paths:
        return
    resolved = _resolve_remote_output()
    if resolved is None:
        return
    out_path, name, config_path = resolved

    files_str = '\n'.join(pathlib.Path(p).name for p in file_paths)
    print(f'-- Uploading wrfrst files:\n{files_str}')

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


def upload_chunk_namelists(run_uuid):
    """Upload ONLY namelist.input + namelist.wps to inputs/<run_uuid>/ for archival.

    The wrf*input/bdy/fdda/lowinp/trmask files are local-only (regenerated each chunk),
    so they're not uploaded.
    """
    resolved = _resolve_remote_output()
    if resolved is None:
        return
    out_path, name, config_path = resolved

    files_from = "namelist.input\nnamelist.wps"
    dest_str = f'{name}:{out_path}/inputs/{run_uuid}/'
    cmd_str = (
        f'rclone copy {params.run_path} {dest_str} '
        f'--config={config_path} --no-traverse --no-check-dest --copy-links '
        f'--files-from -'
    )
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, input=files_from, capture_output=True, text=True, check=False)

    if p.returncode != 0:
        raise ValueError(f'upload_chunk_namelists failed:\n{p.stderr}')
    return True


def detect_remote_restart_state(run_uuid):
    """Phase 3: lightweight S3 metadata read. Returns latest wrfrst timestamp on S3 or None.

    Used to decide chunk boundaries before kicking off preprocess — no file download.
    """
    resolved = _resolve_remote_output()
    if resolved is None:
        return None
    out_path, name, config_path = resolved

    list_cmd = f'rclone lsf {name}:{out_path}/inputs/{run_uuid}/ --include "wrfrst_d*" --config={config_path}'
    p = subprocess.run(shlex.split(list_cmd), capture_output=True, text=True, check=False)
    if p.returncode != 0:
        # Prefix may not exist yet (cold start); rclone lsf returns non-zero. Treat as no wrfrst.
        return None

    timestamps = []
    for line in p.stdout.splitlines():
        fname = line.strip()
        if not fname:
            continue
        try:
            timestamps.append(parse_wrfrst_timestamp(fname))
        except ValueError:
            continue

    return max(timestamps) if timestamps else None


def download_wrfrst_to_run_path(run_uuid):
    """Phase 3: download wrfrst_d* files from inputs/<run_uuid>/ on S3 into params.run_path.

    Called AFTER run_real (which rmtree's run_path). Puts the latest wrfrst back where wrf.exe
    expects it, so the chunk's wrf.exe can resume from where the prior chunk left off.
    """
    resolved = _resolve_remote_output()
    if resolved is None:
        raise ValueError('Phase 3 unified mode requires [remote.output] to be configured.')
    out_path, name, config_path = resolved

    if not params.run_path.exists():
        params.run_path.mkdir(parents=True)

    src_str = f'{name}:{out_path}/inputs/{run_uuid}/'
    cmd_str = (
        f'rclone copy {src_str} {params.run_path} '
        f'--config={config_path} --no-check-dest '
        f'--include "wrfrst_d*" --transfers=4'
    )
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise ValueError(f'download_wrfrst_to_run_path failed:\n{p.stderr}')

    if not any(params.run_path.glob('wrfrst_d*')):
        raise ValueError(f'No wrfrst_d* downloaded from {src_str} — check that the prior chunk completed.')
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


