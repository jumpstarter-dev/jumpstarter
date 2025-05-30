#!/bin/bash

dnf install --setopt=install_weak_deps=false -y git make gcc gcc-c++ which file diffutils wget cpio rsync bc lzop zip patch perl tar qemu-system-aarch64 qemu-img unzboot uboot-tools kmod

git clone --depth 1 --branch 2025.05-rc2  https://github.com/buildroot/buildroot /buildroot

./add_kernel.sh
cp -R overlay /buildroot
cp renesas_s4_defconfig  /buildroot/configs/
( cd /buildroot; make renesas_s4_defconfig && make )
mkimage -f flasher.its data/flasher.itb
