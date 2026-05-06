#!/bin/bash
# Phase 3 local runner. With restart.enable=true in parameters.toml:
#
#   stop_after_upload=false (default for local dev):
#     docker compose up        — loops internally through all chunks until sim_end.
#     This script's `up` mode is the simplest path.
#
#   stop_after_upload=true (mirrors SLURM chained pattern):
#     Re-run this script repeatedly with the same RUN_UUID; each invocation does one chunk.
#     `RUN_UUID=<uuid> ./run_local.sh` to pin a specific uuid.
#
# RUN_UUID can also be set in parameters.toml's run_uuid field; this script will use that.

set -e

cd "$(dirname "$0")"
source ./lib.sh

# Resolve RUN_UUID: env override > parameters.toml > generate fresh.
# (main.py honors all three; resolving here just makes the value visible in the log.)
export RUN_UUID="$(resolve_run_uuid ./parameters.toml)"
echo "RUN_UUID=${RUN_UUID}"

docker compose up --abort-on-container-failure
