BUF_IMAGE=docker.io/bufbuild/buf:latest
BUF=podman run --volume "$(shell pwd):/workspace" --workdir /workspace docker.io/bufbuild/buf:latest

all: lint

lint:
	$(BUF) lint
	
.PHONY: lint
