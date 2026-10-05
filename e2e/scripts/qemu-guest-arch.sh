#!/usr/bin/env bash
# Resolve native QEMU guest architecture for exporterset-qemu e2e.
#
# Prefer JUMPSTARTER_E2E_QEMU_ARCH when set; otherwise map uname -m / GO-style
# names to the guest arch expected by jumpstarter-driver-qemu
# (qemu-system-${GUEST_ARCH}).
#
# When sourced: exports GUEST_ARCH, MACHINE_TYPE, BOARD, VTC_NAME,
#               EXPORTERSET_NAME, QEMU_BINARY, ALPINE_IMAGE_NAME, MANIFEST
# When executed: prints those KEY=VALUE lines on stdout

_qemu_guest_arch_resolve() {
  local raw="${JUMPSTARTER_E2E_QEMU_ARCH:-$(uname -m)}"
  case "${raw}" in
    x86_64|amd64)
      GUEST_ARCH="x86_64"
      MACHINE_TYPE="q35"
      BOARD="x86-64-virtual-e2e"
      VTC_NAME="qemu-x86-64-e2e"
      EXPORTERSET_NAME="x86-64-virtual-e2e"
      MANIFEST="e2e/manifests/exporterset-qemu-kind-x86_64.yaml"
      ;;
    aarch64|arm64)
      GUEST_ARCH="aarch64"
      MACHINE_TYPE="virt"
      BOARD="aarch64-virtual-e2e"
      VTC_NAME="qemu-aarch64-e2e"
      EXPORTERSET_NAME="aarch64-virtual-e2e"
      MANIFEST="e2e/manifests/exporterset-qemu-kind-aarch64.yaml"
      ;;
    *)
      echo "Unsupported QEMU guest architecture: ${raw}" >&2
      echo "Set JUMPSTARTER_E2E_QEMU_ARCH to x86_64 or aarch64" >&2
      return 1
      ;;
  esac
  QEMU_BINARY="qemu-system-${GUEST_ARCH}"
  ALPINE_IMAGE_NAME="nocloud_alpine-3.22.4-${GUEST_ARCH}-uefi-tiny-r0.qcow2"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail
  _qemu_guest_arch_resolve
  printf 'GUEST_ARCH=%s\n' "${GUEST_ARCH}"
  printf 'MACHINE_TYPE=%s\n' "${MACHINE_TYPE}"
  printf 'BOARD=%s\n' "${BOARD}"
  printf 'VTC_NAME=%s\n' "${VTC_NAME}"
  printf 'EXPORTERSET_NAME=%s\n' "${EXPORTERSET_NAME}"
  printf 'QEMU_BINARY=%s\n' "${QEMU_BINARY}"
  printf 'ALPINE_IMAGE_NAME=%s\n' "${ALPINE_IMAGE_NAME}"
  printf 'MANIFEST=%s\n' "${MANIFEST}"
else
  _qemu_guest_arch_resolve || return 1
  export GUEST_ARCH MACHINE_TYPE BOARD VTC_NAME EXPORTERSET_NAME QEMU_BINARY ALPINE_IMAGE_NAME MANIFEST
fi
