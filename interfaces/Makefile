BUF_IMAGE=docker.io/bufbuild/buf:latest
BUF=podman run --volume "$(shell pwd):/workspace" --workdir /workspace docker.io/bufbuild/buf:latest

# Driver modules whose interface classes are exported as .proto contracts.
# Each must expose at least one DriverInterface (a class with a client()
# classmethod that is not itself a concrete Driver). Power is mandatory.
IMPORT_PACKAGES=\
	jumpstarter_driver_power.driver \
	jumpstarter_driver_composite.driver \
	jumpstarter_driver_network.driver \
	jumpstarter_driver_opendal.driver \
	jumpstarter_driver_uds.driver

GENERATE_FLAGS=$(foreach pkg,$(IMPORT_PACKAGES),--import-package $(pkg))

all: generate

# Regenerate the interface .proto sources from the Python interface classes.
# The protos are the language-agnostic contract — do not edit them by hand.
generate:
	cd ../python && uv run python -m jumpstarter.driver.proto_gen generate-all \
		--output-dir ../interfaces/proto \
		$(GENERATE_FLAGS)

lint:
	$(BUF) lint

build:
	$(BUF) build

.PHONY: all generate lint build
