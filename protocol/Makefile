# renovate: datasource=docker depName=docker.io/bufbuild/buf
BUF_VERSION=1.71.0
BUF_IMAGE=docker.io/bufbuild/buf:$(BUF_VERSION)
BUF=podman run --volume "$(shell pwd):/workspace" --workdir /workspace $(BUF_IMAGE)

all: lint

lint:
	$(BUF) lint
	
.PHONY: lint
