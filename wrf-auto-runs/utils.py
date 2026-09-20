#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep 23 15:03:38 2025

@author: mike
"""
import fnmatch
import os
import shlex
import signal
import subprocess
import pathlib

import h5netcdf
import numpy as np
import pendulum
import pyproj
import sentry_sdk

import params
import defaults
from create_trmask import num_wvt_regions as count_wvt_regions

############################################
### Parameters


#######################################################
### Functions

def to_list(val):
    """

    """
    if not isinstance(val, list):
        val = [val]

    return val


def create_rclone_config(name, config_path, config_dict):
    """

    """
    type_ = config_dict['type']
    config_list = [f'{k}={v}' for k, v in config_dict.items() if k != 'type']
    config_str = ' '.join(config_list)
    config_path = config_path.joinpath('rclone.config')
    cmd_str = f'rclone config create {name} {type_} {config_str} --config={config_path} --non-interactive'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=True)

    return config_path


def dl_include_names(pattern, dts):
    """Both time spellings of each datetime, for an rclone --include-from list.

    ``pattern`` is a format string taking ``date``, e.g. ``'wrfout_d01_{date}.nc'``.

    These functions consume a PRIOR run's archive, whose spelling is that run's, not this one's.
    Output uploaded before monitor_wrf began renaming ':' -> '_' keeps colons permanently and
    cannot be regenerated, so both spellings have to be requested indefinitely.
    """
    names = []
    for dt in dts:
        colon = dt.strftime(params.wps_date_format)
        names.append(pattern.format(date=colon))
        names.append(pattern.format(date=colon.replace(':', '_')))
    return names


def dedupe_dl_listing(file_list):
    """Collapse an rclone listing to one file per timestep, preferring the colon-free spelling.

    A remote can legitimately hold both spellings of the same timestep: a chunk that crashed and
    was resubmitted across the rename cutover re-uploads under a different object key, so the
    older colon-named file is not clobbered. Downloading both would hand wrf_to_int (or ndown)
    the same timestep twice with no error.

    Underscore wins because it is by construction the later upload -- and because the colon-named
    member may be a 1-frame placeholder that its 8-frame replacement failed to clobber.
    """
    by_key = {}
    for fname in file_list:
        key = fname.replace(':', '_')
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = fname
        elif prev != fname:
            keep, drop = (fname, prev) if fname == key else (prev, fname)
            print(f'-- WARNING: {drop} and {keep} are the same timestep on the remote; using {keep}')
            by_key[key] = keep
    return sorted(by_key.values())


def dt_to_file_names(prefix, dts, domains):
    """
    pendulum datetimes to wrfout file names.
    """
    out_list = []
    for dt in dts:
        date_str = dt.strftime(params.wps_date_format)
        for domain in domains:
            file_name = params.outfile_format.format(prefix=prefix, domain=domain, date=date_str)
            out_list.append(file_name)

    return out_list


def read_last_line(file_path):
    """

    """
    cmd_str = f'tail -1 {file_path}'
    cmd_list = shlex.split(cmd_str)
    p = subprocess.run(cmd_list, capture_output=True, text=True, check=False)

    return p.stdout.strip('\n')


def query_out_files(run_path, out_files=None, include_xtrm=False):
    """Find WRF output files in run_path, grouped by (out_name, domain) and sorted by name.

    out_files: optional iterable of expected filenames. When provided, only files whose name
        is in this set are returned (legacy/exact-match mode). When None, glob-match against
        the known output prefixes (wrfout_d, wrfxtrm_d, wrfzlevels_d) — needed for restart
        runs where WRF's first wrfout may be offset by history_interval and therefore won't
        appear in a cold-start-derived expected list.
    include_xtrm: when False, skip wrfxtrm files entirely.
    """
    out_files_set = set(out_files) if out_files is not None else None
    files = {}
    for file_path in run_path.iterdir():
        if not file_path.is_file():
            continue
        file_name = file_path.name

        if out_files_set is not None:
            # Legacy exact-match mode.
            if file_name not in out_files_set:
                continue
        else:
            # Glob mode — accept any wrfout / wrfxtrm / wrfzlevels file.
            if not (file_name.startswith('wrfout_d')
                    or file_name.startswith('wrfxtrm_d')
                    or file_name.startswith('wrfzlevels_d')):
                continue

        try:
            out_name, domain, _datetime = file_name.split('_', 2)
        except ValueError:
            continue

        if out_name == 'wrfxtrm' and not include_xtrm:
            continue

        files.setdefault((out_name, domain), []).append(str(file_path))
        files[(out_name, domain)].sort()

    return files


# def query_out_files(run_path, output_globs):
#     """

#     """
#     out_files = {}
#     for glob in output_globs:
#         for file_path in run_path.glob(glob):
#             file_name = file_path.name
#             out_name, domain, datetime = file_name.split('_', 2)
#             if (out_name, domain) in out_files:
#                 out_files[(out_name, domain)].append(str(file_path))
#                 out_files[(out_name, domain)].sort()
#             else:
#                 out_files[(out_name, domain)] = [str(file_path)]

#     return out_files


def select_files_to_ul(out_files, min_files, wrfxtrm_skip_newest=False):
    """Flatten out_files into a list of file paths to upload.

    For each (out_name, domain) group:
    - wrfout / wrfzlevels: upload file_paths[min_files:] (skip the `min_files` newest).
      During polling pass min_files=1 to skip the file WRF is still writing; at post-success
      pass min_files=0 (or 1 if the chunk ends exactly on midnight and the newest wrfout is
      a deceptive partial-day file).
    - wrfxtrm: special-cased because at chunk-end ALL wrfxtrm files are complete (each covers
      `n_days_per_file` days and is closed when that period ends), so we want them all even
      when the corresponding wrfout would be skipped. During polling, however, the newest
      wrfxtrm may still be being written — pass wrfxtrm_skip_newest=True to skip it.
    """
    files = []
    for grp, file_paths in out_files.items():
        out_name, domain = grp
        n_files = len(file_paths)
        file_paths.sort(reverse=True)
        if out_name == 'wrfxtrm':
            if wrfxtrm_skip_newest and n_files > 1:
                files.extend(file_paths[1:n_files])
            elif not wrfxtrm_skip_newest:
                files.extend(file_paths)
            # else: only one wrfxtrm and we're told to skip the newest → upload nothing this poll
        elif n_files > min_files:
            files.extend(file_paths[min_files:n_files])

    return files


def rename_files(files, rename_dict):
    """Apply every matching rename rule to each file, one os.rename per file.

    Returns the post-rename path of EVERY input file, whether or not any rule matched.

    Two properties are load-bearing and both have regression tests in
    tests/test_rename_files.py -- neither is obvious from reading the call sites:

    1. Each rule's membership test runs against the ORIGINAL filename, never against the
       partially-rewritten one. The nested-ndown map chains ({'_d01_': '_d02_',
       '_d02_': '_d03_'}), so matching on a rewritten name would send the d01 file through
       the d02 rule as well and land it on d03 -- on top of the real d02 file, which
       os.rename destroys silently on POSIX.
    2. Files matching no rule are still returned. They are re-selected by the caller after a
       failed upload (deletion only happens on rclone exit 0), and by then they already carry
       their renamed names, so no rule matches on the retry. Dropping them here would strand
       them on local disk forever, unuploaded and never reported.
    """
    if not rename_dict:
        return files

    new_files = []
    # Descending sort so high-numbered domains (e.g., wrfout_d02_...) move
    # to their new slot (d03) BEFORE a lower-numbered file (wrfout_d01_...)
    # renames into the d02 slot. Without this, os.rename silently overwrites
    # the d02 file on POSIX and the higher-domain data is destroyed.
    for file_path in sorted(files, reverse=True):
        orig_path, orig_file_name = os.path.split(file_path)

        new_file_name = orig_file_name
        for orig, new in rename_dict.items():
            if orig in orig_file_name:
                new_file_name = new_file_name.replace(orig, new)

        if new_file_name == orig_file_name:
            new_files.append(file_path)
            continue

        new_file_path = os.path.join(orig_path, new_file_name)
        os.rename(file_path, new_file_path)
        new_files.append(new_file_path)

    return new_files


def check_input_extent(input_type, min_lon, min_lat, max_lon, max_lat):
    """
    Verify that input data spatially covers the WRF domain.

    Reads the first available source file (ERA5 or wrfout) and compares its
    lat/lon extent against the domain bounds from run_geogrid().
    Raises ValueError with a clear message if coverage is insufficient.

    Parameters
    ----------
    input_type : str
        'era5' or 'wrf'
    min_lon, min_lat, max_lon, max_lat : float
        Domain bounds (0-360 longitude convention, from run_geogrid).
    """
    buffer = 0.5  # degrees buffer for interpolation margin

    if input_type == 'era5':
        sfc_path = params.data_path.joinpath('era5', 'e5.oper.an.sfc')
        nc_files = sorted(sfc_path.rglob('*.nc'))
        if not nc_files:
            raise FileNotFoundError(f'No ERA5 sfc files found in {sfc_path}')

        with h5netcdf.File(str(nc_files[0]), 'r') as f:
            lat = np.asarray(f['latitude'][:])
            lon = np.asarray(f['longitude'][:])
        input_lat_min, input_lat_max = float(lat.min()), float(lat.max())
        input_lon = np.where(lon < 0, lon + 360, lon)
        input_lon_min, input_lon_max = float(input_lon.min()), float(input_lon.max())
        source_desc = 'ERA5'

    elif input_type == 'wrf':
        wrfout_path = params.data_path.joinpath('wrfout')
        nc_files = sorted(wrfout_path.glob('wrfout_*.nc'))
        if not nc_files:
            raise FileNotFoundError(f'No wrfout files found in {wrfout_path}')

        with h5netcdf.File(str(nc_files[0]), 'r') as f:
            lat = np.asarray(f['XLAT'][0])
            lon = np.asarray(f['XLONG'][0])
        input_lat_min, input_lat_max = float(lat.min()), float(lat.max())
        input_lon = np.where(lon < 0, lon + 360, lon)
        input_lon_min, input_lon_max = float(input_lon.min()), float(input_lon.max())
        source_desc = 'WRF wrfout'

    else:
        raise ValueError(f"Unknown input_type: {input_type}")

    # Check coverage
    gaps = []
    if input_lat_min > min_lat + buffer:
        gaps.append(f'lat south of {input_lat_min:.1f} (domain needs {min_lat:.1f})')
    if input_lat_max < max_lat - buffer:
        gaps.append(f'lat north of {input_lat_max:.1f} (domain needs {max_lat:.1f})')
    if input_lon_min > min_lon + buffer:
        gaps.append(f'lon west of {input_lon_min:.1f} (domain needs {min_lon:.1f})')
    if input_lon_max < max_lon - buffer:
        gaps.append(f'lon east of {input_lon_max:.1f} (domain needs {max_lon:.1f})')

    if gaps:
        gap_str = '\n  - '.join(gaps)
        raise ValueError(
            f"{source_desc} data extent (lat {input_lat_min:.1f} to {input_lat_max:.1f}, "
            f"lon {input_lon_min:.1f} to {input_lon_max:.1f}) does not cover the WRF domain "
            f"(lat {min_lat:.1f} to {max_lat:.1f}, lon {min_lon:.1f} to {max_lon:.1f}).\n"
            f"Missing coverage:\n  - {gap_str}"
        )


def _wvt_tracer_base(name):
    """If `name` is a WVT 3D named-member tracer (base or _NN-suffixed), return its
    family base (lower-case); else None. e.g. 'qv_tr' -> 'qv_tr', 'qv_tr_03' -> 'qv_tr',
    'TR_THUM_U_PHY_DT_02' -> 'tr_thum_u_phy_dt'."""
    low = name.lower()
    if low in defaults.WVT_TRACER_FAMILIES:
        return low
    head, _, tail = low.rpartition('_')
    if tail.isdigit() and head in defaults.WVT_TRACER_FAMILIES:
        return head
    return None


def resolve_output_variables(variables, n_wvt_regions=1):
    """
    Expand user variable list with required coordinate/auxiliary variables.
    Always adds 2D coordinates. Adds 3D auxiliaries if any 3D variable is present.

    For multi-region WVT (n_wvt_regions > 1), any requested 3D tracer named-member
    family (qv_tr..qg_tr, tr_thum_{u,v}_phy_dt -- region 1 is the unsuffixed base)
    is auto-expanded to all active regions, so the user keeps a stable variable list
    even as the [wvt] region count changes. The region-dimensioned 2D accumulators
    (TR_RAINNC etc.) are single variables and need no expansion. The user's casing is
    preserved on the added _0N members.
    """
    var_set = set(variables)

    if n_wvt_regions > 1:
        for v in list(var_set):
            if v.lower() in defaults.WVT_TRACER_FAMILIES:
                for n in range(2, n_wvt_regions + 1):
                    var_set.add(f'{v}_{n:02d}')

    var_set.update(defaults.COORD_VARS_2D)
    # 3D coords are needed for standard 3D vars and for any 3D WVT tracer member.
    if (var_set & defaults.VARS_3D) or any(_wvt_tracer_base(v) for v in var_set):
        var_set.update(defaults.COORD_VARS_3D)
    return sorted(var_set)


def prune_for_tracer_opt(names, tracer_opt):
    """Drop the WVT-only variables from `names` when tracers are off.

    Returns (kept, pruned), both in the input order. From WRF image 2.5 the WVT fields are
    Registry-packaged and simply do not exist in a tracer_opt != 4 wrfout; `ncks -v` on a
    missing name exits 1 and filter_variables runs it with check=True, so without this a
    tracer-off run carrying a WVT output preset aborts. Prune-with-a-log-line rather than a
    config-time refusal: configs legitimately carry an inert WVT list with tracers off (the P1
    timing family's n00, which has no output target). Pure function so it is testable without
    ncks or a parameters.toml.
    """
    if tracer_opt == 4:
        return list(names), []
    kept, pruned = [], []
    for v in names:
        if v.lower() in defaults.WVT_ONLY_VARIABLES or _wvt_tracer_base(v) is not None:
            pruned.append(v)
        else:
            kept.append(v)
    return kept, pruned


def present_in_file(names, file_path):
    """Split `names` into (present, missing) against the variables of one netCDF file
    (case-insensitive). Defence for whatever the prune did not anticipate: ncks must never be
    handed a name the file lacks."""
    with h5netcdf.File(file_path, 'r') as f:
        have = {v.lower(): v for v in f.variables}
    present = [have.get(v.lower(), v) for v in names if v.lower() in have]
    missing = [v for v in names if v.lower() not in have]
    return present, missing


def filter_variables(files, variables):
    """Subset every wrfout in `files` to `variables` (+ coordinates) in place with ncks."""
    # Multi-region WVT: expand requested tracer families to all active regions.
    tracer_opt = params.file.get('dynamics', {}).get('tracer_opt', 0)
    if isinstance(tracer_opt, list):
        tracer_opt = tracer_opt[0]
    n_wvt = count_wvt_regions(params.file.get('wvt', {})) if tracer_opt == 4 else 1
    resolved = resolve_output_variables(variables, n_wvt)
    resolved, pruned = prune_for_tracer_opt(resolved, tracer_opt)
    if pruned:
        print(f'filter_variables: tracer_opt={tracer_opt}, pruned {len(pruned)} WVT-only variable(s) '
              f'that a tracer-off run does not write: {sorted(pruned)}')
    warned = set()
    for file_path in files:
        orig_path, orig_file_name = os.path.split(file_path)
        if 'wrfout' in orig_file_name:
            # Per FILE (d01 and d02 carry different variable sets, and iterdir order is not
            # sorted): ncks must never be handed a name this particular file lacks.
            present, missing = present_in_file(resolved, file_path)
            if missing and tuple(sorted(missing)) not in warned:
                warned.add(tuple(sorted(missing)))
                print(f'filter_variables: WARNING {len(missing)} requested variable(s) are not in '
                      f'{orig_file_name} and are dropped from its ncks list: {sorted(missing)}')
            vars_str = ','.join(present)
            cmd_str = f'ncks -O -4 -L 1 -v {vars_str} {orig_file_name} wrf_temp.nc'
            cmd_list = shlex.split(cmd_str)
            p = subprocess.run(cmd_list, capture_output=True, text=True, check=True, cwd=orig_path)
            os.replace(os.path.join(orig_path, 'wrf_temp.nc'), file_path)

    return True


def end_frame_min_files(effective_end, upload_end_frame: bool) -> int:
    """
    How many newest files per (type, domain) the post-run upload skips. A run that ends exactly at
    midnight leaves a single-frame wrfout named like a new day; the hindcast pattern skips it
    (``1``) because the next chunk / next week's job rewrites it. ``upload_end_frame`` uploads it
    anyway (``0``) -- a single-stage forecast's last lead lives in that file.
    """
    at_midnight = effective_end.hour == 0 and effective_end.minute == 0 and effective_end.second == 0
    return 1 if (at_midnight and not upload_end_frame) else 0


def run_output_hook(hook, file_path):
    """
    Run ``hook['command']`` (with ``{path}`` substituted, shell-quoted) for one uploaded output file.
    Never raises: the WRF run must not die because a downstream consumer hiccupped -- every exception,
    including a bad command string or undecodable output, is reported and swallowed. A hook that overruns
    ``timeout_seconds`` gets SIGTERM, then ``grace_seconds`` to unwind (release remote locks, close
    files), then SIGKILL; the hook runs in its own session so the signals reach its whole process group
    and a grandchild holding the pipes cannot stall the poll loop.

    Returns True when the hook exited 0, else False (after printing the tail of its output and, with
    Sentry configured, sending a warning). The tail of the hook's stderr is printed on success too, so
    the pipeline log records what each hook did.
    """
    if not fnmatch.fnmatch(os.path.basename(file_path), hook['match']):
        return True
    start = pendulum.now('UTC')
    outcome, err = None, ''
    try:
        cmd_list = shlex.split(hook['command'].format(path=shlex.quote(file_path)))
        proc = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                errors='replace', start_new_session=True)
        try:
            _, err = proc.communicate(timeout=hook['timeout_seconds'])
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGTERM)
            try:
                _, err = proc.communicate(timeout=hook['grace_seconds'])
                outcome = f'timed out after {hook["timeout_seconds"]} s (exited {proc.returncode} on SIGTERM)'
            except subprocess.TimeoutExpired:
                _signal_group(proc, signal.SIGKILL)
                try:
                    _, err = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    err = ''
                outcome = f'timed out after {hook["timeout_seconds"]} s and ignored SIGTERM; killed'
        if outcome is None and proc.returncode != 0:
            outcome = f'exited {proc.returncode}'
    except Exception as e:  # noqa: BLE001 -- the contract is "never raises"
        outcome = f'could not run ({type(e).__name__}: {e})'
    mins = round((pendulum.now('UTC') - start).total_minutes(), 1)
    tail = '\n'.join((err or '').strip().splitlines()[-15:])
    if outcome is None:
        print(f'-- output hook ok for {os.path.basename(file_path)} in {mins} mins')
        if tail:
            print(f'   hook stderr:\n{tail}')
        return True
    print(f'-- output hook FAILED for {os.path.basename(file_path)}: {outcome} ({mins} mins)')
    if tail:
        print(f'   hook stderr:\n{tail}')
    if params.is_sentry:
        sentry_sdk.capture_message(
            f'output hook {outcome} for {os.path.basename(file_path)}: {tail[-500:]}', level='warning'
        )
    return False


def _signal_group(proc, sig):
    """Signal the hook's whole process group (it was started with start_new_session=True)."""
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


# Files whose hook failed during the poll loop. They stay in run_path and would be re-selected on
# every 60 s poll; retrying each one for up to timeout+grace per poll for the rest of a 144 h run is
# not useful (review ifs-forecast-cycle-code-2). They get exactly one more attempt: the post-run pass.
_hook_failed = set()


def hook_output_files(files, retry_failed=False):
    """
    Local-only delivery: the ``[hooks].on_output_file`` command is the sole consumer of each completed
    output file. A file is deleted after its hook succeeded (``[hooks].delete_on_success``, default
    true) and KEPT when the hook failed, so whatever runs after WRF can reconcile from ``run_path``.
    A failed file is not retried on later polls unless ``retry_failed`` (the post-run pass sets it).
    Returns the files that stayed behind.
    """
    hook = params.output_hook
    kept = []
    for file in files:
        if not os.path.exists(file):
            continue
        if not fnmatch.fnmatch(os.path.basename(file), hook['match']):
            kept.append(file)  # not the hook's to consume (e.g. wrfxtrm while only wrfout is archived)
            continue
        if file in _hook_failed and not retry_failed:
            kept.append(file)
            continue
        ok = run_output_hook(hook, file)
        if ok and hook['delete_on_success']:
            os.remove(file)
            _hook_failed.discard(file)
        else:
            kept.append(file)
            if not ok:
                _hook_failed.add(file)
    return kept


def ul_output_files(files, run_path, name, out_path, config_path):
    """

    """
    files_str = '\n'.join([os.path.split(p)[-1] for p in files])
    print(f'-- Uploading files:\n{files_str}')

    cmd_str = f'rclone copy {run_path} {name}:{out_path} --transfers=4 --config={config_path} --files-from-raw -'
    cmd_list = shlex.split(cmd_str)

    start_ul = pendulum.now('UTC')
    p = subprocess.run(cmd_list, input=files_str, capture_output=True, text=True, check=False)
    end_ul = pendulum.now('UTC')

    diff = end_ul - start_ul

    mins = round(diff.total_minutes(), 1)

    if p.returncode == 0:
        for file in files:
            # The hook sees the file after its upload succeeded and before it is deleted, under
            # the archived (colon-free, renamed) name rclone just used.
            if params.output_hook is not None and os.path.exists(file):
                run_output_hook(params.output_hook, file)
            if os.path.exists(file):
                os.remove(file)
        print(f'-- Upload successful in {mins} mins')
    else:
        print(f'-- Upload FAILED in {mins} mins (rclone exit {p.returncode})')
        if p.stderr:
            print(f'   rclone stderr:\n{p.stderr.strip()}')


def recalc_geogrid(geogrid, domains):
    """

    """
    parent_ids = to_list(geogrid['parent_id'])
    old_max_domains = len(parent_ids)

    parent_grid_ratio = to_list(geogrid['parent_grid_ratio'])

    dx = geogrid['dx']
    dy = geogrid['dy']

    i_parent_start = to_list(geogrid['i_parent_start'])
    j_parent_start = to_list(geogrid['j_parent_start'])

    e_we = to_list(geogrid['e_we'])
    e_sn = to_list(geogrid['e_sn'])

    # define original projection
    map_proj = geogrid['map_proj'].lower()
    lat_0 = geogrid['ref_lat']
    lat_1 = geogrid['truelat1']
    lat_2 = geogrid['truelat2']

    if 'stand_lon' in geogrid:
        lon_0 = geogrid['stand_lon']
    else:
        lon_0 = geogrid['ref_lon']

    ref_lon = geogrid['ref_lon']

    new_top_domain = domains[0]

    # TODO: eventually I'd like to allow multiple sub domains below the ndown domain, but currently only one is allowed
    if new_top_domain > old_max_domains:
        raise ValueError('new_top_domain must be greater than max_domains')

    if new_top_domain > 1:

        lon_angle = lon_0 - ref_lon

        if map_proj == 'lambert':
            pwrf = f"""+proj=lcc +lat_1={lat_1} +lat_2={lat_2} +lat_0={lat_0} +lon_0={lon_0} +x_0=0 +y_0=0 +a={params.wrf_sphere_radius} +b={params.wrf_sphere_radius}"""
        elif map_proj == 'mercator':
            pwrf = f"""+proj=merc +lat_ts={lat_1} +lon_0={lon_0} +x_0=0 +y_0=0 +a={params.wrf_sphere_radius} +b={params.wrf_sphere_radius}"""
        elif map_proj == 'polar':
            pwrf = f"""+proj=stere +lat_ts={lat_1} +lat_0=90.0 +lon_0={lon_0} +x_0=0 +y_0=0 +a={params.wrf_sphere_radius} +b={params.wrf_sphere_radius}"""
        else:
            raise NotImplementedError('WRF proj not implemented yet: '
                                      f'{map_proj}')

        proj_crs = pyproj.CRS.from_string(pwrf)

        geo_crs = pyproj.CRS(
                proj='latlong',
                R=params.wrf_sphere_radius
            )

        geo_to_proj = pyproj.Transformer.from_crs(geo_crs, proj_crs, always_xy=True)
        proj_to_geo = pyproj.Transformer.from_crs(proj_crs, geo_crs, always_xy=True)

        index = new_top_domain - 1
        domain_seq = [index]
        while True:
            parent_id = parent_ids[index]
            if parent_id > 1:
                index = parent_id - 1
                domain_seq.insert(0, index)
            else:
                # domain_seq.insert(0, 0)
                break

        prev_x_center, prev_y_center = geo_to_proj.transform(ref_lon, lat_0)
        prev_dx_center = ((e_we[0] - 1) * 0.5) * dx
        prev_dy_center = ((e_sn[0] - 1) * 0.5) * dy
        for i in domain_seq:
            i_start = i_parent_start[i] - 1
            j_start = j_parent_start[i] - 1

            new_dx_start = i_start * dx
            new_dy_start = j_start * dy

            dx = dx / parent_grid_ratio[i]
            dy = dy / parent_grid_ratio[i]

            new_dx_end = new_dx_start + (dx * (e_we[i] - 1))
            new_dy_end = new_dy_start + (dy * (e_sn[i] - 1))

            new_dx_center = (new_dx_end + new_dx_start) * 0.5
            new_dy_center = (new_dy_end + new_dy_start) * 0.5

            ddx = new_dx_center - prev_dx_center
            ddy = new_dy_center - prev_dy_center

            new_x_center = prev_x_center + ddx
            new_y_center = prev_y_center + ddy

            ref_lon, lat_0 = proj_to_geo.transform(new_x_center, new_y_center)

            prev_x_center, prev_y_center = geo_to_proj.transform(ref_lon, lat_0)
            prev_dx_center = ((e_we[i] - 1) * 0.5) * dx
            prev_dy_center = ((e_sn[i] - 1) * 0.5) * dy

        lon_0 = ref_lon + lon_angle

    ## Save projection back to namelist.wps
    ref_lat = round(lat_0, 6)
    ref_lon = round(ref_lon, 6)
    stand_lon = round(lon_0, 6)

    geogrid['dx'] = int(dx)
    geogrid['dy'] = int(dy)
    geogrid['ref_lat'] = ref_lat
    geogrid['ref_lon'] = ref_lon
    geogrid['truelat1'] = ref_lat
    geogrid['truelat2'] = ref_lat
    geogrid['stand_lon'] = stand_lon

    ## Update other parameters in namelist.wps
    domain_index = [domain - 1 for domain in domains]
    new_top_parent_id = new_top_domain - 1
    geogrid['parent_id'] = [parent_ids[pid] - new_top_parent_id if parent_ids[pid] - new_top_parent_id > 1 else 1 for pid in domain_index]

    new_parent_grid_ratio = [parent_grid_ratio[index] for index in domain_index]
    new_parent_grid_ratio[0] = 1
    geogrid['parent_grid_ratio'] = new_parent_grid_ratio

    new_i_parent_start = [i_parent_start[index] for index in domain_index]
    new_i_parent_start[0] = 1
    geogrid['i_parent_start'] = new_i_parent_start

    new_j_parent_start = [j_parent_start[index] for index in domain_index]
    new_j_parent_start[0] = 1
    geogrid['j_parent_start'] = new_j_parent_start

    for p, v in geogrid.items():
        if isinstance(v, list):
            if len(v) == old_max_domains:
                geogrid[p] = [v[index] for index in domain_index]

    return geogrid


def update_geogrid(geogrid, domains):
    """

    """
    parent_ids = to_list(geogrid['parent_id'])
    old_max_domains = len(parent_ids)

    parent_grid_ratio = to_list(geogrid['parent_grid_ratio'])

    dx = geogrid['dx']
    dy = geogrid['dy']

    i_parent_start = to_list(geogrid['i_parent_start'])
    j_parent_start = to_list(geogrid['j_parent_start'])

    new_top_domain = domains[0]

    if new_top_domain > old_max_domains:
        raise ValueError('new_top_domain must be greater than max_domains')

    if new_top_domain > 1:

        index = new_top_domain - 1
        domain_seq = [index]
        while True:
            parent_id = parent_ids[index]
            if parent_id > 1:
                index = parent_id - 1
                domain_seq.insert(0, index)
            else:
                # domain_seq.insert(0, 0)
                break

        for i in domain_seq:
            dx = dx / parent_grid_ratio[i]
            dy = dy / parent_grid_ratio[i]

    geogrid['dx'] = int(dx)
    geogrid['dy'] = int(dy)

    ## Update other parameters in namelist.wps
    domain_index = [domain - 1 for domain in domains]
    new_top_parent_id = new_top_domain - 1
    geogrid['parent_id'] = [parent_ids[pid] - new_top_parent_id if parent_ids[pid] - new_top_parent_id > 1 else 1 for pid in domain_index]

    new_parent_grid_ratio = [parent_grid_ratio[index] for index in domain_index]
    new_parent_grid_ratio[0] = 1
    geogrid['parent_grid_ratio'] = new_parent_grid_ratio

    new_i_parent_start = [i_parent_start[index] for index in domain_index]
    new_i_parent_start[0] = 1
    geogrid['i_parent_start'] = new_i_parent_start

    new_j_parent_start = [j_parent_start[index] for index in domain_index]
    new_j_parent_start[0] = 1
    geogrid['j_parent_start'] = new_j_parent_start

    for p, v in geogrid.items():
        if isinstance(v, list):
            if len(v) == old_max_domains:
                geogrid[p] = [v[index] for index in domain_index]

    return geogrid



















