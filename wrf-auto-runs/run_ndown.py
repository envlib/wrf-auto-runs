#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Oct  6 10:40:23 2025

@author: mike
"""
import os
import pathlib
import shlex
import subprocess
import pendulum
import shutil
import copy
import f90nml

import params
import utils

############################################
### Parameters


###########################################
### Functions


def _promote_single_target():
    """Single-ndown post-success: collapse the two-domain run down to one.

    Deletes d01 files (the coarse parent no longer needed) and promotes the
    ndown-produced d02 wrfinput/wrfbdy into the d01 slot for the downstream
    standalone wrf.exe run.
    """
    for file_path in params.run_path.glob('*_d01'):
        file_path.unlink()

    for file_path in params.run_path.glob('*_d02'):
        file_part = file_path.name.split('_')[0]
        new_file = file_part + '_d01'
        new_file_path = params.run_path.joinpath(new_file)
        os.rename(file_path, new_file_path)


def _promote_after_nested_ndown(post_n_domains):
    """Nested-run post-success: drop the coarse parent and shift every other
    domain down by one slot so the ndown target becomes d01 and its nested
    children become d02..dN.

    Must process in ascending domain order — free slot k before slot k+1
    moves into it, otherwise os.rename silently overwrites on POSIX.

    `post_n_domains` is the count of domains in the post-ndown nested wrf.exe
    run (e.g., 2 for [3km, 1km]); inputs from real.exe were produced for
    domains 1..(post_n_domains + 1) since real ran with the parent + nest chain.
    """
    # Slot 1: delete the coarse-parent (d01) inputs. They served their purpose
    # as ndown's coarse data source via wrfout_d01_*.nc (which we leave on disk
    # for inspection — they're cleaned up at chunk teardown).
    for stem in ('wrfinput_d01', 'wrfbdy_d01'):
        p = params.run_path.joinpath(stem)
        if p.exists():
            p.unlink()
    geo_em_d01 = params.data_path.joinpath('geo_em.d01.nc')
    if geo_em_d01.exists():
        geo_em_d01.unlink()

    # Slots 2..(post_n_domains+1) → 1..post_n_domains, in ascending order.
    # wrfbdy only exists for what will be the new d01 (originally d02 — ndown
    # produced wrfbdy_d02). Inner-nest domains read their BCs from the parent
    # at run time via two-way nesting, not a wrfbdy file.
    for new_id in range(1, post_n_domains + 1):
        old_id = new_id + 1
        for stem in ('wrfinput', 'wrfbdy'):
            src = params.run_path.joinpath(f'{stem}_d{old_id:02d}')
            dst = params.run_path.joinpath(f'{stem}_d{new_id:02d}')
            if src.exists():
                os.rename(src, dst)
        geo_em_src = params.data_path.joinpath(f'geo_em.d{old_id:02d}.nc')
        geo_em_dst = params.data_path.joinpath(f'geo_em.d{new_id:02d}.nc')
        if geo_em_src.exists():
            os.rename(geo_em_src, geo_em_dst)


def run_ndown(run_uuid, mode="single", post_n_domains=None, del_old=True):
    """Run ndown.exe and promote its output into the wrf.exe input slot(s).

    mode
    ----
    "single"      : after success, collapse to a single-domain run (existing).
    "nested-run"  : after success, shift coarse-parent out and renumber the
                    ndown target + its nested children down by one slot, so
                    wrf.exe can run them as a single nested simulation.
    """
    if mode == "nested-run" and not post_n_domains:
        raise ValueError("nested-run mode requires post_n_domains (count of post-ndown nested domains)")

    ## Prep files
    os.rename(params.run_path.joinpath('wrfinput_d02'), params.run_path.joinpath('wrfndi_d02'))

    wrf_nml = f90nml.read(params.wrf_nml_path)
    wrf_nml['time_control']['io_form_auxinput2'] = 2
    wrf_nml['time_control']['fine_input_stream'] = [0, 2] # Is this needed?
    ndown_interval = wrf_nml['time_control']['history_interval'][0] * 60
    wrf_nml['time_control']['interval_seconds'] = ndown_interval

    with open(params.wrf_nml_path, 'w') as nml_file:
       wrf_nml.write(nml_file)

    cmd_str = f'mpirun -n {params.n_cores_preprocess} --map-by core {params.ndown_exe}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=False, text=False, check=False, cwd=params.run_path)

    real_log_path = params.run_path.joinpath('rsl.out.0000')
    with open(real_log_path, 'rt') as f:
        f.seek(0, os.SEEK_END)
        f.seek(f.tell() - 40, os.SEEK_SET)
        results_str = f.read()

    if 'SUCCESS COMPLETE NDOWN_EM INIT' in results_str:
        if del_old:
            for path in params.run_path.glob('wrfout_*.nc'):
                path.unlink()

            params.run_path.joinpath('wrfndi_d02').unlink()

        if mode == "single":
            _promote_single_target()
        else:
            _promote_after_nested_ndown(post_n_domains)

        return ndown_interval
    else:
        # scope = sentry_sdk.get_current_scope()
        # scope.add_attachment(path=real_log_path)

        if params.is_remote_output:
            remote = copy.deepcopy(params.file['remote']['output'])

            name = 'output'

            if 'path' in remote:
                out_path = pathlib.Path(remote.pop('path'))
                # Register the 'output' remote in the rclone config (single-stage path
                # doesn't otherwise create it; see monitor_wrf.py for the same fix).
                utils.create_rclone_config(name, params.data_path, remote)
            else:
                out_path = None

            if out_path is not None:
                print(f'-- Uploading ndown.exe log files for run uuid: {run_uuid}')
                dest_str = f'{name}:{out_path}/logs/{run_uuid}/'
                cmd_str = f'rclone copy {params.run_path} {dest_str} --config={params.config_path} --include "rsl.*" --transfers=8'
                cmd_list = shlex.split(cmd_str)
                p = subprocess.run(cmd_list, capture_output=True, text=True, check=True)

        raise ValueError(f'ndown.exe failed. Look at the logs for details: {results_str}')




