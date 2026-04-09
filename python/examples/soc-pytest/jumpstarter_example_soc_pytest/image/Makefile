all: prepare-image

###############################################################################
# Image preparation targets
###############################################################################

download-image:
	scripts/download-latest-raspbian

prepare-image: images/latest.raw mount
	scripts/prepare-latest-raw
	touch images/.prepared
	umount mnt

images/.prepared:
	make prepare-image

images/latest.raw.xz:
	make download-image

images/latest.raw: images/latest.raw.xz
	xz -d -f -v -T0 -k $^
	touch images/latest.raw
	rm -f images/.prepared

clean-image:
	rm -f images/.prepared
	rm -f images/latest.raw

clean-images: clean-image
	rm -rf images/download.raspberrypi.org
	rm -rf images/latest.raw.xz

clean: clean-image clean-images

###############################################################################
# Image manipulation targets
###############################################################################

mnt:
	mkdir -p $@

umount:
	umount mnt || true

mount: umount images/latest.raw mnt
	guestmount -a images/latest.raw -m /dev/sda2 -m /dev/sda1:/boot/firmware -o allow_other --rw mnt


###############################################################################
# phony targets are targets which don't produce files, just for utility
###############################################################################


.PHONY: download-image prepare-image
.PHONY: mount umount
