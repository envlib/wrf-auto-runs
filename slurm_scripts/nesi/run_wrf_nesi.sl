#!/bin/bash -e
#SBATCH --job-name=wrf-auto
#SBATCH --nodes=1                     # node count
#SBATCH --account=nesi99999           # Replace with your NeSI project code
#SBATCH --partition=genoa             # Or: large, bigmem, hgx — check nesi.org.nz
#SBATCH --time=24:00:00
#SBATCH --ntasks=8                    # MPI ranks for wrf.exe (maps to n_cores)
#SBATCH --mem=32G
#SBATCH --cpus-per-task=2             # Request 2 logical CPUs per task
#SBATCH --hint=nomultithread          # Ensure those 2 CPUs are on the same physical core
#SBATCH --ntasks-per-core=1           # Explicitly restrict to 1 task per physical core
#SBATCH --output=wrf-auto_%j.log
#SBATCH --error=wrf-auto_%j.err

# =============================================================================
# WRF-Auto Pipeline — Apptainer on NeSI
#
# Runs the full WRF-Auto pipeline (main.py) inside an Apptainer container
# converted from the Docker image mullenkamp/wrf-auto-runs.
#
# Prerequisites (one-time setup from a login node):
#
#   1. Pull the SIF image (update IMAGE_VERSION in Configuration to match):
#        module load Apptainer
#        apptainer pull docker://mullenkamp/wrf-auto-runs:<VERSION>
#        mv wrf-auto-runs_<VERSION>.sif /nesi/nobackup/$PROJECT_CODE/$USER/
#
#   2. Download WPS_GEOG static data:
#        See https://www2.mmm.ucar.edu/wrf/users/download/get_sources_wps_geog.html
#        Extract to /nesi/nobackup/$PROJECT_CODE/WPS_GEOG/
#
#   3. Create parameters.toml from parameters_example.toml with your settings.
#      Do NOT include a [no_docker] section — the container uses Docker paths.
#
#   4. Network access: rclone uploads/downloads require outbound network access.
#      On NeSI, compute nodes may have restricted networking. Check with NeSI
#      support or use a slurm_param that allows network access if needed.
# =============================================================================

# ---- Configuration ----------------------------------------------------------

PROJECT_CODE="nesi99999"                                        # NeSI project code
IMAGE_NAME="wrf-auto-runs"                                      # Docker/SIF image name
IMAGE_VERSION="2.2"                                             # Docker/SIF image version
SCRATCH="/nesi/nobackup/${PROJECT_CODE}/${USER}"                # Scratch/nobackup base
SIF_PATH="${SCRATCH}/${IMAGE_NAME}_${IMAGE_VERSION}.sif"        # Apptainer SIF image
WPS_GEOG_PATH="${SCRATCH}/WPS_GEOG"                             # Static geography data
PARAMS_FILE="$(pwd)/parameters.toml"                            # Path to parameters.toml
DATA_DIR="${SCRATCH}/wrf_runs/${SLURM_JOB_ID}"                  # Per-job working directory

# Optional overrides — uncomment to override values in parameters.toml.
# These map to the env var override mechanism in params.py.
# START_DATE="2020-01-01 00:00:00"
# END_DATE="2020-01-03 00:00:00"
# DOMAINS="3,4"
# DURATION_HOURS=48

# ---- Apptainer cache setup (redirect to scratch) ---------------------------

export APPTAINER_CACHEDIR="${SCRATCH}/.apptainer/cache"
export APPTAINER_TMPDIR="${SCRATCH}/.apptainer/tmp"
mkdir -p "${APPTAINER_CACHEDIR}" "${APPTAINER_TMPDIR}"

# ---- Load modules -----------------------------------------------------------

module purge 2>/dev/null
module load Apptainer

# ---- Validation checks ------------------------------------------------------

if [ ! -f "${SIF_PATH}" ]; then
    echo "ERROR: SIF image not found at ${SIF_PATH}"
    echo "Pull it first: apptainer pull docker://mullenkamp/${IMAGE_NAME}:${IMAGE_VERSION}"
    exit 1
fi

if [ ! -f "${PARAMS_FILE}" ]; then
    echo "ERROR: parameters.toml not found at ${PARAMS_FILE}"
    exit 1
fi

if [ ! -d "${WPS_GEOG_PATH}" ]; then
    echo "ERROR: WPS_GEOG directory not found at ${WPS_GEOG_PATH}"
    exit 1
fi

# ---- Create per-job data directory ------------------------------------------

mkdir -p "${DATA_DIR}"
echo "Job ${SLURM_JOB_ID}: data directory = ${DATA_DIR}"

# ---- Build bind mounts ------------------------------------------------------

BIND_ARGS="${PARAMS_FILE}:/app/parameters.toml"
BIND_ARGS="${BIND_ARGS},${WPS_GEOG_PATH}:/WPS_GEOG:ro"
BIND_ARGS="${BIND_ARGS},${DATA_DIR}:/data"

# ---- Build environment variable overrides -----------------------------------
# Use separate --env flags (comma-separated form can mangle values).
# --contain prevents NeSI's admin bind-paths from injecting host MPI libs.
# HYDRA_LAUNCHER=fork  — tell MPICH Hydra to fork locally, not via Slurm/SSH.
# HYDRA_IFACE=lo       — force loopback for intra-container MPI communication.

ENV_ARGS=(--env "TZ=UTC")
ENV_ARGS+=(--env "n_cores=${SLURM_NTASKS}")
ENV_ARGS+=(--env "HYDRA_LAUNCHER=fork")
ENV_ARGS+=(--env "HYDRA_IFACE=lo")

if [ -n "${START_DATE:-}" ]; then
    ENV_ARGS+=(--env "start_date=${START_DATE}")
fi
if [ -n "${END_DATE:-}" ]; then
    ENV_ARGS+=(--env "end_date=${END_DATE}")
fi
if [ -n "${DOMAINS:-}" ]; then
    ENV_ARGS+=(--env "domains=${DOMAINS}")
fi
if [ -n "${DURATION_HOURS:-}" ]; then
    ENV_ARGS+=(--env "duration_hours=${DURATION_HOURS}")
fi

# ---- Run the pipeline -------------------------------------------------------

echo "Starting WRF-Auto pipeline at $(date)"
echo "SIF: ${SIF_PATH}"
echo "Cores: ${SLURM_NTASKS}"
echo "Data dir: ${DATA_DIR}"

apptainer exec \
    --cleanenv \
    --contain \
    --writable-tmpfs \
    --bind "${BIND_ARGS}" \
    "${ENV_ARGS[@]}" \
    "${SIF_PATH}" \
    bash -c "cd /app && uv run python -u main.py"

echo "Pipeline finished at $(date)"

# ---- Optional cleanup (uncomment to remove intermediate files) --------------

# echo "Cleaning up intermediate files..."
# rm -rf "${DATA_DIR}"
