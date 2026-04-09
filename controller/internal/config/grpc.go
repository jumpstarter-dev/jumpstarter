package config

import (
	"fmt"

	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"
)

func LoadGrpcConfiguration(config Grpc) ([]grpc.ServerOption, error) {
	ka := config.Keepalive

	minTime, err := ParseDuration(ka.MinTime)
	if err != nil {
		return nil, fmt.Errorf("failed to parse keepalive minTime: %w", err)
	}
	if minTime == 0 {
		minTime = 1e9
	}

	policy := keepalive.EnforcementPolicy{
		MinTime:             minTime,
		PermitWithoutStream: ka.PermitWithoutStream,
	}

	options := []grpc.ServerOption{
		grpc.KeepaliveEnforcementPolicy(policy),
	}

	timeout, err := ParseDuration(ka.Timeout)
	if err != nil {
		return nil, fmt.Errorf("failed to parse keepalive timeout: %w", err)
	}

	intervalTime, err := ParseDuration(ka.IntervalTime)
	if err != nil {
		return nil, fmt.Errorf("failed to parse keepalive intervalTime: %w", err)
	}

	maxConnectionIdle, err := ParseDuration(ka.MaxConnectionIdle)
	if err != nil {
		return nil, fmt.Errorf("failed to parse keepalive maxConnectionIdle: %w", err)
	}

	maxConnectionAge, err := ParseDuration(ka.MaxConnectionAge)
	if err != nil {
		return nil, fmt.Errorf("failed to parse keepalive maxConnectionAge: %w", err)
	}

	maxConnectionAgeGrace, err := ParseDuration(ka.MaxConnectionAgeGrace)
	if err != nil {
		return nil, fmt.Errorf("failed to parse keepalive maxConnectionAgeGrace: %w", err)
	}

	params := keepalive.ServerParameters{
		Timeout:               timeout,
		Time:                  intervalTime,
		MaxConnectionIdle:     maxConnectionIdle,
		MaxConnectionAge:      maxConnectionAge,
		MaxConnectionAgeGrace: maxConnectionAgeGrace,
	}

	if params != (keepalive.ServerParameters{}) {
		options = append(options, grpc.KeepaliveParams(params))
	}

	return options, nil
}
