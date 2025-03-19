package config

import (
	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"
	"time"
)

func LoadGrpcConfiguration(config Grpc) (grpc.ServerOption, error) {
	minTime, err := time.ParseDuration(config.Keepalive.MinTime)
	if err != nil {
		return nil, err
	}

	return grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
		MinTime:             minTime,
		PermitWithoutStream: config.Keepalive.PermitWithoutStream,
	}), nil
}
