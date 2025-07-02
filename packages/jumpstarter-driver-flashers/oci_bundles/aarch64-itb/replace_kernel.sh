#!/bin/bash

KVER="5.14.0-594.543.el9iv.aarch64"
KMOD=(
	# R-Car S4 storage
	fixed
	renesas_sdhi_internal_dmac
	mmc_block
	# R-Car S4 networking
	gpio-rcar
	r8a779f0-ether-serdes
	marvell10g
	rswitch
)

[ $# -ne 1 ] && { echo "$0 [target_overlay_dir]"; exit 1; }
ODIR=$1
mkdir -p ./kernel-automotive
pushd ./kernel-automotive

url="https://cbs.centos.org/kojifiles/packages/kernel-automotive"
url="$url/$(echo $KVER | sed -r 's|-|/|; s|\.([^.]*)$|/\1|')"
pkgs=(
	kernel-automotive-core-$KVER.rpm
	kernel-automotive-modules-$KVER.rpm
	kernel-automotive-modules-core-$KVER.rpm
)


# fetch kernel rpm packages
for pkg in "${pkgs[@]}"; do
	[[ -f $pkg ]] || wget "$url/$pkg" || exit 1
done

# extract kernel rpm packages
for pkg in "${pkgs[@]}"; do
	echo -n "extracting $pkg ... "
	rpm2cpio $pkg | cpio -id
done
echo "extracting kernel ..."
unzboot lib/modules/$KVER/vmlinuz vmlinuz
ln -sfn lib/modules/$KVER/dtb dtb
echo "updating module deps ..."
depmod --errsyms --filesyms lib/modules/$KVER/System.map --basedir $PWD $KVER
echo "building required modules list ..."
for mod in ${KMOD[@]}; do
	modprobe -d $PWD -S $KVER --show-depends $mod
done | sed "s|$PWD||; s|^builtin|# builtin|; s|\\.ko\\.zst|.ko|" > modlist

popd

echo "installing modules into overlay dir ..."
mkdir -p $ODIR/lib/modules $ODIR/etc/init.d || exit 1
sed -nr 's|^insmod ||p' < ./kernel-automotive/modlist | while read mod; do
	mkdir -p "$ODIR$(dirname $mod)"
	zstd -d "./kernel-automotive$mod.zst" -o "$ODIR$mod"
done

echo "adding modules start-up script to overlay ..."
script=$ODIR/etc/init.d/S01modules
cat >$script <<EOF
#!/bin/sh

if [ "\$1" = "start" ]; then
$(cat ./kernel-automotive/modlist)
fi
EOF
chmod +x $script

