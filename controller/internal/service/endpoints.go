package service

import (
	"os"
)

func routerEndpoint() string {
	ep := os.Getenv("GRPC_ROUTER_ENDPOINT")
	if ep == "" {
		return "localhost:8083"
	}
	return ep
}
