#!/bin/bash
set -euo pipefail

dnf install --setopt=install_weak_deps=false -y git make gcc gcc-c++ which file diffutils wget cpio rsync bc lzop zip patch perl tar qemu-system-aarch64 qemu-img unzboot uboot-tools kmod awk

git clone --depth 1 --branch 2025.05  https://github.com/buildroot/buildroot /buildroot

# build default buildroot kernel & initramfs
cp buildroot_defconfig  /buildroot/configs/
cp -R overlay /buildroot
( cd /buildroot; make buildroot_defconfig && make )
mkimage -f buildroot.its data/flasher-buildroot.itb
rm -rf /buildroot/overlay

# replace kernel with kernel-automotive and rebuild
cp -R overlay /buildroot
./replace_kernel.sh /buildroot/overlay
( cd /buildroot && make )
mkimage -f automotive.its data/flasher-automotive.itb
rm -rf /buildroot/overlay
