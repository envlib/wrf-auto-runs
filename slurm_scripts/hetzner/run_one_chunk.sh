#!/bin/bash
# Test helper: submit a SINGLE chunk.sl job, regardless of stop_after_upload.
#
# Usage:
#     ./run_one_chunk.sl                         # uses run_uuid from parameters.toml or generates one
#     RUN_UUID=restart_test1 ./run_one_chunk.sl  # pin a specific uuid
#
# Useful for:
#   - Iteratively testing chunk behavior without the full chain.
#   - Running a single chunk against an existing wrfrst on S3 (chunk auto-detects from S3).
#   - Debugging Phase 3 behavior in isolation.

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

RUN_UUID=$(resolve_run_uuid "${RUN_PROJECT_DIR}/parameters.toml")

echo "Submitting one chunk.sl job"
echo "  project dir: ${RUN_PROJECT_DIR}"
echo "  run_uuid:    ${RUN_UUID}"

JOB=$(sbatch --parsable \
    --export=ALL,RUN_UUID="${RUN_UUID}",RUN_PROJECT_DIR="${RUN_PROJECT_DIR}" \
    "${RUN_PROJECT_DIR}/chunk.sl")

echo "  jobid:       ${JOB}"
echo ""
echo "Track with: squeue -j ${JOB}"
