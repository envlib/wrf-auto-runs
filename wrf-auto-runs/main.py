#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep 23 15:09:38 2025

@author: mike
"""
import os
import uuid

import pendulum
import sentry_sdk

# from download_nml_domain import dl_nml_domain
from set_params import check_nml_params, set_nml_params, set_ndown_params, update_metgrid_levels, apply_restart_namelist
from download_era5 import dl_era5
from run_era5_to_int import run_era5_to_int
from process_sst_cci import process_sst_cci
from download_wrf import dl_wrf
from run_wrf_to_int import run_wrf_to_int
from run_metgrid import run_metgrid
from run_real import run_real
from monitor_wrf import monitor_wrf
from upload_namelists import (
    upload_chunk_namelists, detect_remote_restart_state, download_wrfrst_to_run_path,
)
from check_ndown import check_ndown_params
from run_geogrid import run_geogrid
from run_ndown import run_ndown
from download_ndown_input import dl_ndown_input
from create_trmask import create_trmask

import params
import utils

run_uuid = (
    os.environ.get('run_uuid')
    or params.file.get('run_uuid')
    or uuid.uuid4().hex[-13:]
)

########################################
## Sentry

if params.is_sentry:
    sentry = params.file['sentry']

    if sentry['dsn'] != '':
        sentry_sdk.init(
            dsn=sentry['dsn'],
            # Add data like request headers and IP for users,
            # see https://docs.sentry.io/platforms/python/data-management/data-collected/ for more info
            send_default_pii=True,
        )

    if sentry['tags']:
        sentry_sdk.set_tags(sentry['tags'])

    sentry_sdk.set_tags({'run_uuid': run_uuid})


########################################
### Helpers


def _resolve_domains():
    """Read [domains].run from parameters.toml — mirrors the inline logic in the main pipeline."""
    if 'run' in params.file.get('domains', {}):
        domains = params.file['domains']['run']
        if isinstance(domains, int):
            domains = [domains]
        elif isinstance(domains, list):
            for domain in domains:
                if not isinstance(domain, int):
                    raise ValueError('domains must be a list of int.')
        else:
            raise ValueError('domains must be a list of int.')
    else:
        domains = None
    return domains


def _build_rename_dict(ndown_check, domains):
    """Mirror main.py's per-domain rename map (post-pipeline lines that build rename_dict)."""
    if ndown_check:
        return {'_d01_': f'_d{domains[-1]:02d}_'}
    rename_dict = {}
    for i, domain in enumerate(domains):
        rename_dict[f'_d{i+1:02d}_'] = f'_d{domain:02d}_'
    return rename_dict


def _read_sim_window():
    """Read original simulation start_date/end_date from parameters.toml (before any chunk mutation)."""
    sim_start = pendulum.parse(params.file['time_control']['start_date']).naive()
    if 'end_date' in params.file['time_control']:
        sim_end = pendulum.parse(params.file['time_control']['end_date']).naive()
    elif 'duration_hours' in params.file['time_control']:
        sim_end = sim_start.add(hours=params.file['time_control']['duration_hours'])
    else:
        raise ValueError('parameters.toml [time_control] must specify end_date or duration_hours')
    return sim_start, sim_end


def run_chunked_pipeline(run_uuid):
    """Phase 3 unified per-chunk mode: preprocess + WRF for one chunk per iteration.

    Loops chunks internally if stop_after_upload=false. Exits after one chunk if true
    (caller — typically a SLURM-submitted container — handles repeated invocations).
    """
    sim_start_user, sim_end = _read_sim_window()
    # Real WRF start is begin_hours before the user's desired output start: WRF integrates
    # the spin-up period and history_begin_h_<n> suppresses wrfout for that span. Chunk math
    # operates on the real WRF window so spin-up gets its own chunks instead of being
    # silently bolted onto chunk 1.
    sim_start = sim_start_user.subtract(hours=params._original_begin_hours)
    if params._original_begin_hours > 0:
        print(f'-- simulation window: {sim_start} → {sim_end} '
              f'(incl. {params._original_begin_hours}h spin-up before user start_date {sim_start_user})')
    else:
        print(f'-- simulation window: {sim_start} → {sim_end}')

    chunk_n = 0
    while True:
        chunk_n += 1
        # Lightweight S3 metadata check — no file download. Returns latest wrfrst timestamp or None.
        restart_state = detect_remote_restart_state(run_uuid)
        chunk_start = restart_state if restart_state is not None else sim_start
        if chunk_start >= sim_end:
            print(f'-- chunk_start {chunk_start} >= sim_end {sim_end}; simulation complete, exiting.')
            return

        chunk_end = min(chunk_start.add(days=params.restart_interval_days), sim_end)
        elapsed_h = int((chunk_start - sim_start).total_seconds() / 3600)
        remaining_begin_h = max(0, params._original_begin_hours - elapsed_h)
        print(f'-- chunk #{chunk_n}: {chunk_start} → {chunk_end}'
              + (f' (restart from {restart_state})' if restart_state is not None else ' (cold start)')
              + (f' [spin-up remaining: {remaining_begin_h}h]' if remaining_begin_h > 0 else ''))

        # Mutate params.file in-place so the existing pipeline reads chunk-specific dates and
        # remaining begin_hours. _chunked_mode_active is set as a side effect, which gates
        # set_params.set_nml_params from double-subtracting begin_hours from start_date.
        params.set_chunk_dates(chunk_start, chunk_end, remaining_begin_h)

        # ---- Preprocess (existing pipeline functions, scoped to this chunk's window) ----
        domains = _resolve_domains()
        ndown_check, domains_init = check_ndown_params(domains)
        src_n_domains, domains = check_nml_params(domains)
        if domains_init is None:
            domains_init = list(domains)

        # First set_nml_params pass (matches existing pipeline)
        if domains_init[0] == 1 and all([d - i == 1 for i, d in enumerate(domains_init)]):
            _ = set_nml_params(domains_init)
        else:
            _ = set_nml_params()

        print('-- Run geogrid.exe...')
        min_lon, min_lat, max_lon, max_lat = run_geogrid(src_n_domains, domains_init)

        # Second set_nml_params pass — returns chunk's start/end/outputs
        start_date, end_date, hour_interval, outputs = set_nml_params(domains_init)

        if params.file.get('dynamics', {}).get('tracer_opt', 0) == 4:
            print('-- Creating WVT tracer mask files...')
            create_trmask(domains_init, start_date)

        if params.is_wrf_input:
            print('-- Downloading WRF data...')
            dl_wrf(start_date, end_date)
            utils.check_input_extent('wrf', min_lon, min_lat, max_lon, max_lat)
            print('-- Processing WRF to WPS Int...')
            run_wrf_to_int(start_date, end_date, hour_interval, del_old=params.cleanup_inputs)
        else:
            print('-- Downloading ERA5 data...')
            dl_era5(start_date, end_date, min_lon, min_lat, max_lon, max_lat)
            utils.check_input_extent('era5', min_lon, min_lat, max_lon, max_lat)
            print('-- Processing ERA5 to WPS Int...')
            run_era5_to_int(start_date, end_date, hour_interval, del_old=params.cleanup_inputs)
            if params.sst_source == 'cci':
                print('-- Processing CCI SST to WPS Int...')
                process_sst_cci(start_date, end_date, hour_interval, min_lon, min_lat, max_lon, max_lat)

        print('-- Running metgrid.exe...')
        run_metgrid(del_old=params.cleanup_inputs)

        print('-- Updating metgrid levels in namelist...')
        update_metgrid_levels()

        print('-- Running real.exe...')
        run_real(run_uuid, del_old=params.cleanup_inputs)

        # ---- Apply chunk-aware namelist edits (always, not just on restart chunks) ----
        # apply_restart_namelist with restart_time=None still sets restart_interval / override_restart_timers /
        # write_hist_at_0h_rst — which we need on the COLD START chunk too, otherwise WRF uses its default
        # restart_interval of 500000 minutes (~347 days) and never writes a wrfrst within a 1-day chunk.
        # On chunks 2+ we additionally download the prior wrfrst and apply restart=.true. + start_date* overrides.
        interval_minutes = params.restart_interval_days * 24 * 60
        if restart_state is not None:
            print(f'-- Downloading wrfrst from S3 inputs/{run_uuid}/...')
            download_wrfrst_to_run_path(run_uuid)
        apply_restart_namelist(restart_state, interval_minutes, end_date_override=chunk_end)
        if restart_state is not None:
            print(f'-- Restarting from {restart_state}; chunk_end_override={chunk_end}')
        else:
            print(f'-- Cold start chunk; restart_interval={interval_minutes}min, chunk_end_override={chunk_end}')

        # ---- Upload namelist archive (the only S3 inputs/<run_uuid>/ persistence in unified mode) ----
        print('-- Uploading chunk namelists for archival...')
        upload_chunk_namelists(run_uuid)

        # ---- Run WRF ----
        rename_dict = _build_rename_dict(ndown_check, domains)
        print('-- Running WRF...')
        monitor_wrf(outputs, end_date, run_uuid, rename_dict, chunk_end=chunk_end)

        # No explicit cleanup needed here — run_real's rmtree(run_path) on the next iteration wipes
        # leftover wrfinput/wrfbdy/wrffdda/wrflowinp/trmask, and the existing del_old=params.cleanup_inputs
        # flags in run_metgrid/run_era5_to_int/run_real handle data_path intermediates.
        if params.restart_stop_after_upload:
            print('-- stop_after_upload=true: chunk done; exiting (caller will submit next chunk).')
            return
        # else: loop back to next chunk in this same container


########################################
### Run sequence

start_time = pendulum.now('UTC')

print(f'-- run uuid: {run_uuid}')
print(f"-- start time: {start_time.format('YYYY-MM-DD HH:mm:ss')}")

if params.restart_enable and not params.preprocess_only:
    # Phase 3 unified per-chunk mode — preprocess + WRF in a single process per chunk,
    # looped internally if stop_after_upload=false.
    print('-- Mode: unified chunked (Phase 3) — preprocess + WRF per chunk in a single container')
    run_chunked_pipeline(run_uuid)

    end_time = pendulum.now('UTC')
    print(f"-- end time: {end_time.format('YYYY-MM-DD HH:mm:ss')}")
    mins = round((end_time - start_time).total_minutes())
    print(f"-- Total run minutes: {mins}")

else:
    domains = _resolve_domains()

    ndown_check, domains_init = check_ndown_params(domains)

    src_n_domains, domains = check_nml_params(domains)

    if domains_init is None:
        domains_init = list(domains)

    print(f'-- domains: {domains}')

    if domains_init[0] == 1 and all([domain - i == 1 for i, domain in enumerate(domains_init)]):
        _ = set_nml_params(domains_init)
    else:
        _ = set_nml_params()

    print('-- Run geogrid.exe...')
    min_lon, min_lat, max_lon, max_lat = run_geogrid(src_n_domains, domains_init)

    print('-- Top domain bounds:')
    print(min_lon, min_lat, max_lon, max_lat, sep=', ')

    start_date, end_date, hour_interval, outputs = set_nml_params(domains_init)

    print(f'start date: {start_date}, end date: {end_date}, input hour interval: {hour_interval}')

    if params.file.get('dynamics', {}).get('tracer_opt', 0) == 4:
        print('-- Creating WVT tracer mask files...')
        create_trmask(domains_init, start_date)

    if ndown_check:
        print('-- ndown has been selected and the prior wrfout files will be downloaded...')
        dl_ndown_input(domains_init[0], start_date, end_date)

    if params.is_wrf_input:
        print('-- Downloading WRF data...')
        dl_wrf(start_date, end_date)

        print('-- Checking input data coverage...')
        utils.check_input_extent('wrf', min_lon, min_lat, max_lon, max_lat)

        print('-- Processing WRF to WPS Int...')
        run_wrf_to_int(start_date, end_date, hour_interval, del_old=params.cleanup_inputs)
    else:
        print('-- Downloading ERA5 data...')
        dl_era5(start_date, end_date, min_lon, min_lat, max_lon, max_lat)

        print('-- Checking input data coverage...')
        utils.check_input_extent('era5', min_lon, min_lat, max_lon, max_lat)

        print('-- Processing ERA5 to WPS Int...')
        run_era5_to_int(start_date, end_date, hour_interval, del_old=params.cleanup_inputs)

        if params.sst_source == 'cci':
            print('-- Processing CCI SST to WPS Int...')
            process_sst_cci(start_date, end_date, hour_interval,
                            min_lon, min_lat, max_lon, max_lat)

    print('-- Running metgrid.exe...')
    run_metgrid(del_old=params.cleanup_inputs)

    print('-- Updating metgrid levels in namelist...')
    update_metgrid_levels()

    print('-- Running real.exe...')
    run_real(run_uuid, del_old=params.cleanup_inputs)

    if ndown_check:

        print('-- Running ndown.exe...')
        ndown_interval = run_ndown(run_uuid, del_old=params.cleanup_inputs)

        start_date, end_date, hour_interval, outputs = set_nml_params(domains)
        set_ndown_params(ndown_interval)

        rename_dict = {'_d01_': f'_d{domains[-1]:02d}_'}

    else:
        rename_dict = _build_rename_dict(ndown_check, domains)

    if params.preprocess_only:
        print('-- preprocess_only=true: preprocessing complete; inputs left in run_path. Exit.')

        end_time = pendulum.now('UTC')

        print(f"-- end time: {end_time.format('YYYY-MM-DD HH:mm:ss')}")

        mins = round((end_time - start_time).total_minutes())

        print(f"-- Total run minutes: {mins}")
    else:
        start_time2 = pendulum.now('UTC')

        print('-- Running WRF...')
        monitor_wrf(outputs, end_date, run_uuid, rename_dict)

        end_time = pendulum.now('UTC')

        print(f"-- end time: {end_time.format('YYYY-MM-DD HH:mm:ss')}")

        diff = end_time - start_time

        mins = round(diff.total_minutes())

        print(f"-- Total run minutes: {mins}")

        diff = end_time - start_time2

        mins = round(diff.total_minutes())

        print(f"-- WRF run minutes: {mins}")
