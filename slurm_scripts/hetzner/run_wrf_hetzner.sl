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
#SBATCH --output=wrf-auto_%j.log
#SBATCH --error=wrf-auto_%j.err

# =============================================================================
# WRF-ERA5 Pipeline — Apptainer on Local Cluster
# =============================================================================

# ---- Configuration ----------------------------------------------------------

IMAGE_NAME="wrf-auto-runs-intel"
IMAGE_VERSION="1.1"
SHARED_BASE="/shared/wrf_data"                                  # Your new NFS share
SIF_PATH="${SHARED_BASE}/${IMAGE_NAME}_${IMAGE_VERSION}.sif"    # Apptainer SIF image
WPS_GEOG_PATH="${SHARED_BASE}/WPS_GEOG"                         # Static geography data
PARAMS_FILE="$(pwd)/parameters.toml"                            # Path to parameters.toml
# DATA_DIR="${SHARED_BASE}/wrf_runs/${SLURM_JOB_ID}"              # Per-job working directory

# The local SSD/NVMe drive on whichever node this lands on
LOCAL_SCRATCH="/var/tmp/wrf_scratch/${SLURM_JOB_ID}"

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

# ---- Create per-job data directory ------------------------------------------

mkdir -p "${LOCAL_SCRATCH}"
# Ensure log directory exists as well
# mkdir -p "${SHARED_BASE}/logs"
echo "Job ${SLURM_JOB_ID}: Running locally on $(hostname) in ${LOCAL_SCRATCH}"

# ---- Build bind mounts ------------------------------------------------------

BIND_ARGS="${PARAMS_FILE}:/app/parameters.toml"
BIND_ARGS="${BIND_ARGS},${WPS_GEOG_PATH}:/WPS_GEOG:ro"
BIND_ARGS="${BIND_ARGS},${LOCAL_SCRATCH}:/data"

# ---- Build environment variable overrides -----------------------------------

ENV_ARGS=(--env "TZ=UTC")
ENV_ARGS=(--env "n_cores=${SLURM_NTASKS}")
ENV_ARGS+=(--env "HYDRA_LAUNCHER=fork")
ENV_ARGS+=(--env "HYDRA_IFACE=lo")

# ---- Run the pipeline -------------------------------------------------------

echo "Starting WRF-ERA5 pipeline at $(date)"
echo "SIF: ${SIF_PATH}"
echo "Cores: ${SLURM_NTASKS}"
# echo "Data dir: ${LOCAL_SCRATCH}"

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
