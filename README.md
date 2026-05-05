# wrf-auto-runs

Automated pipeline to run the WRF (Weather Research and Forecasting) model using ERA5 reanalysis data or WRF output as boundary and initial conditions. All configuration is driven by a single `parameters.toml` file. Runs inside a Docker container with WRF 4.7.1-ARW and WPS 4.6.0 pre-installed.

The pipeline supports either a **single-stage** workflow (everything in one container) or a **split workflow** (preprocess + WRF in separate containers/SLURM jobs, handed off via S3). The split workflow is the recommended pattern for long simulations and HPC.

## Prerequisites

- Linux with Docker installed (your user must be in the `docker` group)
- WPS_GEOG static geography data — download with `test_scripts/add_geog.sh`

## Quick Start (Single-Stage)

```bash
# Edit parameters.toml — at minimum fill in [domains], [time_control], and [remote] credentials
cp parameters_example.toml parameters.toml

# Edit the docker-compose.yml to map the local WPS_GEOG path
docker compose up -d           # Run and detach from process
docker compose logs -f         # Look at the logs go!

# Once everything has finished/failed you need to clean up the docker-compose instance
docker compose down
```

## Split Workflow (Preprocess + WRF)

The split workflow runs preprocessing (steps through `real.exe`) in one container, hands off `wrfinput_d*` / `wrfbdy_d*` / `namelist.input` / etc. via S3, and runs `wrf.exe` in a second container. This decouples the preprocess step (cheap, gfortran image) from the long WRF run (expensive, intel image), and is what production SLURM runs use.

### Local (docker-compose)

A two-service compose file with `depends_on: condition: service_completed_successfully` plus a tiny wrapper:

```yaml
# docker-compose.yml
services:
  preprocess:
    image: mullenkamp/wrf-auto-runs-wvt:1.7
    environment:
      - run_uuid=${RUN_UUID:?}
      - preprocess_only=true
      - n_cores_preprocess=8
    volumes:
      - "./parameters.toml:/app/parameters.toml"
      - ~/WPS_GEOG:/WPS_GEOG
      - ~/data/wrf/tests/test_wvt_preprocess:/data
  wrf:
    image: mullenkamp/wrf-auto-runs-intel-wvt:1.7
    environment:
      - run_uuid=${RUN_UUID:?}
      - wrf_only=true
      - n_cores=8
    volumes:
      - "./parameters.toml:/app/parameters.toml"
      - ~/WPS_GEOG:/WPS_GEOG
      - ~/data/wrf/tests/test_wvt_wrf:/data
    depends_on:
      preprocess:
        condition: service_completed_successfully
```

```bash
#!/bin/bash
# run_local.sh
export RUN_UUID="${RUN_UUID:-$(python3 -c 'import uuid; print(uuid.uuid4().hex[-13:])')}"
docker compose up --abort-on-container-failure
```

Each stage gets its own scratch volume so the S3 handoff is actually exercised (vs. shared volume which would mask S3 issues until SLURM).

### SLURM

A per-project orchestrator (plain bash, not a SLURM job) generates a `run_uuid` and submits two jobs with `--dependency=afterok:<preprocess_jobid>`. See `slurm_scripts/readme.md` for cluster-specific scripts.

## docker-compose.yml

### WPS_GEOG path

The local WPS_GEOG path must be mapped to /WPS_GEOG in the docker image:

```
- /local/path/WPS_GEOG:/WPS_GEOG
```

The static data for NZ can be downloaded and extracted like this:

```bash
wget -N https://b2.envlib.xyz/file/envlib/wrf/static_data/nz_wps_geog.tar.zst
tar --zstd -xf nz_wps_geog.tar.zst
rm nz_wps_geog.tar.zst
```

### Mount the data directory

WRF in the docker image runs all processes in `/data`. Mount this path to a local drive to inspect intermediate files:

```
- /local/path/test_data:/data
```

## Configuration

All settings live in `parameters.toml`. See `parameters_example.toml` for a fully annotated template.

### Top-level

- **`n_cores`** — Number of MPI processes for `wrf.exe` (max ~24 before efficiency drops).
- **`n_cores_preprocess`** — Number of MPI processes for `metgrid.exe` / `real.exe` / `ndown.exe`. Default `4`. Bump higher (8–16) to speed up long preprocessing runs. Requires the gfortran preprocess image (built dmpar from `wrf-wps-wvt-debian:1.3` onward).
- **`preprocess_only`** — Run preprocessing through `real.exe`, upload `inputs/<run_uuid>/` to S3, exit. Mutually exclusive with `wrf_only`.
- **`wrf_only`** — Skip preprocessing, download `inputs/<run_uuid>/` from S3, run `wrf.exe`. Requires `run_uuid` to be set (env or TOML).
- **`cleanup_inputs`** — Default `true`. Single cleanup knob: deletes intermediate preprocessing files (met_em, ERA5 NetCDF, WPS int files) locally as the run progresses, AND purges `inputs/<run_uuid>/` from S3 after a successful WRF run. Set to `false` to keep everything for inspection.
- **`run_uuid`** — Optional 13-char hex identifier for the run. Normally generated fresh per run; set explicitly to re-run a `wrf_only` stage against an existing `inputs/<run_uuid>/` prefix, or to make a run reproducible by uuid. Precedence: env var > TOML > generated.

### `[restart]`

Optional. Enables WRF to write `wrfrst` files at a configurable interval and continue from them on subsequent `wrf_only` invocations. Designed for long simulations (e.g. 13-month runs) split across multiple SLURM jobs subject to per-job time limits.

- **`enable`** (default `false`) — Master switch.
- **`interval_days`** (required when `enable=true`) — WRF `restart_interval` is set to `interval_days * 24 * 60` minutes. The wrf_only stage writes a wrfrst at every boundary and uploads it to `inputs/<run_uuid>/` (only the latest per domain is kept on S3; older ones are deleted automatically).
- **`stop_after_upload`** (default `false`) — When true, each `wrf_only` invocation runs ~`interval_days` then exits cleanly. Designed for chaining: queue multiple `sbatch wrf.sl` jobs (manual or `--dependency=afterany`) reusing the same `RUN_UUID`. Each chunk picks up where the prior one left off via the wrfrst on S3. When true, **disables auto-cleanup of `inputs/<run_uuid>/` regardless of `cleanup_inputs`** — multiple sbatch jobs share the prefix; you manually purge after the simulation completes.

Notes on chunked-run behaviour:

- `apply_restart_namelist` automatically sets `write_hist_at_0h_rst=.true.` so each restart chunk writes a wrfout frame at chunk_start; combined with WRF's `NF_CLOBBER` open-for-write semantics, the next chunk's full 8-frame wrfout file overwrites the prior chunk's 1-frame placeholder at the same filename. Net effect: `Feb13_00:00:00.nc`, `Feb14_00:00:00.nc`, etc. all end up as 8-frame day files after the chain finishes.
- The wrfout file at chunk_end IS skipped on upload when chunk_end falls exactly at midnight (00:00:00) — that file is a "deceptive partial day" containing just the rollover frame, which is captured either in the next chunk's clobber or in the final chunk's wrfrst. Mid-day end_dates produce non-deceptive multi-frame final files and are uploaded normally.
- **`output_presets`** — Optional string or list of named variable presets (e.g. `'wrf_to_int'`). Each preset expands to the set of wrfout variables required by the named tool. Variables from all selected presets are merged together.
- **`output_variables`** — Optional list of additional wrfout variables to retain. Merged with any preset variables. Coordinate and auxiliary 3D variables are included automatically. Comment out both `output_presets` and `output_variables` to keep all variables.

### `[time_control]`

Simulation period and output configuration.

- **`start_date`** / **`end_date`** or **`duration_hours`** — Simulation window.
- **`interval_hours`** — ERA5 boundary-condition update interval.
- **`[time_control.history_file]`** — wrfout output interval per domain and start offset.
- **`[time_control.summary_file]`** — Enable wrfxtrm daily diagnostic output.
- **`[time_control.z_level_file]`** — Enable wrfzlevels height-interpolated output at specified AGL heights.

### `[domains]`

Domain geometry (replaces the WPS `&geogrid` namelist section). Array fields must have one value per domain.

- **`run`** — Optional list of which domains to actually run (e.g. `[3, 4]`). When omitted, all domains run. The pipeline renumbers domains internally and renames output files back to the original numbering.
- **`dx`**, **`dy`**, **`map_proj`**, **`ref_lat`**, **`ref_lon`**, etc. — Projection and grid parameters.
- **`e_vert`**, **`p_top_requested`**, **`parent_time_step_ratio`** — Vertical levels, model top, and time-step ratios.
- Any key not consumed by the pipeline passes through directly to the WRF `&domains` namelist section.

### `[physics]` / `[dynamics]`

Optional overrides for WRF physics and dynamics schemes. Sensible defaults are built in (see `parameters_example.toml` for the full list with alternatives). Scalar values apply to all domains; arrays set per-domain values.

### `[fdda]`, `[bdy_control]`, `[grib2]`, `[namelist_quilt]`, `[diags]`

Direct WRF namelist passthrough sections. All keys are forwarded to their respective namelist sections.

### `[remote]`

Rclone configuration for data transfer (uses rclone config syntax).

- **`[remote.era5]`** — Source for ERA5 boundary-condition files.
- **`[remote.wrf]`** — Source for WRF output files (alternative to ERA5). Includes a `domain` key to specify which domain's wrfout files to use (e.g. `d03`). When present, the pipeline downloads wrfout files and converts them to WPS intermediate format using `wrf_to_int` instead of ERA5.
- **`[remote.output]`** — Destination for WRF output uploads, the `inputs/<run_uuid>/` handoff prefix (split workflow), and `logs/<run_uuid>/` failure-log uploads.

### `[ndown]`

Optional one-way nesting from a prior WRF run. Requires a single non-domain-1 domain (e.g. `run = [3]`). The `[ndown.input]` sub-section specifies the rclone remote where prior parent-domain wrfout files are stored.

**ndown and output variable filtering:** `ndown.exe` requires essentially **all** wrfout variables. It calls `input_history()` which reads every registered state variable from the coarse-domain wrfout file — all 3D atmospheric fields (U, V, W, T, P, PB, PH, PHB, MU, MUB, moisture species), all surface and soil fields, vertical coordinate data, and 119 additional fields flagged for ndown interpolation in the WRF Registry (land use, urban, radiation accumulators, ocean mixed-layer, etc.). Because of this, wrfout files that have been filtered with `output_presets` or `output_variables` should not be used as ndown input — missing variables will cause ndown to fail. The pipeline already handles this correctly: wrfout files downloaded for ndown input are never filtered.

### `[sentry]`

Optional Sentry error tracking. Provide a DSN and optional tags.

### `[no_docker]`

Local (non-Docker) mode. Uncomment and set four paths (`wps_path`, `wrf_path`, `data_path`, `geog_data_path`) to run outside the container. This is really only used for debugging.

## Running Locally

To run without Docker, uncomment the `[no_docker]` section in `parameters.toml` and set the required paths:

```toml
[no_docker]
wps_path = '/path/to/WPS-4.6.0'
wrf_path = '/path/to/WRF-4.7.1-ARW'
data_path = '/path/to/working/directory'
geog_data_path = '/path/to/WPS_GEOG'
```

Then run:

```bash
uv run wrf-auto-runs/main.py
```

Set `preprocess_only = true` in `parameters.toml` to run preprocessing only and upload to S3 inputs/<run_uuid>/; `wrf.exe` is skipped. Pair with a separate `wrf_only = true` invocation to do the WRF stage from those uploaded inputs.

## Pipeline Steps

When neither mode flag is set (single-stage), or when `preprocess_only=true`:

1. Validate ndown parameters and determine mode
2. Validate executables and resolve domain list
3. Configure namelists for the initial domain set
4. Run `geogrid.exe` (static geography processing)
5. Set time/date/output parameters and generate output file list
6. Generate WVT tracer mask files (`tracer_opt=4` only)
7. Download prior wrfout files (ndown mode only)
8. Download ERA5 or WRF data via rclone
9. Convert to WPS intermediate format (`era5_to_int` or `wrf_to_int`)
10. Process CCI SST to WPS Int (CCI SST source only)
11. Run `metgrid.exe` (horizontal interpolation; parallel via `n_cores_preprocess` on dmpar builds)
12. Auto-detect `num_metgrid_levels` from met_em files and update namelist
13. Run `real.exe` (vertical interpolation and initial/boundary conditions)
14. Run `ndown.exe` (ndown mode only)
15. (`preprocess_only=true`) Upload run inputs to `inputs/<run_uuid>/` on S3 and exit
16. Otherwise run `wrf.exe`, poll for completed output files, upload in real-time

When `wrf_only=true`:

1. Re-derive run context (domains, outputs, end_date, rename map) from `parameters.toml`
2. Download `inputs/<run_uuid>/` from S3 into the run directory
3. Run `wrf.exe`, poll for completed output files, upload in real-time
4. (if `cleanup_inputs=true`) Purge `inputs/<run_uuid>/` from S3

## WRF Output as Boundary Conditions

As an alternative to ERA5, the pipeline can use output from a prior WRF run as boundary conditions. Configure `[remote.wrf]` instead of `[remote.era5]` in `parameters.toml`:

```toml
[remote.wrf]
type = 's3'
provider = 'Mega'
endpoint = 'https://s3.ca-west-1.s4.mega.io'
access_key_id = ''
secret_access_key = ''
path = '/wrf-1k/output/'
domain = 'd03'
```

When `[remote.wrf]` is present, the pipeline:
1. Downloads wrfout files for the specified domain
2. Reads the source wrfout's vertical structure (eta levels and P_TOP) to generate appropriate log-spaced pressure levels
3. Converts to WPS intermediate format using `wrf_to_int` (with SST land-filling to prevent coastline interpolation artifacts)
4. Auto-detects `num_metgrid_levels` from the resulting met_em files and updates the WRF namelist accordingly

The number of pressure levels matches the source wrfout's eta level count, spaced logarithmically from 1000 hPa to P_TOP. This adapts automatically to any source WRF configuration.

## S3 Layout

Under `[remote.output].path`:

| Prefix | Contents | Lifetime |
|---|---|---|
| `inputs/<run_uuid>/` | `namelist.input`, `namelist.wps`, `wrfinput_d*`, `wrfbdy_d*`, `wrffdda_d*`, `wrflowinp_d*`, `trmask_d*`, `wrfrst_d*_<TIMESTAMP>` (restart only — only the latest per domain) | Created by preprocess; consumed by wrf-only; purged after successful WRF if `cleanup_inputs=true` AND NOT `restart.stop_after_upload` |
| `<run_uuid>/wrfout*` | Main WRF output files | Uploaded during `monitor_wrf`; persisted |
| `logs/<run_uuid>/rsl.*` | `rsl.error.*` / `rsl.out.*` from failed `real.exe` / `ndown.exe` / `wrf.exe` | Uploaded only on failure |

## Output Files

| File prefix | Description |
|---|---|
| `wrfout` | Main history output |
| `wrfxtrm` | Daily diagnostic extremes (requires `summary_file` enabled) |
| `wrfzlevels` | Height-interpolated fields (requires `z_level_file` enabled) |

All output files are uploaded to `[remote.output]` during the run and deleted locally after upload.

## Image Matrix

| Image | Compiler | WPS | Use |
|---|---|---|---|
| `mullenkamp/wrf-auto-runs-wvt:1.7` | gfortran | dmpar | Preprocess stage (parallel metgrid). Also fine for single-stage short runs. |
| `mullenkamp/wrf-auto-runs-intel-wvt:1.7` | Intel oneAPI | serial | WRF stage (faster `wrf.exe`). Also fine for single-stage runs where WRF dominates. |
| `mullenkamp/wrf-auto-runs:2.7` | gfortran | dmpar | Non-WVT variant. |

## Project Structure

```
wrf-auto-runs/           Python pipeline modules
slurm_scripts/           SLURM job scripts (cluster-specific)
test_scripts/            Helper scripts (add_geog.sh, run_wrf.sh, etc.)
parameters_example.toml  Annotated configuration template
docker-compose.yml       Docker run configuration (single-stage default)
intel_wvt/               Intel WVT pipeline image build context
gfortran_wvt/            gfortran WVT pipeline image build context
intel_wrf/               Intel non-WVT pipeline image build context
gfortran_wvt_ref/        gfortran reference (WRF 4.3.3) build context
```
