BUF_IMAGE=docker.io/bufbuild/buf:latest
BUF=podman run --volume "$(shell pwd):/workspace" --workdir /workspace docker.io/bufbuild/buf:latest

GENERATED_GO=go/jumpstarter/v1/jumpstarter.pb.go go/jumpstarter/v1/jumpstarter_grpc.pb.go \
			 go/jumpstarter/v1/router.pb.go go/jumpstarter/v1/router_grpc.pb.go
GENERATED_PYTHON=python/jumpstarter/v1/jumpstarter_pb2.py python/jumpstarter/v1/jumpstarter_pb2_grpc.py \
				 python/jumpstarter/v1/router_pb2.py python/jumpstarter/v1/router_pb2_grpc.py

all: $(GENERATED_GO) $(GENERATED_PYTHON)

lint:
	$(BUF) lint
	
go/jumpstarter/v1/jumpstarter.pb.go: proto/jumpstarter/v1/jumpstarter.proto
	$(BUF) generate

go/jumpstarter/v1/jumpstarter_grpc.pb.go: proto/jumpstarter/v1/jumpstarter.proto
	$(BUF) generate


go/jumpstarter/v1/router.pb.go: proto/jumpstarter/v1/router.proto
	$(BUF) generate

go/jumpstarter/v1/router_grpc.pb.go: proto/jumpstarter/v1/router.proto
	$(BUF) generate



.PHONY: lint generate