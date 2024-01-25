
jumpstarter: main.go pkg/drivers/dutlink-board/*.go pkg/runner/* pkg/harness/*.go cmd/*.go pkg/tools/*.go
	go build

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
