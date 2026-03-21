# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated pipeline to run the WRF (Weather Research and Forecasting) model using ERA5 reanalysis data or WRF output as boundary/initial conditions. Runs inside a Docker container (`mullenkamp/wrf-auto-runs:2.3`) with WRF 4.6.1-ARW and WPS 4.6.0 pre-installed. All configuration is driven by a single `parameters.toml` file (gitignored; see `parameters_example.toml`).

## Commands

```bash
# Run full pipeline (Docker)
docker-compose up

# Run locally (requires [no_docker] section in parameters.toml)
uv run wrf-era5-auto/main.py

# Linting/formatting (line length: 120)
uv run lint:style        # ruff + black --check
uv run lint:fmt          # black + ruff --fix
uv run lint:typing       # mypy

# Tests
uv run pytest              # pytest wrf-era5-auto/tests/
```

## Pipeline Execution Order (wrf-era5-auto/main.py)

1. `check_ndown_params()` — Determine if ndown (one-way nesting) mode is active
2. `check_nml_params()` — Validate namelist files and resolve domain list
3. `set_nml_params(domains_init)` — First pass: configure namelists for initial domain set
4. `run_geogrid()` — Execute `geogrid.exe` (static geography); returns domain bounding box
5. `set_nml_params(domains_init)` — Second pass: set time/date/history params, generate output file list
6. `upload_namelists()` — Upload namelists to remote storage via rclone
7. `dl_ndown_input()` — (ndown only) Download prior wrfout files
8. `dl_era5()` — Download ERA5 NetCDF files via rclone
9. `run_era5_to_int()` — Convert ERA5 NetCDF to WPS intermediate format
10. `run_metgrid()` — Execute `metgrid.exe`
11. `run_real()` — Execute `real.exe` via `mpirun -n 4`
12. `run_ndown()` — (ndown only) Execute `ndown.exe` via `mpirun -n 4`
13. `monitor_wrf()` — Launch `wrf.exe` via `mpirun`, poll for output, upload files in real-time

`wrf-era5-auto/main_alt.py` runs steps 1–11 only (preprocessing, no WRF execution).

## Key Architecture

All Python modules live under `wrf-era5-auto/`.

- **`params.py`** — Central config loader. Reads `parameters.toml`, detects Docker vs local mode (`[no_docker]` section), supports env var overrides (`start_date`, `end_date`, `domains`, `n_cores`, `duration_hours`). All other scripts import `params` for paths and settings.
- **`defaults.py`** — Default namelist values for WPS and WRF. Defines field classification sets (`GEOGRID_ARRAY_FIELDS`, `DOMAINS_PER_DOMAIN_FIELDS`, etc.) and pipeline key sets (`DOMAINS_PIPELINE_KEYS`, `TIME_CONTROL_PIPELINE_KEYS`) that distinguish pipeline-consumed keys from WRF passthrough keys.
- **`set_params.py`** — Namelist management. Reads/writes Fortran namelists (`namelist.wps`, `namelist.input`) using `f90nml`. Handles domain subsetting/renumbering, time parameter injection, output stream configuration, and computes `time_step = dx * 0.001 * 6`. Uses `apply_overrides()` to merge TOML sections into WRF namelist sections (passthrough keys from `[domains]`/`[time_control]`, and direct sections like `[fdda]`/`[bdy_control]`).
- **`utils.py`** — Shared utilities: rclone config creation, output file querying/renaming/uploading, variable filtering via `ncks`, domain projection recalculation (`pyproj`).
- **`monitor_wrf.py`** — Runs `wrf.exe` and polls every 60s for completed output files, uploads them via rclone, and deletes local copies. On failure, uploads `rsl.*` log files.

## Data Flow

- **Namelists**: Source templates at `/data/namelists/` → modified programmatically → written to `/data/`
- **ERA5 data**: Downloaded from S3 → converted to WPS intermediate format → consumed by metgrid → deleted
- **WRF output**: `wrfout` (history), `wrfxtrm` (daily diagnostics), `wrfzlevels` (height-interpolated) → uploaded to remote during run → deleted locally

## TOML → WRF Namelist Mapping

- **`[domains]`** — Domain geometry (geogrid fields, `e_vert`, `p_top_requested`, `parent_time_step_ratio`). The `run` key selects which domain subset to execute. Any key not in `DOMAINS_PIPELINE_KEYS` passes through directly to WRF `&domains`.
- **`[time_control]`** — Simulation period and output config (`start_date`, `end_date`, `history_file`, etc.). Any key not in `TIME_CONTROL_PIPELINE_KEYS` passes through directly to WRF `&time_control`.
- **`[physics]`** / **`[dynamics]`** — Override defaults; all keys pass to their respective WRF namelist sections.
- **`[fdda]`**, **`[bdy_control]`**, **`[grib2]`**, **`[namelist_quilt]`**, **`[diags]`** — Direct WRF namelist sections. All keys pass through via `apply_overrides()`.

## Domain Subsetting

The pipeline can run any subset of domains defined in `[domains]` (e.g., `run = [3, 4]`). When a subset doesn't start at domain 1, `utils.recalc_geogrid()` recomputes the map projection center and grid parameters. Domains are renumbered sequentially (e.g., domain 3 becomes d01 internally, renamed back on output).

## ndown Mode

One-way nesting from a prior WRF run. Activated by the `[ndown]` section in `parameters.toml`. Requires a single non-domain-1 domain (e.g., `run = [3]`). Downloads prior wrfout files for the parent domain, runs real+ndown, then runs WRF on the child domain only.

## Key Dependencies

- **Python**: `f90nml` (Fortran namelists), `pendulum` (dates), `era5_to_int` (ERA5→WPS conversion CLI), `pyproj` (projections), `h5netcdf` (NetCDF reading), `sentry-sdk` (error tracking)
- **System**: `mpirun` (OpenMPI), `rclone` (data transfer), `ncks` (NetCDF variable filtering), `uv` (package management)

## Style

- Python >=3.11, line length 120, black formatting with `skip-string-normalization`
- All remote data transfer uses `rclone` with dynamically created config files (see `utils.create_rclone_config()`)
- `parameters.toml` contains credentials — never commit it (only `parameters_example.toml` is tracked)
