#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep 23 15:09:38 2025

@author: mike
"""
import uuid
from time import sleep

import pendulum
import sentry_sdk

# from download_nml_domain import dl_nml_domain
from set_params import check_nml_params, set_nml_params, set_ndown_params
from download_era5 import dl_era5
from run_era5_to_int import run_era5_to_int
from download_wrf import dl_wrf
from run_wrf_to_int import run_wrf_to_int
from run_metgrid import run_metgrid
from run_real import run_real
from monitor_wrf import monitor_wrf
from upload_namelists import upload_namelists
from check_ndown import check_ndown_params
from run_geogrid import run_geogrid
from run_ndown import run_ndown
from download_ndown_input import dl_ndown_input

import params

run_uuid = uuid.uuid4().hex[-13:]

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
### Run sequence

start_time = pendulum.now('UTC')

print(f'-- run uuid: {run_uuid}')

print(f"-- start time: {start_time.format('YYYY-MM-DD HH:mm:ss')}")

# print('-- Downloading namelists...')
# dl_check = dl_nml_domain()

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

print('-- Uploading updated namelists')
ul_nml_check = upload_namelists(run_uuid)

if ndown_check:
    print('-- ndown has been selected and the prior wrfout files will be downloaded...')
    dl_ndown_input(domains_init[0], start_date, end_date)
else:
    print('-- A full nested domain model will be run')

if params.is_wrf_input:
    print('-- Downloading WRF data...')
    dl_wrf(start_date, end_date)

    print('-- Processing WRF to WPS Int...')
    run_wrf_to_int(start_date, end_date, hour_interval)
else:
    print('-- Downloading ERA5 data...')
    dl_era5(start_date, end_date)

    print('-- Processing ERA5 to WPS Int...')
    run_era5_to_int(start_date, end_date, hour_interval)

print('-- Running metgrid.exe...')
run_metgrid()

print('-- Running real.exe...')
run_real(run_uuid)

if ndown_check:

    print('-- Running ndown.exe...')
    ndown_interval = run_ndown(run_uuid)

    start_date, end_date, hour_interval, outputs = set_nml_params(domains)
    set_ndown_params(ndown_interval)

    rename_dict = {'_d01_': f'_d{domains[-1]:02d}_'}

else:
    rename_dict = {}
    for i, domain in enumerate(domains):
        rename_dict[f'_d{i+1:02d}_'] = f'_d{domain:02d}_'

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



























