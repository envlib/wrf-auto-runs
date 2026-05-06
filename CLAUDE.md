# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated pipeline to run the WRF (Weather Research and Forecasting) model using ERA5 reanalysis data or WRF output as boundary/initial conditions. Runs inside a Docker container with WRF 4.7.1-ARW and WPS 4.6.0 pre-installed. All configuration is driven by a single `parameters.toml` file (gitignored; see `parameters_example.toml`).

The pipeline supports four execution modes selected by TOML/env flags:

- **Unified per-chunk** (default for production): set `[restart].enable=true` (without `preprocess_only` or `wrf_only`). Each invocation runs its OWN preprocess + WRF for one chunk of duration `interval_days`. wrfbdy/wrffdda/wrfinput/wrflowinp/trmask are local-only — never round-trip through S3. Only wrfrst + namelists persist on S3. With `stop_after_upload=true` the container exits after one chunk (SLURM chained pattern); with `stop_after_upload=false` the container loops internally until sim_end (local-dev pattern).
- **Single-stage**: full pipeline end-to-end in one container. Fine for short runs that fit in one job; no restart support.
- **Preprocess-only** (legacy split-pipeline): runs steps through `real.exe`, then uploads run inputs to S3 `inputs/<run_uuid>/`. Hands off to a separate WRF stage. Kept for backwards compat.
- **WRF-only** (legacy split-pipeline): downloads run inputs from `inputs/<run_uuid>/` and runs `wrf.exe`. Mutually exclusive with preprocess-only.

Unified per-chunk is the standard workflow because per-chunk preprocess avoids the wrfbdy/wrffdda S3 round-trip that dominates wallclock for long FDDA-enabled runs (wrffdda is ~200 GB for a 13-month run; in split mode every chunk re-downloads the full file). Each chunk is a single SLURM job that does preprocess + WRF; chunks chain via `--dependency=afterany` and auto-detect their position from the latest wrfrst on S3.

## Image Matrix

| Image | Compiler | WPS build | Use |
|---|---|---|---|
| `mullenkamp/wrf-auto-runs-intel-wvt:1.8` | Intel oneAPI | dmpar | **Default for all modes** — unified per-chunk, single-stage, and the legacy split-pipeline WRF stage |
| `mullenkamp/wrf-auto-runs-wvt:1.7` | gfortran | dmpar | Backup. Legacy split-pipeline preprocess stage; short single-stage runs |
| `mullenkamp/wrf-auto-runs:2.7` | gfortran | dmpar | Non-WVT variant |

Both WPS builds inject heap-array allocation flags (`-fno-stack-arrays` for gfortran, `-heap-arrays` for Intel) — required for stable long preprocessing runs (without them metgrid segfaults in libc partway through). See `~/.claude/projects/.../memory/wps_heap_arrays_requirement.md` and `wrf-docker-builds/CLAUDE.md`.

## Commands

```bash
# Unified per-chunk local run (Docker — restart.enable=true in parameters.toml).
# stop_after_upload=false → one container loops through all chunks until sim_end.
# stop_after_upload=true  → one container per chunk; ./run_local.sh re-runs with the same RUN_UUID.
./run_local.sh                  # in a project dir with the unified-mode docker-compose.yml

# Run full pipeline locally (Docker, single-stage — restart disabled)
docker compose up

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

### Unified per-chunk mode (default — `[restart].enable=true`, neither split flag set)

`main.py:run_chunked_pipeline(run_uuid)` drives a chunk loop. Each iteration:

1. `detect_remote_restart_state(run_uuid)` — `rclone lsf` against `inputs/<run_uuid>/`; finds the latest wrfrst timestamp on S3 (or None for chunk 1). Lightweight metadata read, no download.
2. `chunk_start = restart_state if restart_state else sim_start`. If `chunk_start >= sim_end`, exit cleanly (simulation complete).
3. `chunk_end = min(chunk_start + interval_days, sim_end)`.
4. `params.set_chunk_dates(chunk_start, chunk_end)` — mutates `params.file['time_control']` so downstream `set_nml_params` calls read chunk-specific dates.
5. Existing preprocess pipeline: `check_ndown_params` → `check_nml_params` → `set_nml_params` (twice, with `run_geogrid` between) → `create_trmask` (WVT only) → `dl_era5` / `dl_wrf` → `run_era5_to_int` / `run_wrf_to_int` → optional `process_sst_cci` → `run_metgrid` → `update_metgrid_levels` → `run_real`. `run_real` rmtrees `run_path`; we don't fight that — every iteration starts fresh.
6. If `restart_state is not None`: `download_wrfrst_to_run_path(run_uuid)` pulls the prior chunk's wrfrst from S3 into the freshly-recreated run_path.
7. `apply_restart_namelist(restart_state, restart_interval_minutes, end_date_override=chunk_end)` — always called (sets `restart_interval` and `write_hist_at_0h_rst=.true.` on chunk 1 too); on chunks 2+ also sets `restart=.true.` and `start_date*` = wrfrst timestamp, `override_restart_timers=.true.`. The `write_hist_at_0h_rst` flag forces wrf.exe to write a history frame at chunk_start so the next chunk's `Feb13_00:00:00.nc` clobbers any prior 1-frame version with a full 8-frame version. With `stop_after_upload=true`, also overrides `end_date*` to `chunk_end` so wrf.exe exits naturally at the chunk boundary (no SIGTERM).
8. `upload_chunk_namelists(run_uuid)` — uploads ONLY namelist.input + namelist.wps to `inputs/<run_uuid>/` (debug archive). No wrf*input/bdy/fdda/lowinp/trmask uploads — those are local-only in this mode.
9. `monitor_wrf(...)` — runs `wrf.exe` via `mpirun -n {n_cores}`; polls every 60s for completed wrfout / wrfxtrm / wrfzlevels / wrfrst files and uploads them.
10. If `params.restart_stop_after_upload`: return (caller submits next chunk container). Else loop to step 1.

### Single-stage / preprocess-only (legacy)

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

### WRF-only (legacy)

When `wrf_only=true`:

1. `derive_wrf_run_context()` — Re-compute `domains` / `outputs` / `start_date` / `end_date` / `rename_dict` from TOML
2. `download_run_inputs(run_uuid)` — Download the inputs prefix from S3 into `params.run_path` (includes any prior `wrfrst_d*` files when restart is enabled)
3. (if `[restart].enable=true`) `detect_restart_state()` — Scan `params.run_path` for any downloaded `wrfrst_d*` and parse the latest timestamp. `None` = cold-start chunk; otherwise restart from that wrfrst timestamp.
4. (if `[restart].enable=true`) `apply_restart_namelist()` — As above.
5. `monitor_wrf()` — Run `wrf.exe`.
6. `cleanup_run_inputs(run_uuid)` — (if `cleanup_inputs=true` AND NOT `restart_stop_after_upload`) Purge `inputs/<run_uuid>/` from S3.

## Mode Toggles (TOML / env vars)

All can be set in `parameters.toml` or overridden via env var (env wins):

- **`preprocess_only`** (default `false`) — Skip the WRF stage; upload inputs to S3 and exit. Mutually exclusive with `wrf_only`.
- **`wrf_only`** (default `false`) — Skip preprocessing; download inputs from S3 and run WRF. Requires `run_uuid` to be supplied (env or TOML).
- **`cleanup_inputs`** (default `true`) — Single cleanup knob. When true: deletes intermediate preprocessing files (met_em, ERA5 NetCDF, WPS int files) locally during the run, AND purges the `inputs/<run_uuid>/` S3 prefix after a successful WRF stage. When false: keeps everything for inspection / re-running.
- **`run_uuid`** (default: newly generated) — 13-char hex identifier for the run. Precedence: env > TOML > generated. Useful for re-running a wrf-only stage against an existing inputs prefix.

### `[restart]` section — chunked WRF runs

Enables the unified per-chunk mode and configures wrfrst checkpointing.

- **`enable`** (default `false`) — Master switch. When true (and neither `preprocess_only` nor `wrf_only` is set), `main.py` dispatches to `run_chunked_pipeline()`. When set alongside `wrf_only=true`, configures restart-aware behaviour for the legacy split-pipeline WRF stage.
- **`interval_days`** (required when `enable=true`) — WRF `restart_interval` set to `interval_days * 24 * 60` minutes. Also defines the chunk window length.
- **`stop_after_upload`** (default `false`) — When true, each invocation processes one chunk and exits (achieved by overriding `end_date*` in the namelist to `chunk_end`, so wrf.exe reaches it naturally; no signal handling). Designed for SLURM chained jobs across `interval_days` boundaries. **Disables auto-cleanup of `inputs/<run_uuid>/`** — multiple invocations share the prefix; user manually purges after the simulation completes. When false, the chunk loop runs internally until sim_end (best for local docker-compose dev).

**Image consolidation:** the same `wrf-auto-runs-intel-wvt:1.8+` image runs both preprocess and WRF in unified mode — the intel WPS is built dmpar (option 10 in `wrf-wps-intel-wvt/Dockerfile`) so `metgrid.exe`/`real.exe` parallelize via `mpirun -n N`.

**Wrfrst round-trip:** every chunk iteration re-downloads wrfrst from S3 even in the in-container loop case (where the wrfrst is technically already local). This is intentional: `run_real` rmtrees `run_path`, and rather than stash/restore wrfrst across that operation, we let every iteration look like a fresh container start. Cost is one wrfrst-sized download per chunk (~500 MB–1 GB); trivial vs. the ~2.6 TB of wrffdda re-downloads this design eliminates.

**S3 layout for restart artifacts:** wrfrst files live in the `inputs/<run_uuid>/` prefix alongside wrfinput/wrfbdy. Only the latest wrfrst per domain is kept on S3 (older ones are deleted via `cleanup_prior_wrfrst` after each upload).

**Wrfrst upload timing:** the in-loop poll uploads a wrfrst file when (a) a newer wrfrst exists locally (definitely complete), OR (b) the file's mtime has been stable for ≥60 seconds (single-write completion detected). Without (b), a wrfrst with no successor would sit locally until the next `restart_interval` write — potentially hours of wallclock for slow-resolving simulations.

**Final wrfout file at midnight chunk_end:** `monitor_wrf` skips the post-loop upload of the chunk_end single-frame wrfout file when the chunk's effective end falls exactly on midnight (00:00:00). Such a file is a "deceptive partial day" — same filename pattern as a new day file but contains only the rollover frame. Either (a) the next chunk clobbers it with a full 8-frame version on restart (via `write_hist_at_0h_rst`), or (b) it's the final chunk and the rollover state is also captured in wrfrst. Mid-day end_dates produce non-deceptive final files (multiple frames of legitimate end-of-sim data) and are uploaded normally.

### Other key TOML/env settings

- **`n_cores`** (default 8) — MPI ranks for `wrf.exe`.
- **`n_cores_preprocess`** (default 4) — MPI ranks for `metgrid.exe` / `real.exe` / `ndown.exe`. Requires the gfortran preprocess image (`wrf-auto-runs-wvt:1.6+`) which has dmpar WPS.

## S3 Layout

Under `<remote.output.path>/`:

- `inputs/<run_uuid>/` — Preprocess outputs handed to the WRF stage: `namelist.input`, `namelist.wps`, `wrfinput_d*`, `wrfbdy_d*`, `wrffdda_d*` (FDDA only), `wrflowinp_d*` (some SST options only), `trmask_d*` (WVT only), `wrfrst_d*_<TIMESTAMP>` (restart only — only the latest per domain). Purged after successful WRF if `cleanup_inputs=true` AND NOT `restart_stop_after_upload`.
- `wrfout_d*` / `wrfxtrm_d*` / `wrfzlevels_d*` (directly under `<remote.output.path>/`, NO run_uuid prefix) — Main WRF output files. Uploaded by `monitor_wrf` during the run, deleted locally after upload. (Earlier docs incorrectly placed these under a `<run_uuid>/` subprefix; the actual code in `utils.ul_output_files` uploads to the root path.)
- `logs/<run_uuid>/rsl.*` — `rsl.error.*` / `rsl.out.*` from `real.exe` / `ndown.exe` / `wrf.exe` failures.

## Key Architecture

All Python modules live under `wrf-auto-runs/`.

- **`params.py`** — Central config loader. Reads `parameters.toml`, detects Docker vs local mode (`[no_docker]` section), supports env var overrides (`start_date`, `end_date`, `domains`, `n_cores`, `n_cores_preprocess`, `duration_hours`, `wrf_only`, `preprocess_only`, `cleanup_inputs`, `run_uuid`, `restart_enable`, `restart_interval_days`, `restart_stop_after_upload`). All other scripts import `params` for paths and settings.
- **`defaults.py`** — Default namelist values for WPS and WRF. Defines field classification sets (`GEOGRID_ARRAY_FIELDS`, `DOMAINS_PER_DOMAIN_FIELDS`, etc.) and pipeline key sets (`DOMAINS_PIPELINE_KEYS`, `TIME_CONTROL_PIPELINE_KEYS`) that distinguish pipeline-consumed keys from WRF passthrough keys.
- **`set_params.py`** — Namelist management. Reads/writes Fortran namelists (`namelist.wps`, `namelist.input`) using `f90nml`. Handles domain subsetting/renumbering, time parameter injection, output stream configuration, and computes `time_step = dx * 0.001 * 6`. Uses `apply_overrides()` to merge TOML sections into WRF namelist sections. Also exposes `apply_restart_namelist(restart_time, restart_interval_minutes, end_date_override=None)` — in-place edit of `namelist.input` for restart/chunk-aware runs.
- **`upload_namelists.py`** — Despite the name, this module owns the entire `inputs/<run_uuid>/` S3 prefix lifecycle: `upload_run_inputs` / `download_run_inputs` / `cleanup_run_inputs` (legacy split mode), plus `upload_chunk_namelists` / `detect_remote_restart_state` / `download_wrfrst_to_run_path` (unified per-chunk mode). Also owns the wrfrst lifecycle helpers used by `monitor_wrf`: `upload_wrfrst`, `cleanup_prior_wrfrst`, `detect_restart_state`, `parse_wrfrst_timestamp`.
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

For production unified per-chunk runs, a per-project orchestrator script (`run_wrf_hetzner.sh` is the working pattern) is a plain bash script (not a SLURM job) that:

1. Resolves `RUN_UUID` (env > `parameters.toml` > generated).
2. Reads `start_date` / `end_date` / `interval_days` / `stop_after_upload` from `parameters.toml` via a small awk-based TOML reader (no python deps in the cluster's global env).
3. Computes `num_chunks = ceil((end - start) / interval_days) + 1` (the +1 hits the early-exit branch and no-ops).
4. Submits `num_chunks` chained `chunk.sl` jobs via `--dependency=afterany`. Each chunk auto-detects its position from S3 wrfrst state.

Shared bash helpers (`toml_get`, `gen_uuid`, `resolve_run_uuid`) live in a per-project `lib.sh` that the three project shell scripts (`run_local.sh`, `run_one_chunk.sh`, `run_wrf_<cluster>.sh`) all source. Copy `lib.sh` alongside when cloning a new project dir.

The legacy split-pipeline pattern (separate `preprocess.sl` + `wrf.sl` chained via `--dependency=afterok`) is still supported — see `slurm_scripts/readme.md` for cluster-specific variants.

**Apptainer gotcha:** with `--contain --writable-tmpfs`, the in-container `/tmp` defaults to a tiny tmpfs (~64 MB) which causes ERA5 downloads to silently truncate (rclone streams through `/tmp`). All SLURM scripts bind `${LOCAL_SCRATCH}/apptainer_tmp:/tmp` to a real disk path.

## Key Dependencies

- **Python**: `f90nml` (Fortran namelists), `pendulum` (dates), `era5_s3_dl` (ERA5 download CLI), `era5_to_int` (ERA5→WPS conversion CLI), `pyproj` (projections), `h5netcdf` (NetCDF reading), `sentry-sdk` (error tracking)
- **System**: `mpirun` (MPICH), `rclone` (data transfer), `ncks` (NetCDF variable filtering), `uv` (package management)

## Style

- Python >=3.11, line length 120, black formatting with `skip-string-normalization`
- All remote data transfer uses `rclone` with dynamically created config files (see `utils.create_rclone_config()`)
- `parameters.toml` contains credentials — never commit it (only `parameters_example.toml` is tracked)
