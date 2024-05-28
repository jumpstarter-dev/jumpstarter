
VERSION := $(shell git describe --tags --always)
LDFLAGS := -ldflags="-X 'github.com/jumpstarter-dev/jumpstarter/cmd.VERSION=${VERSION}'"
TAGS    :=

SOURCES := $(shell find -name '*.go')

jumpstarter: $(SOURCES)
	go build -tags '$(TAGS)' ${LDFLAGS}

containers:
	podman build ./containers/ -f Containerfile -t quay.io/mangelajo/jumpstarter:latest
	podman build ./containers/ -f Containerfile.guestfs -t quay.io/mangelajo/guestfs-tools:latest

push-containers: containers
	podman push quay.io/mangelajo/jumpstarter:latest
	podman push quay.io/mangelajo/guestfs-tools:latest

fmt:
	gofmt -w -s .

all: jumpstarter

.PHONY: all fmt containers push-containers
