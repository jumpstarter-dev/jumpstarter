#!/bin/sh
# Wait for a binary to appear on the shared volume, then exec it.
#
# The exporter init container copies jmp-exec to /shared/ during startup.
# This script polls until the binary exists and is executable, then
# replaces itself with the binary process.
#
# Usage: wait-for-binary.sh <binary-path> [args...]

set -eu

BINARY="$1"
shift

echo "wait-for-binary: waiting for ${BINARY}..." >&2

while [ ! -x "$BINARY" ]; do
    sleep 0.1
done

echo "wait-for-binary: found ${BINARY}, starting" >&2
exec "$BINARY" "$@"
