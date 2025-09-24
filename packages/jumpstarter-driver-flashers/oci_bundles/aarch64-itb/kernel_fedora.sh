#!/bin/bash

KMOD=(
	# S32G3 storage
	sdhci_esdhc_imx
	mmc_block
	# S32G3 networking
	dwmac_s32
	micrel
	# TI storage
	sdhci_am654
	mmc_block
	# TI networking
	phy_gmii_sel
	syscon_clk
	dp83867
	reset_ti_sci
	davinci_mdio
	am65_cpts
	gpio_davinci
	k3_udma
	rti_wdt
	i2c_omap
	i2c_mux_pca954x
	irq_ti_sci_intr
	omap_mailbox
	omap_hwspinlock
	phy_j721e_wiz
	ti_k3_r5_remoteproc
	ti_k3_dsp_remoteproc
	k3_j72xx_bandgap
	pinctrl_tps6594
	tps6594_pfsm
	tps6594_i2c
	tps6594_regulator
	rtc_tps6594
	phy_cadence_torrent
	mux_core
	mux_gpio
	mux_mmio
	virtio_rpmsg_bus
	rpmsg_ctrl
	irq_ti_sci_inta
	optee
	omap_rng
	optee_rng
	ti_am65_cpsw_nuss
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
lz4 -f -12 vmlinuz vmlinuz.lz4
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

echo "updating module deps ..."
depmod --errsyms --filesyms /lib/modules/$KVER/System.map --basedir $1 $KVER

echo "adding modules start-up script to overlay ..."
script=$ODIR/etc/init.d/S01modules
cat >$script <<EOF
#!/bin/sh

if [ "\$1" = "start" ]; then
  if dmesg | head | grep -i S32G-VNP-RDB3; then
    modprobe micrel
  fi
fi
EOF
chmod +x $script

