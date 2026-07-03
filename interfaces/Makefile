BUF_IMAGE=docker.io/bufbuild/buf:latest
BUF=podman run --volume "$(shell pwd):/workspace" --workdir /workspace docker.io/bufbuild/buf:latest

# Driver modules whose interface classes are exported as .proto contracts AND whose
# concrete drivers are recorded in the driver registry (registry/python.yaml) — the
# static `type:` → interface map `jumpstarter-codegen --kind device` consults so it
# never imports driver code. All in-tree driver packages that import cleanly belong
# here. Known exception: jumpstarter_driver_iscsi.driver needs libudev (Linux-only).
IMPORT_PACKAGES=\
	jumpstarter_driver_adb.driver \
	jumpstarter_driver_androidemulator.driver \
	jumpstarter_driver_ble.driver \
	jumpstarter_driver_can.driver \
	jumpstarter_driver_composite.driver \
	jumpstarter_driver_corellium.driver \
	jumpstarter_driver_doip.driver \
	jumpstarter_driver_dut_network.driver \
	jumpstarter_driver_dutlink.driver \
	jumpstarter_driver_energenie.driver \
	jumpstarter_driver_esp32.driver \
	jumpstarter_driver_flashers.driver \
	jumpstarter_driver_gpiod.driver \
	jumpstarter_driver_http.driver \
	jumpstarter_driver_http_power.driver \
	jumpstarter_driver_mitmproxy.driver \
	jumpstarter_driver_network.driver \
	jumpstarter_driver_noyito_relay.driver \
	jumpstarter_driver_obd.driver \
	jumpstarter_driver_opendal.driver \
	jumpstarter_driver_pi_pico.driver \
	jumpstarter_driver_power.driver \
	jumpstarter_driver_power.driver_native \
	jumpstarter_driver_probe_rs.driver \
	jumpstarter_driver_pyserial.driver \
	jumpstarter_driver_qemu.driver \
	jumpstarter_driver_renode.driver \
	jumpstarter_driver_ridesx.driver \
	jumpstarter_driver_sdwire.driver \
	jumpstarter_driver_shell.driver \
	jumpstarter_driver_snmp.driver \
	jumpstarter_driver_someip.driver \
	jumpstarter_driver_ssh.driver \
	jumpstarter_driver_ssh_mitm.driver \
	jumpstarter_driver_ssh_mount.driver \
	jumpstarter_driver_stlink_msd.driver \
	jumpstarter_driver_tasmota.driver \
	jumpstarter_driver_tftp.driver \
	jumpstarter_driver_tmt.driver \
	jumpstarter_driver_uboot.driver \
	jumpstarter_driver_uds.driver \
	jumpstarter_driver_uds_can.driver \
	jumpstarter_driver_uds_doip.driver \
	jumpstarter_driver_ustreamer.driver \
	jumpstarter_driver_vnc.driver \
	jumpstarter_driver_xcp.driver \
	jumpstarter_driver_yepkit.driver

GENERATE_FLAGS=$(foreach pkg,$(IMPORT_PACKAGES),--import-package $(pkg))

all: generate

# Regenerate the interface .proto sources from the Python interface classes.
# The protos are the language-agnostic contract — do not edit them by hand.
generate:
	cd ../python && uv run python -m jumpstarter.driver.proto_gen generate-all \
		--output-dir ../interfaces/proto \
		--registry-out ../interfaces/registry/python.yaml \
		$(GENERATE_FLAGS)

lint:
	$(BUF) lint

build:
	$(BUF) build

.PHONY: all generate lint build
