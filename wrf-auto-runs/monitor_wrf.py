#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 15:41:48 2025

@author: mike
"""
import os
import pathlib
import resource
import shlex
import subprocess
import copy
import sentry_sdk
from time import sleep

import params
import utils
from upload_namelists import upload_wrfrst, cleanup_prior_wrfrst, parse_wrfrst_timestamp

############################################
### Parameters



# out_files_glob = {'wrfout': 'wrfout_d*',
#                   'zlevel': 'wrfzlevels_d*',
#                   'summ': 'wrfxtrm_d*',
#                   }

###########################################
### Functions


def monitor_wrf(outputs, end_date, run_uuid, rename_dict, chunk_end=None):
    """

    """
    if params.is_remote_output:
        remote = copy.deepcopy(params.file['remote']['output'])

        name = 'output'

        if 'path' in remote:
            out_path = pathlib.Path(remote.pop('path'))
            # Register the 'output' remote in the rclone config file. Without this, the
            # single-stage pipeline never creates the [output] section (only the chunked
            # pipeline does, via upload_namelists._resolve_remote_output), so rclone fails
            # with "didn't find section in config file".
            utils.create_rclone_config(name, params.data_path, remote)
        else:
            out_path = None
    else:
        out_path = None
        name = None

    # output_globs = [out_files_glob[op] for op in outputs]

    run_path = params.run_path

    n_cores = params.file['n_cores']

    resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    cmd_str = f'mpirun -np {n_cores} ./wrf.exe'
    cmd_list = shlex.split(cmd_str)
    import time as _time
    wrf_start_time = _time.time()
    p = subprocess.Popen(cmd_list, cwd=run_path)

    check = p.poll()
    while check is None:
        # Glob mode (out_files=None) — accept any wrfout/wrfxtrm/wrfzlevels file regardless of
        # timestamp. Necessary for restart chunks where WRF's first wrfout is offset from
        # chunk_start by history_interval (e.g. a Feb13 chunk's first file is named ..._03:00:00).
        # include_xtrm=True: pick up wrfxtrm progressively during the run instead of holding
        # them all until WRF exits — otherwise a 28-day chunk's wrfxtrm files sit on disk for
        # the entire wallclock and are lost if anything kills the container mid-chunk.
        files = utils.query_out_files(run_path, include_xtrm=True)

        # wrfxtrm_skip_newest=True: WRF is still writing the most recent wrfxtrm file; uploading
        # and deleting it would yank the file out from under WRF mid-write.
        files = utils.select_files_to_ul(files, 1, wrfxtrm_skip_newest=True)

        if files and out_path is not None:
            if params.output_variables:
                print('- wrfout variables will be filtered based on the output_variables.')
                utils.filter_variables(files, params.output_variables)
            files = utils.rename_files(files, rename_dict)
            utils.ul_output_files(files, run_path, name, out_path, params.config_path)

        # wrfrst polling — upload all but the newest per domain (newest may be in-progress).
        # Mirrors the select_files_to_ul(min_files=1) pattern used for wrfout.
        # min_mtime gates out the pre-existing restart-seed wrfrst (downloaded before wrf.exe
        # launched on chunks 2+) so we don't re-upload it.
        if out_path is not None:
            _upload_stable_wrfrst(run_path, run_uuid, keep_newest=True, min_mtime=wrf_start_time)

        sleep(60)
        check = p.poll()

    wrf_log_path = run_path.joinpath('rsl.out.0000')
    results_str = utils.read_last_line(wrf_log_path)

    if 'SUCCESS COMPLETE WRF' in results_str:
        # Glob mode — see comment in the poll loop above.
        files = utils.query_out_files(run_path, include_xtrm=True)

        # Skip the chunk_end single-frame wrfout when the chunk's actual end falls exactly at
        # midnight (00:00:00). Such a file is a "deceptive partial day" — same filename pattern
        # as a new day file but contains only the rollover frame. Either (a) the next chunk
        # clobbers it with a full 8-frame version on restart, or (b) it's the final chunk and
        # the rollover frame is also captured in wrfrst. When the chunk end is mid-day (e.g.
        # end_date=2023-02-15 03:00:00), the final wrfout file contains multiple frames of
        # legitimate end-of-simulation data, so upload it.
        effective_end = chunk_end if chunk_end is not None else end_date
        skip_chunk_end_partial = (
            effective_end.hour == 0 and effective_end.minute == 0 and effective_end.second == 0
        )
        min_files = 1 if skip_chunk_end_partial else 0
        files = utils.select_files_to_ul(files, min_files)

        if files and out_path is not None:
            if params.output_variables:
                print('- wrfout variables will be filtered based on the output_variables.')
                utils.filter_variables(files, params.output_variables)
            files = utils.rename_files(files, rename_dict)
            utils.ul_output_files(files, run_path, name, out_path, params.config_path)

        # Final wrfrst upload — wrf has exited cleanly so all wrfrst files are complete.
        # min_mtime still gates out the pre-existing restart-seed wrfrst.
        if out_path is not None:
            _upload_stable_wrfrst(run_path, run_uuid, keep_newest=False, min_mtime=wrf_start_time)

        return True
    else:
        cmd_str = 'grep cfl rsl.error*'
        cmd_list = shlex.split(cmd_str)
        pe = subprocess.run(cmd_list, capture_output=True, text=True, cwd=run_path)
        if pe.stdout != '':
            results_str = pe.stdout
        # scope = sentry_sdk.get_current_scope()
        # scope.add_attachment(path=wrf_log_path)
        if out_path is not None:
            print(f'-- Uploading WRF log files for run uuid: {run_uuid}')
            dest_str = f'{name}:{out_path}/logs/{run_uuid}/'
            cmd_str = f'rclone copy {params.run_path} {dest_str} --no-check-dest --config={params.config_path} --include "rsl.*" --transfers=8'
            cmd_list = shlex.split(cmd_str)
            p = subprocess.run(cmd_list, capture_output=True, text=True, check=True)

        raise ValueError(f'wrf.exe failed. Look at the logs for details: {results_str}')


_WRFRST_MTIME_STABLE_SECONDS = 60


def _upload_stable_wrfrst(run_path, run_uuid, keep_newest, min_mtime=0):
    """Upload wrfrst files from run_path to inputs/<run_uuid>/, then cleanup older ones on S3.

    keep_newest=True (poll loop): per domain, upload (a) any older wrfrst (definitely complete
        because a newer one exists) AND (b) the newest wrfrst if its mtime has been stable for
        at least _WRFRST_MTIME_STABLE_SECONDS — the write is finished. Without the mtime check,
        a single wrfrst with no newer successor would sit locally until the next restart_interval
        (potentially hours/days of wallclock).
    keep_newest=False (post-loop after wrf SUCCESS): upload everything — wrf has exited so all
        are complete; no stability check needed.
    min_mtime: float (epoch seconds). Skip any wrfrst whose mtime is older than this threshold —
        used to filter out pre-existing restart-seed wrfrst (downloaded into run_path before
        wrf.exe started). Pass `wrf_start_time` to gate out the seed file on chunks 2+.

    After uploading, deletes prior wrfrst files from S3 (keeping only the latest) and removes
    uploaded files from local disk. Does nothing if no wrfrst files are present.
    """
    import time

    wrfrst_local = sorted(run_path.glob('wrfrst_d*'))
    if not wrfrst_local:
        return

    # Filter out seed files (mtime < min_mtime) — these are pre-existing restart inputs, not
    # outputs of the current wrf.exe run. Don't re-upload them.
    if min_mtime > 0:
        wrfrst_local = [f for f in wrfrst_local if f.stat().st_mtime >= min_mtime]
        if not wrfrst_local:
            return

    by_domain = {}
    for f in wrfrst_local:
        # filename: wrfrst_d<NN>_<TIMESTAMP> — second underscore-separated token is the domain id
        parts = f.name.split('_')
        if len(parts) < 4:
            continue
        domain = parts[1]
        by_domain.setdefault(domain, []).append(f)

    now = time.time()
    to_upload = []
    for domain, files in by_domain.items():
        files.sort()
        if keep_newest:
            # All but the newest are guaranteed complete (a newer one exists).
            to_upload.extend(files[:-1])
            # The newest is also complete if its mtime has been stable for the threshold.
            newest = files[-1]
            try:
                age = now - newest.stat().st_mtime
            except FileNotFoundError:
                age = 0
            if age >= _WRFRST_MTIME_STABLE_SECONDS:
                to_upload.append(newest)
        else:
            to_upload.extend(files)

    if not to_upload:
        return

    upload_wrfrst(run_uuid, to_upload)
    latest_ts = max(parse_wrfrst_timestamp(f.name) for f in to_upload)
    cleanup_prior_wrfrst(run_uuid, latest_ts)
    for f in to_upload:
        try:
            f.unlink()
        except FileNotFoundError:
            pass















































