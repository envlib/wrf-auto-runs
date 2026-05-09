# Slurm Scripts for WRF-Auto Pipeline

These scripts run the WRF-Auto pipeline inside an Apptainer container on HPC clusters managed by Slurm.

The pipeline supports two execution patterns:

1. **Unified per-chunk** (recommended for long runs) — `[restart].enable=true` in `parameters.toml`, `stop_after_upload=true` for chained-job behaviour. One SLURM job per chunk, each container does its own preprocess + WRF for `interval_days`. wrfbdy/wrffdda/wrfinput/wrflowinp/trmask are local-only (never round-trip through S3); only wrfrst + namelists persist. Working example: `run_wrf_hetzner.sh` + `chunk.sl` in `wrf-runs/projects/.../v33_3km_wvt_sst_max_pp/`.
2. **Single-stage** — one SLURM job runs preprocessing and WRF end-to-end. Simple; fine for short runs that fit in a single job. The `run_wrf_*.sl` scripts in this directory use this pattern.

## Prerequisites

### 1. SIF Image

The pipeline runs inside an Apptainer (SIF) image converted from one of the Docker pipeline images. Each script has `IMAGE_NAME` and `IMAGE_VERSION` variables at the top of its Configuration section — update `IMAGE_VERSION` when switching to a new release.

**Image matrix:**

| Image | Compiler | WPS | Used for |
|---|---|---|---|
| `mullenkamp/wrf-auto-runs-wvt:1.7` | gfortran | dmpar (parallel metgrid) | preprocess stage and short single-stage WVT runs |
| `mullenkamp/wrf-auto-runs-intel-wvt:1.8` | Intel oneAPI | dmpar | **Default**: unified Phase 3 chunked mode (preprocess + WRF), Phase 2 WRF stage, single-stage runs |
| `mullenkamp/wrf-auto-runs:2.7` | gfortran | dmpar | non-WVT variant |

**Pulling the SIF (Option A: recommended — pre-built download):**

```bash
wget -N https://b2.envlib.xyz/file/envlib/sif/<image-name>_<VERSION>.sif
```

**Option B: ORAS pull (avoids squashfs conversion):**

```bash
module load Apptainer
export VERSION=1.8
apptainer pull oras://registry-1.docker.io/mullenkamp/wrf-auto-runs-intel-wvt:${VERSION}-sif
mv wrf-auto-runs-intel-wvt_${VERSION}-sif.sif wrf-auto-runs-intel-wvt_${VERSION}.sif
```

**Option C: Docker Hub via Apptainer:**

```bash
module load Apptainer
export VERSION=1.8
apptainer pull docker://mullenkamp/wrf-auto-runs-intel-wvt:${VERSION}
```

May fail on memory-constrained HPC login nodes during squashfs build. Build locally and `scp` the SIF as a workaround. If you only have Docker locally:

```bash
docker pull mullenkamp/wrf-auto-runs-intel-wvt:${VERSION}
docker save mullenkamp/wrf-auto-runs-intel-wvt:${VERSION} -o image.tar
apptainer build wrf-auto-runs-intel-wvt_${VERSION}.sif docker-archive://image.tar
```

**Refreshing a SIF after a Docker tag is republished:** Apptainer caches by tag. If the same Docker tag is re-pushed, you must clear the cache *and* delete the existing SIF before pulling, otherwise apptainer reuses the stale digest:

```bash
apptainer cache clean --force \
  && rm -f /shared/wrf_data/<image-name>_<VERSION>.sif \
  && apptainer pull --force --dir /shared/wrf_data docker://mullenkamp/<image-name>:${VERSION}
```

Better long-term: bump the tag every time the image content changes.

### 2. WPS_GEOG Static Data

**Option A: NZ-specific dataset (recommended for New Zealand domains)**

```bash
wget -N https://b2.envlib.xyz/file/envlib/wrf/static_data/nz_wps_geog.tar.zst -O nz_wps_geog.tar.zst
tar --zstd -xf nz_wps_geog.tar.zst
rm nz_wps_geog.tar.zst
```

**Option B: Full WPS_GEOG from NCAR**

The complete global dataset: https://www2.mmm.ucar.edu/wrf/users/download/get_sources_wps_geog.html

### 3. parameters.toml

Copy `parameters_example.toml` to `parameters.toml` and configure with your simulation settings and remote storage credentials. Do **not** include a `[no_docker]` section — the container uses its built-in Docker-mode paths (`/data`, `/WPS_GEOG`, `/WRF`, `/WPS`).

### 4. Network Access

The pipeline uses `rclone` to download ERA5 data and upload WRF output. Compute nodes may have restricted network access depending on your cluster. Check with your HPC support if rclone transfers fail.

## Scripts

### Single-stage scripts (one SLURM job)

#### run_wrf_nesi.sl — Single Run

Runs one WRF simulation end-to-end. Edit the configuration section at the top to set paths for your environment (`PROJECT_CODE`, `SCRATCH`, `SIF_PATH`, `WPS_GEOG_PATH`).

```bash
sbatch slurm_scripts/run_wrf_nesi.sl
```

**Override parameters via environment variables:**

```bash
sbatch --export=ALL,START_DATE="2020-01-01 00:00:00",DURATION_HOURS=48 slurm_scripts/run_wrf_nesi.sl
```

Available overrides: `START_DATE`, `END_DATE`, `DOMAINS`, `DURATION_HOURS`.

#### run_wrf_nesi_array.sl — Multiple Runs (Job Array)

Submits multiple WRF runs as a Slurm job array, each with a different start date computed from a base date and step size:

```
start_date = BASE_DATE + (SLURM_ARRAY_TASK_ID * STEP_HOURS)
```

```bash
# BASE_DATE="2020-01-01 00:00:00", STEP_HOURS=48 (set in script)
sbatch --array=0-11 slurm_scripts/run_wrf_nesi_array.sl
```

#### run_wrf_nesi_csv_array.sl — Multiple Runs (Job Array, CSV-based)

Each task reads its `start_date` and `end_date` from a CSV file (`slurm_scripts/periods.csv`), using `SLURM_ARRAY_TASK_ID` as a 0-based row index.

```csv
start_date,end_date
2020-01-01 00:00:00,2020-01-03 00:00:00
2020-01-03 00:00:00,2020-01-05 00:00:00
```

```bash
sbatch slurm_scripts/run_wrf_nesi_csv_array.sl
```

Auto-detect array range from CSV:

```bash
ROWS=$(( $(wc -l < slurm_scripts/run_periods.csv) - 1 ))
sbatch --array=0-$((ROWS - 1)) slurm_scripts/run_wrf_nesi_csv_array.sl
```

#### run_wrf_hetzner_csv_array.sl — Multiple Runs on Hetzner (Job Array, CSV-based)

Same CSV-based approach configured for the Hetzner local cluster (Intel image, local NVMe scratch, shared NFS).

#### run_wrf_uc.sl — University of Canterbury HPC

Variant configured for a different HPC environment.

### Unified per-chunk scripts (recommended for long runs)

For long simulations with FDDA (where an up-front-preprocess wrffdda would be ~200 GB and re-downloaded by every chunk), the unified per-chunk pattern moves preprocess INSIDE each chunk's container. wrfbdy/wrffdda/wrfinput/wrflowinp/trmask never round-trip through S3 — only wrfrst (chunk handoff) and namelists (debug archive) persist. Per-project files:

- **`run_wrf_<cluster>.sh`** — Plain bash orchestrator (NOT a SLURM job). Run with `./run_wrf_<cluster>.sh`. Reads `interval_days` and sim window from `parameters.toml` via the awk-based `toml_get` helper in `lib.sh`, computes `num_chunks = ceil(days/interval) + 1` (the +1 trips the early-exit branch and no-ops), and submits a chained `chunk.sl` job per chunk via `--dependency=afterany`.
- **`chunk.sl`** — SLURM job (intel image, `wrf-auto-runs-intel-wvt:1.8`). One container = one chunk. Auto-detects which chunk it is via S3 wrfrst state. Runs preprocess + WRF for its `[chunk_start, chunk_end]` window. Sets both `n_cores=${SLURM_NTASKS}` and `n_cores_preprocess=${SLURM_NTASKS}` so the same allocation runs preprocess (dmpar metgrid/real) and wrf.exe at full width.
- **`run_one_chunk.sh`** — Test helper. Submits a single `chunk.sl` job with the current run_uuid; useful for iterating on chunk behaviour without launching the full chain.
- **`run_local.sh`** — Local docker-compose runner. Resolves `RUN_UUID` and runs `docker compose up`; with `stop_after_upload=false` it loops through all chunks in one container.
- **`lib.sh`** — Shared bash helpers (`toml_get`, `gen_uuid`, `resolve_run_uuid`) sourced by all three shell scripts. Copy alongside when cloning a new project dir.

In `parameters.toml`:

```toml
[restart]
enable = true
interval_days = 7
stop_after_upload = true   # required for chained-chunk pattern; false = loop-in-container (local dev)
```

Auto-cleanup of `inputs/<run_uuid>/` is disabled when `stop_after_upload=true` (the prefix is shared across chained jobs); manually purge after the chain completes. With `stop_after_upload=false` the inputs prefix is purged automatically on clean exit.

The working unified-mode pattern lives in `wrf-runs/projects/.../v33_3km_wvt_sst_max_pp/`. To create a unified-mode run for another project, copy those five files (`run_wrf_<cluster>.sh`, `chunk.sl`, `run_one_chunk.sh`, `run_local.sh`, `lib.sh`) plus `parameters.toml` and `docker-compose.yml`.

### Known gotcha: `/tmp` size with `--contain --writable-tmpfs`

With `--contain --writable-tmpfs` (used by all scripts here), the in-container `/tmp` defaults to a tiny tmpfs (~64 MB on most builds). `era5_dl` streams downloads through `/tmp` for atomic writes, so the first few small files succeed and later ones silently truncate — manifesting as "metgrid failed because nothing was downloaded" with no error from the download step.

**Fix in every script:**

```bash
mkdir -p "${LOCAL_SCRATCH}/apptainer_tmp"
BIND_ARGS="${BIND_ARGS},${LOCAL_SCRATCH}/apptainer_tmp:/tmp"
```

This binds `/tmp` inside the container to a real disk path on `LOCAL_SCRATCH`, giving rclone the full size of the local NVMe.

## How It Works

The scripts use Apptainer bind mounts to inject host files into the container, replicating the same setup as `docker-compose.yml`:

| Host Path | Container Path | Purpose |
|---|---|---|
| `parameters.toml` | `/app/parameters.toml` | Pipeline configuration |
| WPS_GEOG directory | `/WPS_GEOG` (read-only) | Static geography data |
| Per-job scratch directory | `/data` | Working directory for namelists, metgrid, WRF output |
| `${LOCAL_SCRATCH}/apptainer_tmp` | `/tmp` | Real-disk-backed `/tmp` (see gotcha above) |

WRF, WPS, Python, and all dependencies are baked into the SIF image — no bind mounts needed for those.

Key flags:
- **`--writable-tmpfs`** — The pipeline creates a symlink inside `/WPS/geogrid/` for Noah-MP. Without this, the read-only SIF filesystem would block it.
- **`HYDRA_LAUNCHER=fork`** — MPICH inside the container detects Slurm environment variables and tries to use `srun`, which doesn't exist in the container. This forces MPICH's Hydra process manager to use `fork` instead.
- **`HYDRA_IFACE=lo`** — Forces MPICH to use the loopback interface for intra-container MPI communication, avoiding issues with host network interfaces (e.g. InfiniBand) that may not work inside the container.
- **`n_cores=$SLURM_NTASKS`** — Syncs `wrf.exe`'s MPI rank count with the Slurm allocation. Used by single-stage and WRF-stage scripts only.
- **`n_cores_preprocess=$SLURM_NTASKS`** — Syncs metgrid/real/ndown ranks with the Slurm allocation. Both the intel and gfortran WPS images are dmpar-built so this parallelizes metgrid in either. In unified per-chunk mode `chunk.sl` exports both `n_cores` and `n_cores_preprocess` to the same value so the single allocation runs preprocess and wrf.exe at full width.
- **`run_uuid=$RUN_UUID`** — Shared between preprocess and WRF stages so they target the same `inputs/<run_uuid>/` S3 prefix. Generated once by the orchestrator and exported to both jobs.

### Container isolation: `--contain` and `--cleanenv`

All scripts use `--contain` and `--cleanenv` for robust MPI behaviour across HPC environments:

- **`--contain`** — Prevents Apptainer from applying admin-configured bind mounts. Some HPC sites (e.g. NeSI) configure Apptainer to inject host MPI libraries into containers (the "hybrid MPI" model for InfiniBand performance). This replaces the container's own MPI with the host's MPICH, which then tries to use Slurm's PMI for process management. Since PMI isn't accessible from inside the container, `mpirun` fails with `HYD_pmci_wait_for_completion`. `--contain` blocks these admin bind mounts while still honoring the explicit `--bind` flags defined in the script. Note: `--contain` also disables Apptainer's auto-mount of `/tmp`, which is why we explicitly bind a host path to `/tmp` (see gotcha above).
- **`--cleanenv`** — Strips all host environment variables from the container, preventing Slurm variables (`SLURM_*`, `PMI_*`) from leaking in and interfering with the container's MPI. Only variables explicitly passed via `--env` are set inside the container. Note: `--cleanenv` also strips the `TZ` (timezone) variable and `--contain` blocks access to `/etc/localtime`, so `TZ=UTC` is explicitly passed via `--env`. The Python code also uses `pendulum.now('UTC')` rather than `pendulum.now()` to avoid depending on the container's timezone configuration.

Each job gets an isolated data directory (using `$SLURM_JOB_ID` or `$SLURM_ARRAY_JOB_ID_$SLURM_ARRAY_TASK_ID`), so multiple runs don't interfere with each other.

## Monitoring Jobs

```bash
squeue -u $USER          # List your running/queued jobs
scancel <job_id>          # Cancel a job
scancel <array_job_id>    # Cancel all tasks in an array

sinfo --format="%P %C"
```

## Completed Jobs

```bash
sacct -j <job_id> --format=JobID,Elapsed,Timelimit,TotalCPU,Alloc%2,MaxRSS,State --units=G
```

Log files are written to the submission directory:
- Single run: `wrf-auto_<job_id>.log`
- Array: `wrf-auto_<array_job_id>_<task_id>.log`
- Chunked: `log_chunk_<job_id>.log`
