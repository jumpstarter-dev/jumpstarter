#!/usr/bin/env bash

cd "$(dirname "$0")"

# run only in a container
if [[ -z "$container" && ! -f /.dockerenv ]]; then
    exec podman run --rm -it -v $(pwd):/host:Z -w /host fedora:42 "$0" "$@"
else
	set -euo pipefail
	BUILDROOT_DIR="/var/tmp/buildroot"

	dnf install --setopt=install_weak_deps=false -y git make gcc gcc-c++ which file diffutils \
		wget cpio rsync bc lzop zip patch perl tar qemu-system-aarch64 qemu-img unzboot \
		uboot-tools kmod awk zstd

	git clone --depth 1 --branch 2025.05  https://github.com/buildroot/buildroot "${BUILDROOT_DIR}"

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
fi
