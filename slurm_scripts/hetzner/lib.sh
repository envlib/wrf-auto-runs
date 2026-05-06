#!/bin/bash
# Shared helpers for the run_*.sh / chunk.sl orchestrators in a project dir.
# Source from the same directory:  source ./lib.sh
#
# Provides:
#   toml_get <section|""> <key> <file>     — read a single TOML field
#   gen_uuid                                — print a fresh 13-char hex uuid
#   resolve_run_uuid <toml_file>            — env RUN_UUID > toml run_uuid > gen_uuid

toml_get() {
    # Usage: toml_get <section|""> <key> <file>
    # Empty section reads top-level fields (those before the first [section]).
    # Handles inline `# comment` after the value, and strips surrounding "..." or '...'.
    local section="$1" key="$2" file="$3"
    awk -v sec="$section" -v key="$key" '
        BEGIN { in_sec = (sec == "") ? 1 : 0 }
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*\[/ {
            line = $0
            sub(/[[:space:]]*#.*$/, "", line)
            if (line ~ /^[[:space:]]*\[.+\][[:space:]]*$/) {
                sub(/^[[:space:]]*\[/, "", line)
                sub(/\][[:space:]]*$/, "", line)
                in_sec = (line == sec) ? 1 : 0
            }
            next
        }
        in_sec && $0 ~ "^[[:space:]]*"key"[[:space:]]*=" {
            sub(/^[^=]*=[[:space:]]*/, "")
            sub(/[[:space:]]*#.*$/, "")
            gsub(/^["'\'']|["'\'']$/, "")
            print
            exit
        }
    ' "$file"
}

gen_uuid() {
    tr -dc 'a-f0-9' < /dev/urandom | head -c 13
}

resolve_run_uuid() {
    # Usage: resolve_run_uuid <toml_file>
    # Precedence: env $RUN_UUID > toml top-level run_uuid > fresh gen_uuid.
    local file="$1"
    if [ -n "${RUN_UUID:-}" ]; then
        echo "${RUN_UUID}"
        return
    fi
    local from_toml
    from_toml=$(toml_get "" run_uuid "${file}")
    echo "${from_toml:-$(gen_uuid)}"
}
