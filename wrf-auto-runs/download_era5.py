#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Download ERA5 data via the era5_dl CLI (era5-s3-dl package).

Translates wrf-auto-runs' [remote.era5] config into era5_dl's [source] TOML,
points [remote] at the local /data/era5 directory, and invokes era5_dl with
the right preset, date range, bbox, and (when sst_source == 'cci') the
--skip-vars SSTK,CI flag.
"""
import copy
import shlex
import subprocess

import params


def _format_toml_value(v):
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace('\\', '\\\\').replace('"', '\\"')
    return f'"{s}"'


def _write_era5_dl_toml(path, sections):
    """Write a minimal TOML containing only top-level tables of scalar keys."""
    lines = []
    for section, values in sections.items():
        lines.append(f'[{section}]')
        for k, v in values.items():
            lines.append(f'{k} = {_format_toml_value(v)}')
        lines.append('')
    path.write_text('\n'.join(lines))


def dl_era5(start_date, end_date, min_lon, min_lat, max_lon, max_lat):
    era5_out = params.data_path.joinpath('era5')
    era5_out.mkdir(exist_ok=True)

    source_cfg = copy.deepcopy(params.file['remote']['era5'])

    cfg_path = params.data_path.joinpath('era5_dl.toml')
    _write_era5_dl_toml(cfg_path, {
        'source': source_cfg,
        'remote': {'type': 'local', 'path': str(era5_out)},
    })

    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    cmd_parts = [
        'era5_dl', str(cfg_path),
        '--preset', 'wrf',
        '-s', start_str,
        '-e', end_str,
        '--min-lon', f'{min_lon}',
        '--max-lon', f'{max_lon}',
        '--min-lat', f'{min_lat}',
        '--max-lat', f'{max_lat}',
        '--no-check-target',
        '-n', '4',
    ]
    if params.sst_source == 'cci':
        cmd_parts += ['--skip-vars', 'sstk,ci']

    p = subprocess.run(cmd_parts, capture_output=True, text=True, check=False)

    if p.returncode != 0:
        raise RuntimeError(f'era5_dl failed ({p.returncode}):\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}')

    return True
