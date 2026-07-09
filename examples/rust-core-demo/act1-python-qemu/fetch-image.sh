#!/usr/bin/env bash
# Fetch the QEMU boot assets for Act 1 into ./assets (gitignored):
#   base.qcow2              a small aarch64 cloud image (cloud-init aware, serial console)
#   disk.qcow2              a qcow2 overlay on base.qcow2 so boots are repeatable (reset = recreate)
#   edk2-aarch64-code.fd    UEFI firmware code (copied from your qemu install; aarch64 virt needs UEFI)
#   edk2-aarch64-vars.fd    writable UEFI vars (copied from the qemu vars template)
#
# Usage:
#   bash fetch-image.sh           # download + prepare (idempotent)
#   bash fetch-image.sh --reset   # recreate the disk.qcow2 overlay (fresh VM state, fast)
#
# Override the image with DEMO_IMAGE_URL. The default is Debian 12 genericcloud arm64 (~350 MB):
# small, cloud-init aware, and configures a serial getty on the virt serial port so the driver's
# `-serial pty` console sees a login prompt. QEMU runs under TCG (software) on macOS — the driver
# adds no hvf/kvm accel — so keep the image small for a snappy boot.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ASSETS="$PWD/assets"
mkdir -p "$ASSETS"

DEMO_IMAGE_URL="${DEMO_IMAGE_URL:-https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-arm64.qcow2}"

make_overlay() {
  rm -f "$ASSETS/disk.qcow2"
  qemu-img create -q -f qcow2 -F qcow2 -b "$ASSETS/base.qcow2" "$ASSETS/disk.qcow2"
  echo "[assets] fresh overlay disk.qcow2 -> base.qcow2"
}

if [ "${1:-}" = "--reset" ]; then
  [ -f "$ASSETS/base.qcow2" ] || { echo "no base.qcow2 yet; run without --reset first" >&2; exit 1; }
  make_overlay
  exit 0
fi

# 1) base cloud image
if [ ! -f "$ASSETS/base.qcow2" ]; then
  echo "[assets] downloading cloud image: $DEMO_IMAGE_URL"
  curl -fL --retry 3 -o "$ASSETS/base.qcow2" "$DEMO_IMAGE_URL"
else
  echo "[assets] base.qcow2 already present (skip; delete it to re-download)"
fi

# 2) repeatable overlay
[ -f "$ASSETS/disk.qcow2" ] || make_overlay

# 3) UEFI firmware — locate in the qemu share dir (brew or system).
QEMU_SHARE=""
for d in "$(brew --prefix qemu 2>/dev/null)/share/qemu" /opt/homebrew/opt/qemu/share/qemu \
         /opt/homebrew/share/qemu /usr/local/share/qemu /usr/share/qemu; do
  [ -f "$d/edk2-aarch64-code.fd" ] && { QEMU_SHARE="$d"; break; }
done
[ -n "$QEMU_SHARE" ] || { echo "could not find edk2-aarch64-code.fd in any qemu share dir" >&2; exit 1; }

cp -f "$QEMU_SHARE/edk2-aarch64-code.fd" "$ASSETS/edk2-aarch64-code.fd"
# The vars template is shipped as edk2-arm-vars.fd (64 MiB, works for aarch64); copy to a
# writable per-DUT vars file. The driver mounts it snapshot=on so this copy is not mutated.
VARS_TEMPLATE=""
for v in edk2-aarch64-vars.fd edk2-arm-vars.fd; do
  [ -f "$QEMU_SHARE/$v" ] && { VARS_TEMPLATE="$QEMU_SHARE/$v"; break; }
done
[ -n "$VARS_TEMPLATE" ] || { echo "could not find an edk2 vars template in $QEMU_SHARE" >&2; exit 1; }
cp -f "$VARS_TEMPLATE" "$ASSETS/edk2-aarch64-vars.fd"
chmod u+w "$ASSETS/edk2-aarch64-vars.fd"

echo "[assets] ready:"
ls -lh "$ASSETS"
echo
echo "Smoke-test the boot directly (Ctrl-A X to quit qemu):"
echo "  qemu-system-aarch64 -nographic -machine virt -cpu cortex-a57 -smp 2 -m 1G \\"
echo "    -drive file=$ASSETS/edk2-aarch64-code.fd,if=pflash,format=raw,readonly=on \\"
echo "    -drive file=$ASSETS/edk2-aarch64-vars.fd,if=pflash,format=raw \\"
echo "    -device virtio-blk-pci,drive=hd -blockdev qcow2,node-name=hd,file.driver=file,file.filename=$ASSETS/disk.qcow2"
