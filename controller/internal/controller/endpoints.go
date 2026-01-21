package controller

import (
	"os"
)

func controllerEndpoint() string {
	ep := os.Getenv("GRPC_ENDPOINT")
	if ep == "" {
		return "localhost:8082"
	}
	return ep
}
