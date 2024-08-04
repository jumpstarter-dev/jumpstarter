package service

import (
	"os"
	"strings"
)

func routerEndpoint() string {
	ep := os.Getenv("GRPC_ROUTER_ENDPOINT")
	if ep == "" {
		return "localhost:8083"
	}
	return ep
}

func grpcEndpoint() string {
	ep := os.Getenv("GRPC_ENDPOINT")
	if ep == "" {
		return "localhost:8082"
	}
	return ep
}

func routerHostName() string {
	ep := routerEndpoint()
	// remove the port from the endpoint
	parts := strings.Split(ep, ":")
	return parts[0]
}

func grpcHostName() string {
	ep := grpcEndpoint()
	// remove the port from the endpoint
	parts := strings.Split(ep, ":")
	return parts[0]
}
