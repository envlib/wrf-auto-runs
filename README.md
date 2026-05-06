# wrf-auto-runs

Automated pipeline to run the WRF (Weather Research and Forecasting) model using ERA5 reanalysis data or WRF output as boundary and initial conditions. All configuration is driven by a single `parameters.toml` file. Runs inside a Docker container with WRF 4.7.1-ARW and WPS 4.6.0 pre-installed.

The recommended workflow is **unified per-chunk**: with `[restart].enable=true` set in `parameters.toml`, each container invocation runs its own preprocess + WRF for one chunk of the simulation (length `interval_days`). For `stop_after_upload=true` the container exits after one chunk and SLURM submits a chained job per chunk; for `stop_after_upload=false` the container loops internally through all chunks. Only wrfrst (chunk handoff) and namelists (debug archive) round-trip through S3 — wrfbdy/wrffdda/wrfinput/wrflowinp/trmask are local-only, which avoids hours of redundant network transfer for long FDDA-enabled runs.

A **single-stage** workflow (one container does everything end-to-end) is supported for short runs that fit in one job. A **legacy split workflow** (preprocess job → WRF job, handed off via S3) is preserved for backwards compat.

## Prerequisites

- Linux with Docker installed (your user must be in the `docker` group)
- WPS_GEOG static geography data — download with `test_scripts/add_geog.sh`

## Quick Start (Unified Per-Chunk — Recommended)

```bash
# Edit parameters.toml — at minimum fill in [domains], [time_control], [remote] credentials,
# and the [restart] section:
#   enable = true
#   interval_days = N
#   stop_after_upload = false   # local dev: one container loops through all chunks
cp parameters_example.toml parameters.toml

# Run the chunked pipeline. With stop_after_upload=false, one container loops through all
# chunks until sim_end. With stop_after_upload=true, ./run_local.sh re-runs once per chunk
# (mirrors the SLURM chained-jobs pattern locally — useful for testing the resume-from-S3 path).
./run_local.sh

# Or run a single chunk for iteration / debugging:
./run_one_chunk.sh   # SLURM cluster
docker compose run --rm chunk   # local
```

The working unified-mode project lives at `wrf-runs/projects/.../v33_3km_wvt_sst_max_pp/`. To start a new project, copy that directory's `chunk.sl`, `run_wrf_<cluster>.sh`, `run_one_chunk.sh`, `run_local.sh`, `lib.sh`, and `docker-compose.yml`, then customise `parameters.toml`.

## Quick Start (Single-Stage)

For short runs that fit in one job and don't need restart support:

```bash
cp parameters_example.toml parameters.toml
# Leave the [restart] section commented out

docker compose up -d           # Run and detach from process
docker compose logs -f         # Look at the logs go!
docker compose down            # Clean up after completion / failure
```

## Legacy Split Workflow (Preprocess + WRF)

The split workflow runs preprocessing (steps through `real.exe`) in one container, hands off `wrfinput_d*` / `wrfbdy_d*` / `namelist.input` / etc. via S3, and runs `wrf.exe` in a second container. The unified per-chunk workflow supersedes this for new work, but the split mode is preserved for backwards compat.

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
    image: mullenkamp/wrf-auto-runs-intel-wvt:1.8
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
source ./lib.sh
export RUN_UUID="$(resolve_run_uuid ./parameters.toml)"
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
- **`preprocess_only`** — Legacy split-pipeline mode. Run preprocessing through `real.exe`, upload `inputs/<run_uuid>/` to S3, exit. Mutually exclusive with `wrf_only`.
- **`wrf_only`** — Legacy split-pipeline mode. Skip preprocessing, download `inputs/<run_uuid>/` from S3, run `wrf.exe`. Requires `run_uuid` to be set (env or TOML).
- **`cleanup_inputs`** — Default `true`. Single cleanup knob: deletes intermediate preprocessing files (met_em, ERA5 NetCDF, WPS int files) locally as the run progresses, AND purges `inputs/<run_uuid>/` from S3 after a successful WRF run. Set to `false` to keep everything for inspection. Note: `[restart].stop_after_upload=true` overrides this for the inputs-prefix purge (the prefix is shared across chained jobs).
- **`run_uuid`** — Optional 13-char hex identifier for the run. Normally generated fresh per run; set explicitly to resume a chunked run against an existing `inputs/<run_uuid>/` prefix, or to make a run reproducible by uuid. Precedence: env var > TOML > generated.
- **`output_presets`** — Optional string or list of named variable presets (e.g. `'wrf_to_int'`). Each preset expands to the set of wrfout variables required by the named tool. Variables from all selected presets are merged together.
- **`output_variables`** — Optional list of additional wrfout variables to retain. Merged with any preset variables. Coordinate and auxiliary 3D variables are included automatically. Comment out both `output_presets` and `output_variables` to keep all variables.

### `[restart]`

Activates the unified per-chunk workflow (the recommended mode for production runs). With `enable=true` and neither `preprocess_only` nor `wrf_only` set, each container invocation runs its own preprocess + WRF for one `interval_days` chunk; chunk boundaries auto-detect from the latest wrfrst on S3. Also configures restart-aware behaviour for the legacy `wrf_only` mode if combined with that flag.

- **`enable`** (default `false`) — Master switch.
- **`interval_days`** (required when `enable=true`) — Chunk window length and WRF `restart_interval` (set to `interval_days * 24 * 60` minutes). Each chunk writes a wrfrst at chunk_end and uploads it to `inputs/<run_uuid>/` (only the latest per domain is kept on S3; older ones are deleted automatically).
- **`stop_after_upload`** (default `false`) — When true, each invocation processes one chunk and exits cleanly (achieved by overriding `end_date*` in the namelist to `chunk_end`). Designed for SLURM chained jobs: queue multiple `sbatch chunk.sl` invocations reusing the same `RUN_UUID` (the per-project `run_wrf_<cluster>.sh` orchestrator computes how many chunks are needed and submits them via `--dependency=afterany`). When true, **disables auto-cleanup of `inputs/<run_uuid>/` regardless of `cleanup_inputs`** — manually purge after the simulation completes. When false, the chunk loop runs internally until sim_end (best for local docker-compose dev).

Notes on chunked-run behaviour:

- `apply_restart_namelist` automatically sets `write_hist_at_0h_rst=.true.` so each restart chunk writes a wrfout frame at chunk_start; combined with WRF's `NF_CLOBBER` open-for-write semantics, the next chunk's full 8-frame wrfout file overwrites the prior chunk's 1-frame placeholder at the same filename. Net effect: `Feb13_00:00:00.nc`, `Feb14_00:00:00.nc`, etc. all end up as 8-frame day files after the chain finishes.
- The wrfout file at chunk_end IS skipped on upload when chunk_end falls exactly at midnight (00:00:00) — that file is a "deceptive partial day" containing just the rollover frame, which is captured either in the next chunk's clobber or in the final chunk's wrfrst. Mid-day end_dates produce non-deceptive multi-frame final files and are uploaded normally.

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

### Unified per-chunk (`[restart].enable=true`, neither split flag set)

`run_chunked_pipeline` loops the following until `chunk_start >= sim_end` (or returns after one iteration if `stop_after_upload=true`):

1. `detect_remote_restart_state(run_uuid)` — `rclone lsf` against `inputs/<run_uuid>/`; finds latest wrfrst timestamp (or None on chunk 1)
2. `chunk_start = restart_state if restart_state else sim_start`; exit if `chunk_start >= sim_end`
3. `chunk_end = min(chunk_start + interval_days, sim_end)`
4. `params.set_chunk_dates(chunk_start, chunk_end)` — mutates `params.file['time_control']`
5. Validate ndown parameters and resolve domain list
6. Configure namelists, run `geogrid.exe`, configure time/output params
7. Generate WVT tracer mask files (`tracer_opt=4` only)
8. Download ERA5 or WRF data for the chunk window via rclone
9. Convert to WPS intermediate format (`era5_to_int` or `wrf_to_int`); optional CCI SST processing
10. Run `metgrid.exe` (parallel via `n_cores_preprocess` on dmpar builds), auto-detect `num_metgrid_levels`
11. Run `real.exe` — `run_real` rmtrees `run_path` so every iteration starts fresh
12. If `restart_state is not None`: `download_wrfrst_to_run_path(run_uuid)` pulls the prior chunk's wrfrst from S3
13. `apply_restart_namelist(restart_state, restart_interval_minutes, end_date_override=chunk_end)` — always called
14. `upload_chunk_namelists(run_uuid)` — uploads only `namelist.input` + `namelist.wps` to `inputs/<run_uuid>/`
15. `monitor_wrf` runs `wrf.exe`; uploads wrfout / wrfxtrm / wrfzlevels / wrfrst as they complete
16. If `stop_after_upload=true`: return; else loop to step 1

### Single-stage (no restart, no split flag) or `preprocess_only=true`

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

### Legacy `wrf_only=true`

1. Re-derive run context (domains, outputs, end_date, rename map) from `parameters.toml`
2. Download `inputs/<run_uuid>/` from S3 into the run directory
3. (if `[restart].enable=true`) Detect any wrfrst, apply restart namelist edits
4. Run `wrf.exe`, poll for completed output files, upload in real-time
5. (if `cleanup_inputs=true` AND not `restart.stop_after_upload`) Purge `inputs/<run_uuid>/` from S3

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
| `wrfout_d*` / `wrfxtrm_d*` / `wrfzlevels_d*` (directly under root, NO `<run_uuid>/` prefix) | Main WRF output files | Uploaded during `monitor_wrf`; persisted |
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
| `mullenkamp/wrf-auto-runs-intel-wvt:1.8` | Intel oneAPI | dmpar | **Default for all modes** — unified per-chunk (preprocess + WRF in one image), single-stage, and the legacy split-pipeline WRF stage |
| `mullenkamp/wrf-auto-runs-wvt:1.7` | gfortran | dmpar | Backup image. Legacy split-pipeline preprocess stage; short single-stage runs |
| `mullenkamp/wrf-auto-runs:2.7` | gfortran | dmpar | Non-WVT variant |

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
