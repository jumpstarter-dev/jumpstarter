# Image preparation scripts

This directory contains scripts to prepare the image for the example SOC test,
running `make` should:

* Download a minimal raspbian image
* Inject the settings for the test to be performed (enable UART, setup basic password, tpm dtb)

You will need guestmount installed, sudo permissions.

fuse must be configured to enable `user_allow_other` in `/etc/fuse.conf`.


```shell
$ make
make download-image
make[1]: Entering directory '/home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/image'
scripts/download-latest-raspbian
https://downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2024-07-04/2024-07-04-raspios-bookworm-armhf-lite.img.xz
--2024-10-17 12:12:43--  https://downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2024-07-04/2024-07-04-raspios-bookworm-armhf-lite.img.xz
Resolving downloads.raspberrypi.org (downloads.raspberrypi.org)... 2a00:1098:80:56::2:1, 2a00:1098:80:56::1:1, 2a00:1098:82:47::1, ...
Connecting to downloads.raspberrypi.org (downloads.raspberrypi.org)|2a00:1098:80:56::2:1|:443... connected.
HTTP request sent, awaiting response... 200 OK
Length: 523828628 (500M) [application/x-xz]
Saving to: ‘./images/downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2024-07-04/2024-07-04-raspios-bookworm-armhf-lite.img.xz’

downloads.raspberrypi.org/raspios_lite_armhf/ima 100%[=======================================================================================================>] 499.56M  77.9MB/s    in 6.6s

2024-10-17 12:12:53 (75.3 MB/s) - ‘./images/downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2024-07-04/2024-07-04-raspios-bookworm-armhf-lite.img.xz’ saved [523828628/523828628]

FINISHED --2024-10-17 12:12:53--
Total wall clock time: 10s
Downloaded: 1 files, 500M in 6.6s (75.3 MB/s)
Latest image: ./images/downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2024-07-04/2024-07-04-raspios-bookworm-armhf-lite.img.xz
Updating link from latest.raw.xz -> ./images/downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2024-07-04/2024-07-04-raspios-bookworm-armhf-lite.img.xz
make[1]: Leaving directory '/home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/image'
xz -d -f -v -T0 -k images/latest.raw.xz
images/latest.raw.xz (1/1)
  100 %     499.6 MiB / 2,512.0 MiB = 0.199   102 MiB/s       0:24
touch images/latest.raw
rm -f images/.prepared
umount mnt || true
umount: /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/image/mnt: not mounted.
guestmount -a images/latest.raw -m /dev/sda2 -m /dev/sda1:/boot/firmware -o allow_other --rw mnt
scripts/prepare-latest-raw
+ sudo sed -i 's/console=serial0,115200 console=tty1/console=serial0,115200/g' mnt/boot/firmware/cmdline.txt
+ cat mnt/boot/firmware/cmdline.txt
console=serial0,115200 root=PARTUUID=d28ec40f-02 rootfstype=ext4 fsck.repair=yes rootwait quiet init=/usr/lib/raspberrypi-sys-mods/firstboot
+ cat
+ sudo tee mnt/boot/firmware/custom.toml
# Raspberry Pi First Boot Setup
[system]
hostname = "rpitest"

[user]
name = "root"
password = "changeme"
password_encrypted = false

[ssh]
enabled = false

[wlan]
country = "es"

[locale]
keymap = "es"
timezone = "Europe/Madrid"
+ cat
+ sudo tee -a mnt/boot/firmware/config.txt
dtparam=spi=on
dtoverlay=tpm-slb9670
enable_uart=1
touch images/.prepared
umount mnt
```
