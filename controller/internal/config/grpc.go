package config

import (
	"fmt"

	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"
)

// LoadGrpcConfiguration loads the gRPC server configuration from the parsed Config struct.
// It creates a gRPC server option with keepalive enforcement policy configured.
func LoadGrpcConfiguration(config Grpc) (grpc.ServerOption, error) {
	ka := config.Keepalive

	// Parse MinTime with default of 1s
	minTime, err := ParseDuration(ka.MinTime)
	if err != nil {
		return nil, fmt.Errorf("failed to parse keepalive minTime: %w", err)
	}
	if minTime == 0 {
		minTime = 1e9 // 1 second default
	}

	// Create the keepalive enforcement policy
	policy := keepalive.EnforcementPolicy{
		MinTime:             minTime,
		PermitWithoutStream: ka.PermitWithoutStream,
	}

	return grpc.KeepaliveEnforcementPolicy(policy), nil
}
