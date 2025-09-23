#!/bin/bash

KMOD=(
	# S32G3 storage
	sdhci_esdhc_imx
	mmc_block
	# S32G3 networking
	dwmac_s32
	micrel
)

[ $# -ne 1 ] && { echo "$0 [target_overlay_dir]"; exit 1; }
ODIR=$1
mkdir -p ./kernel-fedora
pushd ./kernel-fedora

KVER=$(rpm -q kernel | head -1 | cut -d - -f 2,3)
if [ "$(uname -m)" != "aarch64" ]; then
  echo "ERROR: kernel_fedora.sh must run in an aarch64 container"
  exit 1
fi
unzboot /usr/lib/modules/$KVER/vmlinuz vmlinuz
ln -sfn /lib/modules/$KVER/dtb dtb
echo "building required modules list ..."
for mod in ${KMOD[@]}; do
	modprobe -S $KVER --show-depends $mod
done | sed "s|^builtin|# builtin|; s|\\.ko\\.xz|.ko|" > modlist

popd

echo "installing modules into overlay dir ..."
mkdir -p $ODIR/etc/init.d || exit 1
sed -nr 's|^insmod ||p' < ./kernel-fedora/modlist | while read mod; do
	echo $mod
	mkdir -p "$ODIR$(dirname $mod)"
	xz -dc "$mod.xz" > "$ODIR$mod"
done

echo "adding modules start-up script to overlay ..."
script=$ODIR/etc/init.d/S01modules
cat >$script <<EOF
#!/bin/sh

if [ "\$1" = "start" ]; then
$(cat ./kernel-fedora/modlist)
fi
EOF
chmod +x $script

