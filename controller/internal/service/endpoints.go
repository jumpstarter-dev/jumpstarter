package service

import (
	"net"
	"os"
)

func controllerEndpoint() string {
	ep := os.Getenv("GRPC_ENDPOINT")
	if ep == "" {
		return "localhost:8082"
	}
	return ep
}

func routerEndpoint() string {
	ep := os.Getenv("GRPC_ROUTER_ENDPOINT")
	if ep == "" {
		return "localhost:8083"
	}
	return ep
}

func endpointToSAN(endpoint string) ([]string, []net.IP, error) {
	host, _, err := net.SplitHostPort(endpoint)
	if err != nil {
		return nil, nil, err
	}
	ip := net.ParseIP(host)
	if ip != nil {
		return []string{}, []net.IP{ip}, nil
	} else {
		return []string{host}, []net.IP{}, nil
	}
}
