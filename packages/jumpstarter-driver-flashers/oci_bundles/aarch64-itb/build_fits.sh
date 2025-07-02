#!/usr/bin/env bash

# If this script may be sourced, you want ${BASH_SOURCE[0]} instead of $0
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  SOURCE="$(readlink "$SOURCE")"
done

SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
cd "${SCRIPT_DIR}"

BUILDROOT_DIR="${SCRIPT_DIR}/buildroot"

set -euo pipefail


sudo dnf install --setopt=install_weak_deps=false -y git make gcc gcc-c++ which file diffutils \
	wget cpio rsync bc lzop zip patch perl tar qemu-system-aarch64 qemu-img unzboot \
	uboot-tools kmod awk zstd

if [[ ! -d "${BUILDROOT_DIR}" ]]; then
	git clone --depth 1 --branch 2025.05  https://github.com/buildroot/buildroot "${BUILDROOT_DIR}"
fi

# build default buildroot kernel & initramfs
cp buildroot_defconfig  "${BUILDROOT_DIR}/configs/"
cp -R overlay "${BUILDROOT_DIR}"
( cd "${BUILDROOT_DIR}"; make buildroot_defconfig && make )
mkimage -f buildroot.its data/flasher-buildroot.itb
rm -rf "${BUILDROOT_DIR}/overlay"

# replace kernel with kernel-automotive and rebuild
cp -R overlay "${BUILDROOT_DIR}"
./replace_kernel.sh "${BUILDROOT_DIR}/overlay"
( cd "${BUILDROOT_DIR}" && make )
mkimage -f automotive.its data/flasher-automotive.itb
rm -rf "${BUILDROOT_DIR}/overlay"
