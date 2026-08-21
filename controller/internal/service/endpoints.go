package service

import (
	"fmt"
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

// telemetryEndpoint returns the GRPC_TELEMETRY_ENDPOINT env var value.
// Returns ("", nil) if unset, or an error if set but malformed.
func telemetryEndpoint() (string, error) {
	ep := os.Getenv("GRPC_TELEMETRY_ENDPOINT")
	if ep == "" {
		return "", nil
	}
	if err := validateHostPort(ep); err != nil {
		return "", fmt.Errorf("GRPC_TELEMETRY_ENDPOINT %q is not a valid host:port: %w", ep, err)
	}
	return ep, nil
}

// validateHostPort checks that s is a valid "host:port" with a non-empty host.
func validateHostPort(s string) error {
	host, _, err := net.SplitHostPort(s)
	if err != nil {
		return err
	}
	if host == "" {
		return fmt.Errorf("endpoint %q has no host", s)
	}
	return nil
}

func endpointToSAN(endpoint string) ([]string, []net.IP, error) {
	if err := validateHostPort(endpoint); err != nil {
		return nil, nil, err
	}
	host, _, _ := net.SplitHostPort(endpoint)
	ip := net.ParseIP(host)
	if ip != nil {
		return []string{}, []net.IP{ip}, nil
	} else {
		return []string{host}, []net.IP{}, nil
	}
}
