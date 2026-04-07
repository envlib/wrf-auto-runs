#!/bin/bash -e
#SBATCH --job-name=topo-3km
#SBATCH --nodes=1
#SBATCH --partition=batch               # Updated to your local partition
#SBATCH --time=24:00:00
#SBATCH --ntasks=48                    # You have 48 cores/node, so this uses a full node
#SBATCH --mem=64G                      # You have ~256GB/node, this will easily fit
#SBATCH --cpus-per-task=2             # Request 2 logical CPUs per task
#SBATCH --hint=nomultithread          # Ensure those 2 CPUs are on the same physical core
#SBATCH --ntasks-per-core=1           # Explicitly restrict to 1 task per physical core
#SBATCH --output=wrf-auto_%A_%a.log
#SBATCH --error=wrf-auto_%A_%a.err
#SBATCH --array=0-11                  # Adjust range for number of CSV rows

# =============================================================================
# WRF-Auto Pipeline — Apptainer on Hetzner (Job Array, CSV-based)
#
# Submits multiple WRF runs as a Slurm job array. Each task reads its
# start_date and end_date from a CSV file (run_periods.csv), using
# SLURM_ARRAY_TASK_ID as a 0-based row index.
#
# CSV format (slurm_scripts/run_periods.csv):
#   start_date,end_date
#   2020-01-01 00:00:00,2020-01-03 00:00:00
#   2020-01-03 00:00:00,2020-01-05 00:00:00
#   ...
#
# Usage:
#   sbatch slurm_scripts/run_wrf_hetzner_csv_array.sl
#   sbatch --array=0-5 slurm_scripts/run_wrf_hetzner_csv_array.sl   # override range
#   CSV_FILE=/path/to/dates.csv sbatch slurm_scripts/run_wrf_hetzner_csv_array.sl
#
# To auto-detect array range from CSV row count:
#   ROWS=$(( $(wc -l < slurm_scripts/run_periods.csv) - 1 ))
#   sbatch --array=0-$((ROWS - 1)) slurm_scripts/run_wrf_hetzner_csv_array.sl
#
# Prerequisites: same as run_wrf_hetzner.sl (SIF image, WPS_GEOG, parameters.toml)
# =============================================================================

# ---- Configuration ----------------------------------------------------------

IMAGE_NAME="wrf-auto-runs-intel"
IMAGE_VERSION="1.1"
SHARED_BASE="/shared/wrf_data"                                  # Your new NFS share
SIF_PATH="${SHARED_BASE}/${IMAGE_NAME}_${IMAGE_VERSION}.sif"    # Apptainer SIF image
WPS_GEOG_PATH="${SHARED_BASE}/WPS_GEOG"                         # Static geography data
PARAMS_FILE="$(pwd)/parameters.toml"                            # Path to parameters.toml
CSV_FILE="$(pwd)/periods.csv"

# The local SSD/NVMe drive on whichever node this lands on
LOCAL_SCRATCH="/var/tmp/wrf_scratch/${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"

# Optional overrides — uncomment to override values in parameters.toml.
# DOMAINS="3,4"
# DURATION_HOURS=48

# ---- Read start_date and end_date from CSV ----------------------------------

if [ ! -f "${CSV_FILE}" ]; then
    echo "ERROR: CSV file not found at ${CSV_FILE}"
    exit 1
fi

TOTAL_ROWS=$(( $(grep -c '' "${CSV_FILE}") - 1 ))

if [ "${SLURM_ARRAY_TASK_ID}" -ge "${TOTAL_ROWS}" ]; then
    echo "ERROR: SLURM_ARRAY_TASK_ID=${SLURM_ARRAY_TASK_ID} exceeds CSV rows (${TOTAL_ROWS} data rows, indices 0-$((TOTAL_ROWS - 1)))"
    exit 1
fi

CSV_LINE=$(sed -n "$((SLURM_ARRAY_TASK_ID + 2))p" "${CSV_FILE}" | tr -d '\r')
START_DATE=$(echo "${CSV_LINE}" | cut -d',' -f1 | tr -d '"')
END_DATE=$(echo "${CSV_LINE}" | cut -d',' -f2 | tr -d '"')

if [ -z "${START_DATE}" ] || [ -z "${END_DATE}" ]; then
    echo "ERROR: Failed to parse dates from CSV line $((SLURM_ARRAY_TASK_ID + 2)): '${CSV_LINE}'"
    exit 1
fi

echo "Array task ${SLURM_ARRAY_TASK_ID}: start_date=${START_DATE}, end_date=${END_DATE}"

# ---- Apptainer cache setup --------------------------------------------------

export APPTAINER_CACHEDIR="${SHARED_BASE}/.apptainer/cache"
export APPTAINER_TMPDIR="${SHARED_BASE}/.apptainer/tmp"
mkdir -p "${APPTAINER_CACHEDIR}" "${APPTAINER_TMPDIR}"

# ---- Validation checks ------------------------------------------------------

if [ ! -f "${SIF_PATH}" ]; then
    echo "ERROR: SIF image not found at ${SIF_PATH}"
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

mkdir -p "${LOCAL_SCRATCH}"
echo "Job ${SLURM_ARRAY_JOB_ID}, task ${SLURM_ARRAY_TASK_ID}: Running locally on $(hostname) in ${LOCAL_SCRATCH}"

# ---- Build bind mounts ------------------------------------------------------

BIND_ARGS="${PARAMS_FILE}:/app/parameters.toml"
BIND_ARGS="${BIND_ARGS},${WPS_GEOG_PATH}:/WPS_GEOG:ro"
BIND_ARGS="${BIND_ARGS},${LOCAL_SCRATCH}:/data"

# ---- Build environment variable overrides -----------------------------------

ENV_ARGS=(--env "TZ=UTC")
ENV_ARGS+=(--env "n_cores=${SLURM_NTASKS}")
ENV_ARGS+=(--env "HYDRA_LAUNCHER=fork")
ENV_ARGS+=(--env "HYDRA_IFACE=lo")
ENV_ARGS+=(--env "start_date=${START_DATE}")
ENV_ARGS+=(--env "end_date=${END_DATE}")

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
echo "End date: ${END_DATE}"
echo "Data dir: ${LOCAL_SCRATCH}"

apptainer exec \
    --cleanenv \
    --contain \
    --writable-tmpfs \
    --bind "${BIND_ARGS}" \
    "${ENV_ARGS[@]}" \
    "${SIF_PATH}" \
    bash -c "cd /app && uv run python -u main.py"

echo "Pipeline finished at $(date)"
echo "Data is safely stored in ${LOCAL_SCRATCH} on $(hostname)."

# ---- Optional cleanup (uncomment to remove intermediate files) --------------

# echo "Cleaning up intermediate files..."
# rm -rf "${LOCAL_SCRATCH}"
