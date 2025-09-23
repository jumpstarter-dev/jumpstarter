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
		uboot-tools kmod awk zstd lz4  kernel dtc rpm-build

	# FIXME remove in Fedora 43
	# until unzboot is updated, use the build directly
	rpm -Uvh https://kojipkgs.fedoraproject.org//packages/unzboot/0.1~git.20250502.0c0c3ad/2.fc43/aarch64/unzboot-0.1~git.20250502.0c0c3ad-2.fc43.aarch64.rpm

	git clone --depth 1 --branch 2025.08  https://github.com/buildroot/buildroot "${BUILDROOT_DIR}"

	# build buildroot (initramfs)
	cp buildroot_defconfig  "${BUILDROOT_DIR}/configs/"
	cp -R overlay "${BUILDROOT_DIR}"
	./kernel_fedora.sh "${BUILDROOT_DIR}/overlay"
	( cd "${BUILDROOT_DIR}"; make buildroot_defconfig && make )
	dtc s32g3.dts -o s32g3.dtb
	mkimage -f fedora.its data/flasher-fedora.itb
	rm -rf "${BUILDROOT_DIR}/overlay"

	# replace kernel with kernel-automotive and rebuild
	cp -R overlay "${BUILDROOT_DIR}"
	./kernel_automotive.sh "${BUILDROOT_DIR}/overlay"
	( cd "${BUILDROOT_DIR}" && make )
	mkimage -f automotive.its data/flasher-automotive.itb
	rm -rf "${BUILDROOT_DIR}/overlay"
fi
