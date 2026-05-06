#!/bin/bash -e
#SBATCH --job-name=wrf-chunk
#SBATCH --account=nesi99999             # Replace with your NeSI project code
#SBATCH --nodes=1
#SBATCH --partition=genoa               # Or: large, bigmem, hgx — check nesi.org.nz
#SBATCH --time=72:00:00                 # >24h on most NeSI partitions needs --qos=long
#SBATCH --ntasks=48                     # MPI ranks for wrf.exe (maps to n_cores)
#SBATCH --mem=96G
#SBATCH --cpus-per-task=2
#SBATCH --hint=nomultithread
#SBATCH --ntasks-per-core=1
#SBATCH --output=log_chunk_%j.log
#SBATCH --error=log_chunk_%j.err

# =============================================================================
# Phase 3 unified per-chunk job (NeSI). One container does:
#   1. detect remote restart state (auto-determines which chunk this is)
#   2. preprocess for [chunk_start, chunk_end] window
#   3. (if restart) download wrfrst from S3, apply restart namelist
#   4. run wrf.exe; upload wrfout + wrfrst as it goes
#   5. exit (caller submits next chunk if more chunks remain)
#
# When [restart].stop_after_upload=true (typical SLURM use), the orchestrator
# (run_wrf_nesi.sh) submits N chained copies. Each one auto-detects which
# chunk it is via S3 wrfrst state.
#
# When [restart].stop_after_upload=false (typical local docker-compose use),
# the pipeline loops internally until sim_end. This script would still work
# but you'd typically only submit one copy.
# =============================================================================

# ---- Configuration ----------------------------------------------------------

PROJECT_CODE="nesi99999"                                        # NeSI project code (must match --account above)
IMAGE_NAME="wrf-auto-runs-intel-wvt"
IMAGE_VERSION="1.8"
SCRATCH="/nesi/nobackup/${PROJECT_CODE}/${USER}"                # NeSI Lustre scratch base
SIF_PATH="${SCRATCH}/${IMAGE_NAME}_${IMAGE_VERSION}.sif"
WPS_GEOG_PATH="${SCRATCH}/WPS_GEOG"
PARAMS_FILE="${RUN_PROJECT_DIR}/parameters.toml"

# Per-job working directory on Lustre nobackup. Larger than node-local TMPDIR
# (which is ~200 GB on most genoa nodes) and persists across crash for debug.
# For peak I/O performance on small chunks you can switch to ${TMPDIR}/wrf_chunk
# but make sure your chunk fits — wrfout for a 7-day 3 km run can exceed 200 GB.
LOCAL_SCRATCH="${SCRATCH}/wrf_runs/${SLURM_JOB_ID}"

# ---- Load modules -----------------------------------------------------------

module purge 2>/dev/null
module load Apptainer

# ---- Apptainer cache + tmp setup --------------------------------------------

export APPTAINER_CACHEDIR="${SCRATCH}/.apptainer/cache"
export APPTAINER_TMPDIR="${SCRATCH}/.apptainer/tmp"
mkdir -p "${APPTAINER_CACHEDIR}" "${APPTAINER_TMPDIR}"
mkdir -p "${LOCAL_SCRATCH}/apptainer_tmp"

# ---- Validation -------------------------------------------------------------

if [ ! -f "${SIF_PATH}" ]; then echo "ERROR: SIF image not found at ${SIF_PATH}"; exit 1; fi
if [ ! -f "${PARAMS_FILE}" ]; then echo "ERROR: parameters.toml not found at ${PARAMS_FILE}"; exit 1; fi
if [ ! -d "${WPS_GEOG_PATH}" ]; then echo "ERROR: WPS_GEOG directory not found at ${WPS_GEOG_PATH}"; exit 1; fi

mkdir -p "${LOCAL_SCRATCH}"
echo "Job ${SLURM_JOB_ID}: chunk on $(hostname), scratch=${LOCAL_SCRATCH}"

# ---- Bind mounts ------------------------------------------------------------

BIND_ARGS="${PARAMS_FILE}:/app/parameters.toml"
BIND_ARGS="${BIND_ARGS},${WPS_GEOG_PATH}:/WPS_GEOG:ro"
BIND_ARGS="${BIND_ARGS},${LOCAL_SCRATCH}:/data"
BIND_ARGS="${BIND_ARGS},/dev/shm:/dev/shm"
BIND_ARGS="${BIND_ARGS},${LOCAL_SCRATCH}/apptainer_tmp:/tmp"

# ---- Env vars ---------------------------------------------------------------
# In unified mode the same allocation runs both preprocess (metgrid/real/ndown) and WRF, so
# both rank counts default to SLURM_NTASKS — no reason to leave cores idle during preprocess.
# Override n_cores_preprocess in parameters.toml if you specifically want metgrid at fewer ranks.
# RUN_UUID is forwarded from the orchestrator so all chunks in the chain share one S3 prefix.

ENV_ARGS=(--env "TZ=UTC")
ENV_ARGS+=(--env "n_cores=${SLURM_NTASKS}")
ENV_ARGS+=(--env "n_cores_preprocess=${SLURM_NTASKS}")
ENV_ARGS+=(--env "HYDRA_LAUNCHER=fork")
ENV_ARGS+=(--env "HYDRA_IFACE=lo")
[ -n "${RUN_UUID:-}" ] && ENV_ARGS+=(--env "run_uuid=${RUN_UUID}")

# ---- Run --------------------------------------------------------------------

echo "Starting chunk at $(date)"
echo "SIF: ${SIF_PATH}"
echo "Cores: ${SLURM_NTASKS}"
echo "RUN_UUID: ${RUN_UUID:-<from parameters.toml or auto-generated>}"

apptainer exec \
    --cleanenv \
    --contain \
    --writable-tmpfs \
    --bind "${BIND_ARGS}" \
    "${ENV_ARGS[@]}" \
    "${SIF_PATH}" \
    bash -c "cd /app && uv run python -u main.py"

echo "Chunk finished at $(date)"
