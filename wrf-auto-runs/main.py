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
    upload_run_inputs, download_run_inputs, cleanup_run_inputs, detect_restart_state,
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


def derive_wrf_run_context():
    """Compute (domains, outputs, end_date, rename_dict) for wrf_only mode.

    Calls set_nml_params once to derive the same return values the full preprocess
    pipeline would. set_nml_params writes namelist.{input,wps} to data_path as a
    side effect, but that's harmless: download_run_inputs places the real, post-real
    namelist.input directly at run_path/namelist.input, which is what wrf.exe reads.
    """
    domains = _resolve_domains()
    ndown_check, domains_init = check_ndown_params(domains)
    src_n_domains, domains = check_nml_params(domains)
    if domains_init is None:
        domains_init = list(domains)

    start_date, end_date, hour_interval, outputs = set_nml_params(domains_init)

    rename_dict = _build_rename_dict(ndown_check, domains)
    return domains, outputs, start_date, end_date, rename_dict


########################################
### Run sequence

start_time = pendulum.now('UTC')

print(f'-- run uuid: {run_uuid}')
print(f"-- start time: {start_time.format('YYYY-MM-DD HH:mm:ss')}")

if params.wrf_only:
    print('-- Mode: wrf_only (skipping preprocessing; downloading inputs from S3)')
    domains, outputs, start_date, end_date, rename_dict = derive_wrf_run_context()
    print(f'-- domains: {domains}')

    print(f'-- Downloading run inputs for uuid {run_uuid}...')
    download_run_inputs(run_uuid)

    # Restart-mode handling — see plan: Phase 2.
    if params.restart_enable:
        restart_state = detect_restart_state()  # latest wrfrst timestamp on disk, or None for cold-start

        if restart_state is not None and restart_state >= end_date:
            print(f'-- Restart timestamp {restart_state} >= end_date {end_date}; nothing to simulate.')
            if params.cleanup_inputs and not params.restart_stop_after_upload:
                cleanup_run_inputs(run_uuid)
            end_time = pendulum.now('UTC')
            print(f"-- end time: {end_time.format('YYYY-MM-DD HH:mm:ss')}")
            import sys
            sys.exit(0)

        chunk_start = restart_state if restart_state is not None else start_date
        interval_minutes = params.restart_interval_days * 24 * 60
        end_date_override = None
        if params.restart_stop_after_upload:
            chunk_end = min(chunk_start.add(days=params.restart_interval_days), end_date)
            end_date_override = chunk_end

        apply_restart_namelist(restart_state, interval_minutes, end_date_override)

        if restart_state is not None:
            print(f'-- Restarting from {restart_state}')
        else:
            print('-- Cold start with restart writes enabled')
        if end_date_override is not None:
            print(f'-- Chunk end_date overridden to {end_date_override}')

    # Pass the chunk's actual simulation end to monitor_wrf so it can apply the
    # midnight-skip rule correctly: intermediate chunks have chunk_end < end_date
    # (and chunk_end is always at midnight by construction), while the final chunk
    # / non-chunked runs have chunk_end == end_date which may or may not be midnight.
    # end_date_override is only defined when restart was enabled above; short-circuit
    # ensures we never reference it otherwise.
    effective_chunk_end = (
        end_date_override
        if (params.restart_enable and params.restart_stop_after_upload and end_date_override is not None)
        else end_date
    )

    start_time2 = pendulum.now('UTC')
    print('-- Running WRF...')
    monitor_wrf(outputs, end_date, run_uuid, rename_dict, chunk_end=effective_chunk_end)

    # Cleanup gating: stop_after_upload mode disables auto-cleanup (multiple sbatch jobs share the prefix).
    if params.cleanup_inputs and not params.restart_stop_after_upload:
        print(f'-- Cleaning up inputs/{run_uuid}/ on remote...')
        cleanup_run_inputs(run_uuid)
    elif params.restart_stop_after_upload:
        print('-- stop_after_upload=true: skipping cleanup_run_inputs (manual cleanup required after full simulation completes)')

    end_time = pendulum.now('UTC')
    print(f"-- end time: {end_time.format('YYYY-MM-DD HH:mm:ss')}")
    mins = round((end_time - start_time).total_minutes())
    print(f"-- Total run minutes: {mins}")
    wrf_mins = round((end_time - start_time2).total_minutes())
    print(f"-- WRF run minutes: {wrf_mins}")

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
        print('-- Uploading run inputs (wrfinput, wrfbdy, namelist.input) for handoff to WRF stage...')
        upload_run_inputs(run_uuid)

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
