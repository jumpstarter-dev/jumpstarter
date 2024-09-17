package service

import (
	"net"
	"net/url"
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
	parsed, err := url.Parse(endpoint)
	if err != nil {
		return nil, nil, err
	}
	hostname := parsed.Hostname()
	ip := net.ParseIP(hostname)
	if ip != nil {
		return []string{}, []net.IP{ip}, nil
	} else {
		return []string{hostname}, []net.IP{}, nil
	}
}
