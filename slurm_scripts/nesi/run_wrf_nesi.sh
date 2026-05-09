#!/bin/bash
# =============================================================================
# WRF-ERA5 Pipeline Orchestrator (Phase 3) — NeSI / Apptainer
# =============================================================================
#
# This script is NOT a SLURM job script. Run it directly from a NeSI login node:
#
#     ./run_wrf_nesi.sh
#         or
#     bash run_wrf_nesi.sh
#
# It generates a single run_uuid (or reads one from parameters.toml) and submits
# a chain of chunk.sl jobs, each depending on the prior chunk via afterany.
# Each chunk:
#   1. detects which chunk it is from S3 wrfrst state
#   2. runs preprocess + WRF for its [chunk_start, chunk_end] window
#   3. uploads wrfrst (next chunk's seed) and wrfout
#   4. exits
#
# The number of chunks is computed as ceil((end - start) / interval_days).
#
# NeSI specifics live in chunk.sl: account flag, partition, module load,
# and Lustre/nobackup paths. This orchestrator is cluster-agnostic.
# =============================================================================

set -e

cd "$(dirname "$0")"
RUN_PROJECT_DIR="$(pwd)"
source ./lib.sh

if [ ! -f "${RUN_PROJECT_DIR}/chunk.sl" ]; then
    echo "ERROR: chunk.sl missing from ${RUN_PROJECT_DIR}"
    exit 1
fi

if [ ! -f "${RUN_PROJECT_DIR}/parameters.toml" ]; then
    echo "ERROR: parameters.toml missing from ${RUN_PROJECT_DIR}"
    exit 1
fi

# ---- Pull run_uuid + sim window + interval_days from parameters.toml --------

PARAMS="${RUN_PROJECT_DIR}/parameters.toml"
RUN_UUID=$(resolve_run_uuid "${PARAMS}")
SIM_START=$(toml_get time_control start_date "${PARAMS}")
SIM_END=$(toml_get time_control end_date "${PARAMS}")
INTERVAL_DAYS=$(toml_get restart interval_days "${PARAMS}")
STOP_AFTER_UPLOAD=$(toml_get restart stop_after_upload "${PARAMS}")
[ "${STOP_AFTER_UPLOAD}" = "true" ] || STOP_AFTER_UPLOAD=false

# begin_hours shifts the real WRF start back from the user's start_date; the chunk count
# must cover that spin-up too. Default 0 if missing/empty so non-spin-up runs are unchanged.
BEGIN_HOURS=$(toml_get "time_control.history_file" begin_hours "${PARAMS}")
[ -z "${BEGIN_HOURS}" ] && BEGIN_HOURS=0

if [ -z "${SIM_START}" ] || [ -z "${SIM_END}" ]; then
    echo "ERROR: [time_control].start_date and end_date must be set in parameters.toml"
    exit 1
fi
if [ -z "${INTERVAL_DAYS}" ] || [ "${INTERVAL_DAYS}" -le 0 ]; then
    echo "ERROR: [restart].interval_days must be > 0 in parameters.toml"
    exit 1
fi

# ---- Determine number of jobs based on stop_after_upload --------------------
# stop_after_upload=true:  one container per chunk → submit ceil((end - real_start)/interval) chained jobs,
#                          where real_start = sim_start - begin_hours.
# stop_after_upload=false: one container loops internally through all chunks → submit just ONE job.

START_EPOCH=$(date -u -d "${SIM_START}" +%s)
REAL_START_EPOCH=$((START_EPOCH - BEGIN_HOURS * 3600))
REAL_START=$(date -u -d "@${REAL_START_EPOCH}" '+%Y-%m-%d %H:%M:%S')

if [ "${STOP_AFTER_UPLOAD}" = "true" ]; then
    END_EPOCH=$(date -u -d "${SIM_END}" +%s)
    DIFF_SEC=$((END_EPOCH - REAL_START_EPOCH))
    INTERVAL_SEC=$((INTERVAL_DAYS * 86400))
    # ceil(diff / interval)
    NUM_CHUNKS=$(( (DIFF_SEC + INTERVAL_SEC - 1) / INTERVAL_SEC ))
else
    NUM_CHUNKS=1
fi

echo "Submitting WRF-ERA5 chunked pipeline (NeSI)"
echo "  project dir:        ${RUN_PROJECT_DIR}"
echo "  run_uuid:           ${RUN_UUID}"
echo "  user start_date:    ${SIM_START}"
echo "  sim end_date:       ${SIM_END}"
echo "  begin_hours:        ${BEGIN_HOURS} (real WRF start: ${REAL_START})"
echo "  interval_days:      ${INTERVAL_DAYS}"
echo "  stop_after_upload:  ${STOP_AFTER_UPLOAD}"
if [ "${STOP_AFTER_UPLOAD}" = "true" ]; then
    echo "  num_jobs:           ${NUM_CHUNKS} (one per chunk, covering spin-up + sim window)"
else
    echo "  num_jobs:           1 (container loops internally through all chunks)"
fi
echo ""

# ---- Submit job(s) ---------------------------------------------------------

PREV_JOB=""
for i in $(seq 1 ${NUM_CHUNKS}); do
    DEP_FLAG=""
    [ -n "${PREV_JOB}" ] && DEP_FLAG="--dependency=afterany:${PREV_JOB}"

    JOB=$(sbatch --parsable ${DEP_FLAG} \
        --export=ALL,RUN_UUID="${RUN_UUID}",RUN_PROJECT_DIR="${RUN_PROJECT_DIR}" \
        "${RUN_PROJECT_DIR}/chunk.sl")

    if [ "${STOP_AFTER_UPLOAD}" = "true" ]; then
        echo "  chunk #${i}: jobid=${JOB}${DEP_FLAG:+ (depends on ${PREV_JOB})}"
    else
        echo "  job: ${JOB} (single container, loops internally)"
    fi
    PREV_JOB="${JOB}"
done

echo ""
echo "Track with: squeue -u \$USER --start"
if [ "${STOP_AFTER_UPLOAD}" = "true" ]; then
    echo "After completion, manually purge inputs/${RUN_UUID}/ on S3 (auto-cleanup is disabled in stop_after_upload mode)."
fi
