#!/bin/bash -e
#SBATCH --nodes=1
#SBATCH --output=log_chunk_%j.log
#SBATCH --error=log_chunk_%j.err

# =============================================================================
# Generic WRF restart-chain job: ONE job = ONE chunk of [restart].interval_days, run by the wrf-auto-runs
# pipeline inside an Apptainer SIF. Submitted only by ./submit, which:
#   - puts every resource option (partition, ntasks, mem, time, ...) on the sbatch command line from the site
#     file, so this header holds nothing cluster-specific;
#   - writes everything this job needs into a per-submission snapshot dir and passes that dir as $1
#     (sbatch runs a spool copy of this script, so $0 cannot locate anything).
# This job reads only $1/job.env and $1/parameters.toml, so later edits to the project or to this checkout
# never reach a queued chain.
# =============================================================================

RUN_DIR="${1:-}"
if [ -z "${RUN_DIR}" ] || [ ! -f "${RUN_DIR}/job.env" ]; then
    echo "ERROR: no snapshot dir with job.env given as \$1 -- submit with launcher/submit, not sbatch directly"
    exit 1
fi
# SLURM_JOB_ID names the scratch dir that is later `rm -rf`ed; empty, it would be the whole scratch base.
: "${SLURM_JOB_ID:?not running under Slurm}" "${SLURM_NTASKS:?no --ntasks in the allocation}"
EXTRA_ENV=()                     # job.env sets it only when [env] is non-empty; never inherit one from the login shell
# shellcheck source=/dev/null
source "${RUN_DIR}/job.env"      # CLUSTER RUN_UUID IMAGE_* SIF_PATH WPS_GEOG_PATH SHARED_BASE SCRATCH_BASE
                                 # MODULE_LOAD KEEP_SCRATCH [EXTRA_ENV]
PARAMS_FILE="${RUN_DIR}/parameters.toml"
LOCAL_SCRATCH="${SCRATCH_BASE}/${SLURM_JOB_ID}"

# ---- Modules ----------------------------------------------------------------
if ! command -v apptainer >/dev/null 2>&1; then
    if [ -z "${MODULE_LOAD}" ]; then
        echo "ERROR: apptainer is not on PATH and the site sets no module_load for ${CLUSTER}"
        exit 1
    fi
    module purge 2>/dev/null || true      # if `module` itself is missing, let the load below say so
    module load "${MODULE_LOAD}"
fi

# ---- Apptainer cache + scratch ----------------------------------------------
export APPTAINER_CACHEDIR="${SHARED_BASE}/.apptainer/cache"
export APPTAINER_TMPDIR="${SHARED_BASE}/.apptainer/tmp"
mkdir -p "${APPTAINER_CACHEDIR}" "${APPTAINER_TMPDIR}"
mkdir -p "${LOCAL_SCRATCH}/apptainer_tmp"

# ---- Validation -------------------------------------------------------------
if [ ! -f "${SIF_PATH}" ]; then echo "ERROR: SIF image not found at ${SIF_PATH}"; exit 1; fi
if [ ! -f "${PARAMS_FILE}" ]; then echo "ERROR: parameters.toml not found at ${PARAMS_FILE}"; exit 1; fi
if [ ! -d "${WPS_GEOG_PATH}" ]; then echo "ERROR: WPS_GEOG directory not found at ${WPS_GEOG_PATH}"; exit 1; fi
echo "Job ${SLURM_JOB_ID}: chunk on $(hostname) (${CLUSTER}), scratch=${LOCAL_SCRATCH}, snapshot=${RUN_DIR}"

# ---- Project hooks (optional; snapshotted with everything else) -------------
# hooks/pre runs before the pipeline and a failure stops the job; hooks/post runs after it and a failure only warns.
# They run on the host with these variables exported (apptainer is on PATH), and post also gets CHUNK_RC.
export RUN_DIR RUN_UUID CLUSTER SIF_PATH IMAGE_NAME IMAGE_VERSION PARAMS_FILE LOCAL_SCRATCH SLURM_JOB_ID
if [ -f "${RUN_DIR}/hooks/pre" ]; then
    if ! bash "${RUN_DIR}/hooks/pre"; then
        echo "ERROR: hooks/pre failed (above) -- not running the chunk"
        exit 1
    fi
fi

# ---- Bind mounts ------------------------------------------------------------
# /dev/shm and apptainer_tmp:/tmp are both REQUIRED. Without the /tmp bind, --contain --writable-tmpfs gives a
# ~64 MB tmpfs and rclone silently truncates ERA5 downloads.
BIND_ARGS="${PARAMS_FILE}:/app/parameters.toml"
BIND_ARGS="${BIND_ARGS},${WPS_GEOG_PATH}:/WPS_GEOG:ro"
BIND_ARGS="${BIND_ARGS},${LOCAL_SCRATCH}:/data"
BIND_ARGS="${BIND_ARGS},/dev/shm:/dev/shm"
BIND_ARGS="${BIND_ARGS},${LOCAL_SCRATCH}/apptainer_tmp:/tmp"

# ---- Env vars ---------------------------------------------------------------
# n_cores_metgrid is deliberately NOT set here -- parameters.toml stays authoritative (a project can pass it
# through [env]). Exporting SLURM_NTASKS as n_cores_metgrid caused intermittent high-rank metgrid SIGSEGVs.
ENV_ARGS=(--env "TZ=UTC")
ENV_ARGS+=(--env "n_cores=${SLURM_NTASKS}")
ENV_ARGS+=(--env "n_cores_preprocess=${SLURM_NTASKS}")
ENV_ARGS+=(--env "HYDRA_LAUNCHER=fork")
ENV_ARGS+=(--env "HYDRA_IFACE=lo")
ENV_ARGS+=(--env "run_uuid=${RUN_UUID}")

# rclone stall mitigation. The pipeline's upload runs rclone with its defaults (--timeout 5m idle,
# --low-level-retries 10, --retries 3), so one transient hiccup can walk the whole retry ladder (~1.5 h with all
# ranks idle). --cleanenv strips the host environment, so these are injected here. --timeout is an IDLE
# timeout: it never fires on a slow but progressing transfer.
ENV_ARGS+=(--env "RCLONE_TIMEOUT=60s")
ENV_ARGS+=(--env "RCLONE_CONTIMEOUT=30s")
ENV_ARGS+=(--env "RCLONE_LOW_LEVEL_RETRIES=3")
ENV_ARGS+=(--env "RCLONE_RETRIES=5")

for kv in "${EXTRA_ENV[@]}"; do ENV_ARGS+=(--env "${kv}"); done   # the project's [env] (submit refuses reserved names)

# ---- Run --------------------------------------------------------------------
echo "Starting chunk at $(date)"
echo "SIF: ${SIF_PATH}"
echo "Ranks: ${SLURM_NTASKS}"
echo "RUN_UUID: ${RUN_UUID}"

# Tee the pipeline's output so the scratch decision below can inspect it. PIPESTATUS, because the pipeline's own
# status is tee's (always 0).
PIPE_LOG="${LOCAL_SCRATCH}/pipeline.log"
apptainer exec \
    --cleanenv \
    --contain \
    --writable-tmpfs \
    --bind "${BIND_ARGS}" \
    "${ENV_ARGS[@]}" \
    "${SIF_PATH}" \
    bash -c "cd /app && uv run python -u main.py" 2>&1 | tee "${PIPE_LOG}"
RC=${PIPESTATUS[0]}

echo "Chunk finished at $(date) (rc=${RC})"

if [ -f "${RUN_DIR}/hooks/post" ]; then
    if ! CHUNK_RC="${RC}" bash "${RUN_DIR}/hooks/post"; then
        echo "*** WARNING: hooks/post failed (above); the job still exits with the pipeline's status"
    fi
fi

# An S3 upload failure is NOT fatal to the pipeline: utils.ul_output_files() prints "-- Upload FAILED ..." and
# returns, and if wrf.exe succeeded the job still exits 0. That function deletes the local wrfout only when rclone
# exits 0, so on a failed upload the ONLY copy is in LOCAL_SCRATCH -- cleaning scratch on rc=0 alone would destroy it.
UPLOAD_FAILED=0
if grep -q -- '-- Upload FAILED' "${PIPE_LOG}" 2>/dev/null; then
    UPLOAD_FAILED=1
    echo ""
    echo "*** WARNING: at least one upload FAILED — output is retained locally ***"
    grep -- '-- Upload FAILED' "${PIPE_LOG}" | sed 's/^/    /'
    echo "    Local wrfout still in ${LOCAL_SCRATCH}/run/ :"
    ls -lh "${LOCAL_SCRATCH}"/run/wrfout_d0* 2>/dev/null | sed 's/^/    /' || true
    echo "    Nothing in the chain re-uploads it: copy these files to the output prefix by hand"
    echo "    (rclone copy with the [remote.output] remote) BEFORE this scratch is reused."
fi

# ---- Scratch lifecycle ------------------------------------------------------
# Clean on success; RETAIN on failure (a metgrid failure uploads nothing, and metgrid.log here is the only
# evidence). submit --keep-scratch retains it regardless.
if [ "${RC}" -eq 0 ] && [ "${UPLOAD_FAILED}" -eq 0 ] && [ -z "${KEEP_SCRATCH}" ]; then
    rm -rf "${LOCAL_SCRATCH}"
    echo "Scratch cleaned: ${LOCAL_SCRATCH}"
else
    echo "Scratch RETAINED for diagnosis: ${LOCAL_SCRATCH}"
    if [ "${UPLOAD_FAILED}" -ne 0 ]; then
        echo "  upload failed    -> ${LOCAL_SCRATCH}/run/wrfout_d0*  IS THE ONLY COPY"
    fi
    if [ "${RC}" -ne 0 ]; then
        echo "  metgrid failure  -> ${LOCAL_SCRATCH}/metgrid.log   (NOT uploaded to S3)"
        echo "  real/wrf failure -> ${LOCAL_SCRATCH}/run/rsl.error.*  (also under logs/<run_uuid>/)"
    fi
fi

# A failed upload leaves the chain intact (the restart is a separate upload, and the next job is afterany), but the
# job must not read as a success: exit 75 so sacct and mail-on-fail show it.
if [ "${RC}" -eq 0 ] && [ "${UPLOAD_FAILED}" -ne 0 ]; then
    exit 75
fi
exit "${RC}"
