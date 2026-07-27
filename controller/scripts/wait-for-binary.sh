#!/bin/sh
# Wait for a binary to appear on the shared volume, then exec it.
#
# The exporter init container copies jumpstarter-exec to /shared/ during startup.
# This script polls until the binary exists and is executable, then
# replaces itself with the binary process.
#
# Usage: wait-for-binary.sh <binary-path> [args...]

set -eu

BINARY="$1"
shift

TIMEOUT="${WAIT_FOR_BINARY_TIMEOUT:-60}"
elapsed=0

echo "wait-for-binary: waiting for ${BINARY} (timeout ${TIMEOUT}s)..." >&2

while [ ! -x "$BINARY" ]; do
    if [ "$elapsed" -ge "$TIMEOUT" ]; then
        echo "wait-for-binary: timed out after ${TIMEOUT}s waiting for ${BINARY}" >&2
        exit 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

echo "wait-for-binary: found ${BINARY}, starting" >&2
exec "$BINARY" "$@"
