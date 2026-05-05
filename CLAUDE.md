# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated pipeline to run the WRF (Weather Research and Forecasting) model using ERA5 reanalysis data or WRF output as boundary/initial conditions. Runs inside a Docker container with WRF 4.7.1-ARW and WPS 4.6.0 pre-installed. All configuration is driven by a single `parameters.toml` file (gitignored; see `parameters_example.toml`).

The pipeline supports three execution modes selected by TOML/env flags:

- **Single-stage** (default): full pipeline end-to-end in one container.
- **Preprocess-only**: runs steps through `real.exe`, then uploads run inputs to S3 `inputs/<run_uuid>/`. Hands off to a separate WRF stage.
- **WRF-only**: downloads run inputs from `inputs/<run_uuid>/` and runs `wrf.exe`. Mutually exclusive with preprocess-only.

The split modes enable a SLURM dependency chain (preprocess job → WRF job via `--dependency=afterok`) so a long WRF simulation isn't tied to the preprocess scratch / hardware.

## Image Matrix

| Image | Compiler | WPS build | Use |
|---|---|---|---|
| `mullenkamp/wrf-auto-runs-wvt:1.7` | gfortran | dmpar | preprocess stage (parallel metgrid) and short single-stage runs |
| `mullenkamp/wrf-auto-runs-intel-wvt:1.7` | Intel oneAPI | serial | WRF stage (faster `wrf.exe`) |
| `mullenkamp/wrf-auto-runs:2.7` | gfortran | dmpar | non-WVT variant |

Both WPS builds inject heap-array allocation flags (`-fno-stack-arrays` for gfortran, `-heap-arrays` for Intel) — required for stable long preprocessing runs (without them metgrid segfaults in libc partway through). See `~/.claude/projects/.../memory/wps_heap_arrays_requirement.md` and `wrf-docker-builds/CLAUDE.md`.

## Commands

```bash
# Run full pipeline locally (Docker, single-stage)
docker compose up

# Run split pipeline locally (preprocess → wrf via S3 handoff)
./run_local.sh                 # in a project dir with the two-stage docker-compose.yml

# Run locally without Docker (requires [no_docker] section in parameters.toml)
uv run wrf-auto-runs/main.py

# Linting/formatting (line length: 120)
uv run lint:style              # ruff + black --check
uv run lint:fmt                # black + ruff --fix
uv run lint:typing             # mypy

# Tests
uv run pytest                   # pytest wrf-auto-runs/tests/
```

## Pipeline Execution Order (`wrf-auto-runs/main.py`)

When `wrf_only=false` (single-stage or preprocess-only), the pipeline runs:

1. `check_ndown_params()` — Determine if ndown (one-way nesting) mode is active
2. `check_nml_params()` — Validate executables and domain configuration
3. `set_nml_params()` — First pass: configure namelists for the initial domain set
4. `run_geogrid()` — Execute `geogrid.exe`; returns domain bounding box
5. `set_nml_params(domains_init)` — Second pass: time/date/history params, output file list
6. `create_trmask()` — (WVT only, `tracer_opt=4`) Generate tracer mask files
7. `dl_ndown_input()` — (ndown only) Download prior wrfout files
8. `dl_era5()` or `dl_wrf()` — Download ERA5 NetCDF or prior wrfout via rclone
9. `run_era5_to_int()` / `run_wrf_to_int()` — Convert to WPS intermediate format
10. `process_sst_cci()` — (CCI SST source only) Process CCI SST to WPS Int
11. `run_metgrid()` — Execute `metgrid.exe` via `mpirun -n {n_cores_preprocess}`
12. `update_metgrid_levels()` — Auto-detect `num_metgrid_levels`, update namelist
13. `run_real()` — Execute `real.exe` via `mpirun -n {n_cores_preprocess}`
14. `run_ndown()` — (ndown only) Execute `ndown.exe` via `mpirun -n {n_cores_preprocess}`
15. `upload_run_inputs()` — (preprocess-only) Upload `wrfinput_d*` / `wrfbdy_d*` / `wrffdda_d*` / `wrflowinp_d*` / `trmask_d*` / `namelist.input` / `namelist.wps` to `inputs/<run_uuid>/` on S3, then exit
16. `monitor_wrf()` — Launch `wrf.exe` via `mpirun`, poll for output, upload files in real-time

When `wrf_only=true`:

1. `derive_wrf_run_context()` — Re-compute `domains` / `outputs` / `start_date` / `end_date` / `rename_dict` from TOML
2. `download_run_inputs(run_uuid)` — Download the inputs prefix from S3 into `params.run_path` (includes any prior `wrfrst_d*` files when restart is enabled)
3. (if `[restart].enable=true`) `detect_restart_state()` — Scan `params.run_path` for any downloaded `wrfrst_d*` and parse the latest timestamp. `None` = cold-start chunk; otherwise restart from that wrfrst timestamp.
4. (if `[restart].enable=true`) `apply_restart_namelist()` — In-place edit of `run_path/namelist.input` to set `restart=.true.`, `start_date*` = wrfrst timestamp, `restart_interval` = `interval_days * 24 * 60` minutes, `override_restart_timers=.true.` (timer counts from chunk_start), `write_hist_at_0h_rst=.true.` (forces wrf.exe to write a history frame at chunk_start so the next chunk's `Feb13_00:00:00.nc` clobbers any prior 1-frame version with a full 8-frame version). When `stop_after_upload=true`, also overrides `end_date*` to `min(chunk_start + interval_days, original_end_date)` so wrf.exe naturally exits at chunk_end without needing SIGTERM.
5. `monitor_wrf()` — Run `wrf.exe`. Polls every 60s for completed wrfout / wrfrst files; uploads them as soon as detected.
6. `cleanup_run_inputs(run_uuid)` — (if `cleanup_inputs=true` AND NOT `restart_stop_after_upload`) Purge `inputs/<run_uuid>/` from S3. `stop_after_upload=true` disables auto-cleanup because the prefix is shared across multiple chained sbatch jobs and premature deletion breaks the chain.

## Mode Toggles (TOML / env vars)

All can be set in `parameters.toml` or overridden via env var (env wins):

- **`preprocess_only`** (default `false`) — Skip the WRF stage; upload inputs to S3 and exit. Mutually exclusive with `wrf_only`.
- **`wrf_only`** (default `false`) — Skip preprocessing; download inputs from S3 and run WRF. Requires `run_uuid` to be supplied (env or TOML).
- **`cleanup_inputs`** (default `true`) — Single cleanup knob. When true: deletes intermediate preprocessing files (met_em, ERA5 NetCDF, WPS int files) locally during the run, AND purges the `inputs/<run_uuid>/` S3 prefix after a successful WRF stage. When false: keeps everything for inspection / re-running.
- **`run_uuid`** (default: newly generated) — 13-char hex identifier for the run. Precedence: env > TOML > generated. Useful for re-running a wrf-only stage against an existing inputs prefix.

### `[restart]` section — chunked WRF runs

Enables WRF to write `wrfrst` files at a fixed interval and continue from them on subsequent `wrf_only` invocations. Designed for long simulations split across SLURM jobs subject to per-job time limits.

- **`enable`** (default `false`) — Master switch. When true, `apply_restart_namelist()` configures the namelist for restart-aware behavior on every wrf_only invocation.
- **`interval_days`** (required when `enable=true`) — WRF `restart_interval` set to `interval_days * 24 * 60` minutes.
- **`stop_after_upload`** (default `false`) — When true, the wrf_only invocation runs ~`interval_days` of simulation then exits cleanly (achieved by overriding `end_date*` in the namelist to `chunk_end`, so wrf.exe reaches it naturally; no signal handling). Designed for chaining multiple `sbatch wrf.sl` jobs across `interval_days` boundaries. **Disables auto-cleanup of `inputs/<run_uuid>/`** — multiple sbatch jobs share the prefix; user manually purges after the simulation completes.

**S3 layout for restart artifacts:** wrfrst files live in the `inputs/<run_uuid>/` prefix alongside wrfinput/wrfbdy. Only the latest wrfrst per domain is kept on S3 (older ones are deleted via `cleanup_prior_wrfrst` after each upload).

**Wrfrst upload timing:** the in-loop poll uploads a wrfrst file when (a) a newer wrfrst exists locally (definitely complete), OR (b) the file's mtime has been stable for ≥60 seconds (single-write completion detected). Without (b), a wrfrst with no successor would sit locally until the next `restart_interval` write — potentially hours of wallclock for slow-resolving simulations.

**Final wrfout file at midnight chunk_end:** `monitor_wrf` skips the post-loop upload of the chunk_end single-frame wrfout file when the chunk's effective end falls exactly on midnight (00:00:00). Such a file is a "deceptive partial day" — same filename pattern as a new day file but contains only the rollover frame. Either (a) the next chunk clobbers it with a full 8-frame version on restart (via `write_hist_at_0h_rst`), or (b) it's the final chunk and the rollover state is also captured in wrfrst. Mid-day end_dates produce non-deceptive final files (multiple frames of legitimate end-of-sim data) and are uploaded normally.

### Other key TOML/env settings

- **`n_cores`** (default 8) — MPI ranks for `wrf.exe`.
- **`n_cores_preprocess`** (default 4) — MPI ranks for `metgrid.exe` / `real.exe` / `ndown.exe`. Requires the gfortran preprocess image (`wrf-auto-runs-wvt:1.6+`) which has dmpar WPS.

## S3 Layout

Under `<remote.output.path>/`:

- `inputs/<run_uuid>/` — Preprocess outputs handed to the WRF stage: `namelist.input`, `namelist.wps`, `wrfinput_d*`, `wrfbdy_d*`, `wrffdda_d*` (FDDA only), `wrflowinp_d*` (some SST options only), `trmask_d*` (WVT only), `wrfrst_d*_<TIMESTAMP>` (restart only — only the latest per domain). Purged after successful WRF if `cleanup_inputs=true` AND NOT `restart_stop_after_upload`.
- `<run_uuid>/wrfout*` — Main WRF output files (uploaded by `monitor_wrf` during the run, deleted locally after upload).
- `logs/<run_uuid>/rsl.*` — `rsl.error.*` / `rsl.out.*` from `real.exe` / `ndown.exe` / `wrf.exe` failures.

## Key Architecture

All Python modules live under `wrf-auto-runs/`.

- **`params.py`** — Central config loader. Reads `parameters.toml`, detects Docker vs local mode (`[no_docker]` section), supports env var overrides (`start_date`, `end_date`, `domains`, `n_cores`, `n_cores_preprocess`, `duration_hours`, `wrf_only`, `preprocess_only`, `cleanup_inputs`, `run_uuid`, `restart_enable`, `restart_interval_days`, `restart_stop_after_upload`). All other scripts import `params` for paths and settings.
- **`defaults.py`** — Default namelist values for WPS and WRF. Defines field classification sets (`GEOGRID_ARRAY_FIELDS`, `DOMAINS_PER_DOMAIN_FIELDS`, etc.) and pipeline key sets (`DOMAINS_PIPELINE_KEYS`, `TIME_CONTROL_PIPELINE_KEYS`) that distinguish pipeline-consumed keys from WRF passthrough keys.
- **`set_params.py`** — Namelist management. Reads/writes Fortran namelists (`namelist.wps`, `namelist.input`) using `f90nml`. Handles domain subsetting/renumbering, time parameter injection, output stream configuration, and computes `time_step = dx * 0.001 * 6`. Uses `apply_overrides()` to merge TOML sections into WRF namelist sections.
- **`upload_namelists.py`** — Despite the name, this module owns the entire `inputs/<run_uuid>/` S3 prefix lifecycle: `upload_run_inputs`, `download_run_inputs`, `cleanup_run_inputs`. Also owns the wrfrst lifecycle helpers used by `monitor_wrf`: `upload_wrfrst`, `cleanup_prior_wrfrst`, `detect_restart_state`, `parse_wrfrst_timestamp`.
- **`set_params.py`** — Also exposes `apply_restart_namelist(restart_time, restart_interval_minutes, end_date_override=None)` for the wrf_only restart flow. Mirrors the existing `set_ndown_params` / `update_metgrid_levels` pattern of in-place f90nml edits.
- **`utils.py`** — Shared utilities: rclone config creation, output file querying/renaming/uploading, variable filtering via `ncks`, domain projection recalculation (`pyproj`).
- **`monitor_wrf.py`** — Runs `wrf.exe` and polls every 60s for completed output files, uploads them via rclone, and deletes local copies. On failure, uploads `rsl.*` log files.

## Data Flow

- **ERA5 / wrfout input**: downloaded from S3 → converted to WPS intermediate format → consumed by metgrid → deleted (if `cleanup_inputs=true`).
- **Preprocess-stage outputs**: `wrfinput_d*` / `wrfbdy_d*` / `wrffdda_d*` / `wrflowinp_d*` / `trmask_d*` written by `real.exe` to `params.run_path` → uploaded by `upload_run_inputs` to `inputs/<run_uuid>/`.
- **WRF-stage outputs**: `wrfout` (history), `wrfxtrm` (daily diagnostics), `wrfzlevels` (height-interpolated) → uploaded to `<run_uuid>/` during the run by `monitor_wrf` → deleted locally.

## TOML → WRF Namelist Mapping

- **`[domains]`** — Domain geometry (geogrid fields, `e_vert`, `p_top_requested`, `parent_time_step_ratio`). The `run` key selects which domain subset to execute. Any key not in `DOMAINS_PIPELINE_KEYS` passes through directly to WRF `&domains`.
- **`[time_control]`** — Simulation period and output config (`start_date`, `end_date`, `history_file`, etc.). Any key not in `TIME_CONTROL_PIPELINE_KEYS` passes through directly to WRF `&time_control`.
- **`[physics]`** / **`[dynamics]`** — Override defaults; all keys pass to their respective WRF namelist sections.
- **`[fdda]`**, **`[bdy_control]`**, **`[grib2]`**, **`[namelist_quilt]`**, **`[diags]`** — Direct WRF namelist sections. All keys pass through via `apply_overrides()`.

## Domain Subsetting

The pipeline can run any subset of domains defined in `[domains]` (e.g., `run = [3, 4]`). When a subset doesn't start at domain 1, `utils.recalc_geogrid()` recomputes the map projection center and grid parameters. Domains are renumbered sequentially (e.g., domain 3 becomes d01 internally, renamed back on output).

## ndown Mode

One-way nesting from a prior WRF run. Activated by the `[ndown]` section in `parameters.toml`. Requires a single non-domain-1 domain (e.g., `run = [3]`). Downloads prior wrfout files for the parent domain, runs real+ndown, then runs WRF on the child domain only.

## SLURM Orchestration

For production split-pipeline runs, a per-project orchestrator script (`run_wrf_hetzner.sl` is the working pattern) is a plain bash script (not a SLURM job) that:

1. Generates a single `run_uuid`.
2. `sbatch preprocess.sl` (gfortran image, `preprocess_only=true`) → captures jobid.
3. `sbatch --dependency=afterok:<jobid> wrf.sl` (Intel image, `wrf_only=true`) → only runs if preprocess succeeded.

Both `preprocess.sl` and `wrf.sl` get the `RUN_UUID` via `--export` so they share the S3 inputs prefix. See `slurm_scripts/readme.md` for cluster-specific variants.

**Apptainer gotcha:** with `--contain --writable-tmpfs`, the in-container `/tmp` defaults to a tiny tmpfs (~64 MB) which causes ERA5 downloads to silently truncate (rclone streams through `/tmp`). All split-pipeline SLURM scripts bind `${LOCAL_SCRATCH}/apptainer_tmp:/tmp` to a real disk path.

## Key Dependencies

- **Python**: `f90nml` (Fortran namelists), `pendulum` (dates), `era5_s3_dl` (ERA5 download CLI), `era5_to_int` (ERA5→WPS conversion CLI), `pyproj` (projections), `h5netcdf` (NetCDF reading), `sentry-sdk` (error tracking)
- **System**: `mpirun` (MPICH), `rclone` (data transfer), `ncks` (NetCDF variable filtering), `uv` (package management)

## Style

- Python >=3.11, line length 120, black formatting with `skip-string-normalization`
- All remote data transfer uses `rclone` with dynamically created config files (see `utils.create_rclone_config()`)
- `parameters.toml` contains credentials — never commit it (only `parameters_example.toml` is tracked)
