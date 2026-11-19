#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
controller_dir=$(cd "$script_dir/.." && pwd)
repo_dir=$(cd "$controller_dir/.." && pwd)
containerfile="$controller_dir/Containerfile.prebuilt"

if [[ -n "${CONTAINER_TOOL:-}" ]]; then
	:
elif command -v podman >/dev/null 2>&1; then
	CONTAINER_TOOL=podman
elif command -v docker >/dev/null 2>&1; then
	CONTAINER_TOOL=docker
else
	echo "test-prebuilt-nested-bin: podman or docker is required" >&2
	exit 1
fi

tmp_root="$repo_dir/.opencode/tmp"
mkdir -p "$tmp_root"
context=$(mktemp -d "$tmp_root/prebuilt-nested-bin.XXXXXX")
# Derive the tag and container name from the mktemp suffix: two invocations can
# share a PID namespace and a container daemon, so $$ alone is not unique.
unique=$(basename "$context")
image="jumpstarter-prebuilt-nested-bin-test:$unique"
container_id=""

cleanup() {
	if [[ -n "$container_id" ]]; then
		"$CONTAINER_TOOL" rm -f "$container_id" >/dev/null 2>&1 || true
	fi
	"$CONTAINER_TOOL" rmi -f "$image" >/dev/null 2>&1 || true
	rm -rf "$context"
}
trap cleanup EXIT

# A nested BIN must be executable by the final unprivileged user. Use a real
# executable as the payload, but make it owner-only so a missing chmod fails
# the container run below.
mkdir -p "$context/bin"
cp /bin/true "$context/bin/manager"
chmod 0700 "$context/bin/manager"

"$CONTAINER_TOOL" build \
	--build-arg BIN=bin/manager \
	-t "$image" \
	-f "$containerfile" \
	"$context"

# Inspect the copied entrypoint mode rather than executing it. The host binary
# used as a fixture may not be compatible with the UBI micro runtime, but the
# image build's chmod behavior is what this check covers.
container_id=$("$CONTAINER_TOOL" create "$image")
"$CONTAINER_TOOL" cp "$container_id:/entrypoint" "$context/entrypoint"
mode=$(stat -c '%a' "$context/entrypoint")
if [[ "$mode" != "755" && "$mode" != "0755" ]]; then
	echo "prebuilt Containerfile nested BIN check failed: /entrypoint mode is $mode, want 755" >&2
	exit 1
fi

echo "prebuilt Containerfile nested BIN check passed"
