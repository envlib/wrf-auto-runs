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
#SBATCH --output=wrf-auto_%A_%a.log
#SBATCH --error=wrf-auto_%A_%a.err
#SBATCH --array=0-11                  # Adjust range for number of runs

# =============================================================================
# WRF-Auto Pipeline — Apptainer on NeSI (Job Array)
#
# Submits multiple WRF runs as a Slurm job array, each starting at a different
# date. The start date for each task is computed from a base date and a step
# size (in hours). Duration is set globally in parameters.toml (duration_hours).
#
# Example: BASE_DATE="2020-01-01 00:00:00", STEP_HOURS=48, --array=0-11
#   Task 0:  2020-01-01 00:00:00
#   Task 1:  2020-01-03 00:00:00
#   Task 2:  2020-01-05 00:00:00
#   ...
#   Task 11: 2020-01-23 00:00:00
#
# Usage:
#   sbatch slurm_scripts/run_wrf_nesi_array.sl
#   sbatch --array=0-5 slurm_scripts/run_wrf_nesi_array.sl    # override range
#
# Prerequisites: same as run_wrf_nesi.sl (SIF image, WPS_GEOG, parameters.toml)
# =============================================================================

# ---- Configuration ----------------------------------------------------------

PROJECT_CODE="nesi99999"                                        # NeSI project code
IMAGE_NAME="wrf-auto-runs"                                      # Docker/SIF image name
IMAGE_VERSION="2.2"                                             # Docker/SIF image version
SCRATCH="/nesi/nobackup/${PROJECT_CODE}/${USER}"                # Scratch/nobackup base
SIF_PATH="${SCRATCH}/${IMAGE_NAME}_${IMAGE_VERSION}.sif"        # Apptainer SIF image
WPS_GEOG_PATH="${SCRATCH}/WPS_GEOG"                             # Static geography data
PARAMS_FILE="$(pwd)/parameters.toml"                            # Path to parameters.toml
DATA_DIR="${SCRATCH}/wrf_runs/${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"  # Per-task working directory

# Job array date settings
BASE_DATE="2020-01-01 00:00:00"       # Start date for task 0
STEP_HOURS=48                         # Hours between each task's start date

# Optional overrides — uncomment to override values in parameters.toml.
# DOMAINS="3,4"
# DURATION_HOURS=48

# ---- Compute start date for this task --------------------------------------

OFFSET_HOURS=$((SLURM_ARRAY_TASK_ID * STEP_HOURS))
START_DATE=$(date -u -d "${BASE_DATE} + ${OFFSET_HOURS} hours" "+%Y-%m-%d %H:%M:%S")
echo "Array task ${SLURM_ARRAY_TASK_ID}: start_date = ${START_DATE}"

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

# ---- Create per-task data directory -----------------------------------------

mkdir -p "${DATA_DIR}"
echo "Job ${SLURM_ARRAY_JOB_ID}, task ${SLURM_ARRAY_TASK_ID}: data directory = ${DATA_DIR}"

# ---- Build bind mounts ------------------------------------------------------

BIND_ARGS="${PARAMS_FILE}:/app/parameters.toml"
BIND_ARGS="${BIND_ARGS},${WPS_GEOG_PATH}:/WPS_GEOG:ro"
BIND_ARGS="${BIND_ARGS},${DATA_DIR}:/data"

# ---- Build environment variable overrides -----------------------------------

ENV_ARGS=(--env "TZ=UTC")
ENV_ARGS+=(--env "n_cores=${SLURM_NTASKS}")
ENV_ARGS+=(--env "HYDRA_LAUNCHER=fork")
ENV_ARGS+=(--env "HYDRA_IFACE=lo")
ENV_ARGS+=(--env "start_date=${START_DATE}")

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
echo "Start date: ${START_DATE}"
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
