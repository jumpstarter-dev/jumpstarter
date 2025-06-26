#!/bin/sh

IF_WAIT_DELAY=30

if [ "${IFACE}" != "lo" ]; then
	ip link set ${IFACE} up
	printf "Waiting for interface %s carrier" "${IFACE}"
	while [ ${IF_WAIT_DELAY} -gt 0 ]; do
		if [ "$(cat /sys/class/net/${IFACE}/carrier)" = "1" ]; then
			printf "\n"
			exit 0
		fi
		sleep 1
		printf "."
		: $((IF_WAIT_DELAY -= 1))
	done
	printf " timeout!\n"
	exit 1
fi
