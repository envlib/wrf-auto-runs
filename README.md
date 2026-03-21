# wrf-era5-auto

Automated pipeline to run the WRF (Weather Research and Forecasting) model using ERA5 reanalysis data as boundary and initial conditions. All configuration is driven by a single `parameters.toml` file. Runs inside a Docker container with WRF 4.6.1-ARW and WPS 4.6.0 pre-installed.

## Prerequisites

- Linux with Docker installed (your user must be in the `docker` group)
- WPS_GEOG static geography data — download with `test_scripts/add_geog.sh`

## Quick Start

```bash
# Edit parameters.toml — at minimum fill in [domains], [time_control], and [remote] credentials
cp parameters_example.toml parameters.toml

# Edit the docker-compose.yml to map the local WPS_GEOG path
docker-compose up -d      # Run and detach from process 
docker-compose logs -f    # Look at the logs go!

# Once everything has finished/failed you need to clean up the docker-compose instance
docker-compose down
```
## docker-compose.yml
### WPS_GEOG path
The local WPS_GEOG path must be mapped to /WPS_GEOG in the docker image.
It should be something like this:
```
- /local/path/WPS_GEOG:/WPS_GEOG
```
The first part is the local path then the docker image path (with a colon in between)

The static data for NZ can be downloaded and extracted like this:
```bash
wget -N https://b2.envlib.xyz/file/envlib/wrf/static_data/nz_wps_geog.tar.zst
tar --zstd -xf nz_wps_geog.tar.zst
rm nz_wps_geog.tar.zst
```

### Mount the data directory
Internally, WRF in the docker image runs all processes in the /data path (in the docker image). The user can mount this path to their local drive to see the processes and data.

```
- /local/path/test_data:/data
```

## Configuration

All settings live in `parameters.toml`. See `parameters_example.toml` for a fully annotated template.

### Top-level

- **`n_cores`** — Number of MPI processes for `wrf.exe` (max ~24 before efficiency drops).
- **`output_variables`** — Optional list of wrfout variables to retain. Coordinate and auxiliary 3D variables are included automatically. Comment out to keep all variables.

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
- **`[remote.output]`** — Destination for WRF output uploads.

### `[ndown]`

Optional one-way nesting from a prior WRF run. Requires a single non-domain-1 domain (e.g. `run = [3]`). The `[ndown.input]` sub-section specifies the rclone remote where prior parent-domain wrfout files are stored.

### `[sentry]`

Optional Sentry error tracking. Provide a DSN and optional tags.

### `[no_docker]`

Local (non-Docker) mode. Uncomment and set four paths (`wps_path`, `wrf_path`, `data_path`, `geog_data_path`) to run outside the container. This is really only used for debugging.

## Running Locally

To run without Docker, uncomment the `[no_docker]` section in `parameters.toml` and set the required paths:

```toml
[no_docker]
wps_path = '/path/to/WPS-4.6.0'
wrf_path = '/path/to/WRF-4.6.1-ARW'
data_path = '/path/to/working/directory'
geog_data_path = '/path/to/WPS_GEOG'
```

Then run:

```bash
uv run wrf-era5-auto/main.py
```

Use `main_alt.py` instead to run preprocessing only (steps 1-11, no WRF execution).

## Pipeline Steps

1. Validate ndown parameters and determine mode
2. Validate namelists and resolve domain list
3. Configure namelists for the initial domain set
4. Run `geogrid.exe` (static geography processing)
5. Set time/date/output parameters and generate output file list
6. Upload namelists to remote storage
7. Download prior wrfout files (ndown mode only)
8. Download ERA5 data via rclone
9. Convert ERA5 NetCDF to WPS intermediate format
10. Run `metgrid.exe` (horizontal interpolation)
11. Run `real.exe` (vertical interpolation and initial/boundary conditions)
12. Run `ndown.exe` (ndown mode only)
13. Run `wrf.exe`, poll for completed output files, upload in real-time

## Output Files

| File prefix | Description |
|---|---|
| `wrfout` | Main history output |
| `wrfxtrm` | Daily diagnostic extremes (requires `summary_file` enabled) |
| `wrfzlevels` | Height-interpolated fields (requires `z_level_file` enabled) |

All output files are uploaded to `[remote.output]` during the run and deleted locally after upload.

## Project Structure

```
wrf-era5-auto/           Python pipeline modules
test_scripts/            Helper scripts (add_geog.sh, run_wrf.sh, run_wrf.sl)
parameters_example.toml  Annotated configuration template
docker-compose.yml       Docker run configuration
```
