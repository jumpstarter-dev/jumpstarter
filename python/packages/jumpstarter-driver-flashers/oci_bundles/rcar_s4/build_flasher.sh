#!/bin/bash

dnf install --setopt=install_weak_deps=false -y git make gcc gcc-c++ which file diffutils wget cpio rsync bc lzop zip patch perl tar qemu-system-aarch64 qemu-img unzboot uboot-tools kmod awk

git clone --depth 1 --branch 2025.05  https://github.com/buildroot/buildroot /buildroot

./replace_kernel.sh
cp -R overlay /buildroot
cp rootfs_only_defconfig  /buildroot/configs/
( cd /buildroot; make rootfs_only_defconfig && make )
mkimage -f flasher.its data/flasher.itb
