#!/usr/bin/env bash
# Ensure the Alpine UEFI tiny guest image is available for exporterset-qemu e2e.
#
# Resolution order:
#   1. JUMPSTARTER_E2E_QEMU_IMAGE (absolute path or URL — printed and exited)
#   2. Existing file at e2e/testdata/<default name>
#   3. Copy from python/packages/jumpstarter-driver-qemu/images/ if present
#   4. Download from ALPINE_IMAGE_URL
#
# Prints the absolute image path on stdout (last line).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TESTDATA_DIR="${REPO_ROOT}/e2e/testdata"
IMAGE_NAME="${JUMPSTARTER_E2E_QEMU_IMAGE_NAME:-nocloud_alpine-3.22.4-x86_64-uefi-tiny-r0.qcow2}"
DEST="${TESTDATA_DIR}/${IMAGE_NAME}"
# Pinned Alpine nocloud UEFI tiny image (~128Mi). Override with JUMPSTARTER_E2E_QEMU_IMAGE_URL.
ALPINE_IMAGE_URL="${JUMPSTARTER_E2E_QEMU_IMAGE_URL:-https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/cloud/${IMAGE_NAME}}"
QEMU_PKG_IMAGE="${REPO_ROOT}/python/packages/jumpstarter-driver-qemu/images/${IMAGE_NAME}"

if [ -n "${JUMPSTARTER_E2E_QEMU_IMAGE:-}" ]; then
  echo "${JUMPSTARTER_E2E_QEMU_IMAGE}"
  exit 0
fi

mkdir -p "${TESTDATA_DIR}"

if [ -f "${DEST}" ]; then
  echo "${DEST}"
  exit 0
fi

if [ -f "${QEMU_PKG_IMAGE}" ]; then
  echo "Copying guest image from ${QEMU_PKG_IMAGE}" >&2
  cp -f "${QEMU_PKG_IMAGE}" "${DEST}"
  echo "${DEST}"
  exit 0
fi

echo "Downloading Alpine guest image from ${ALPINE_IMAGE_URL}" >&2
tmp="${DEST}.partial"
curl -fL --retry 3 --retry-delay 2 -o "${tmp}" "${ALPINE_IMAGE_URL}"
mv "${tmp}" "${DEST}"
echo "${DEST}"
