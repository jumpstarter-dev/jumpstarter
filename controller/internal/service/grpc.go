package service

import (
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"
)

func KeepaliveEnforcementPolicy() grpc.ServerOption {
	return grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
		MinTime:             30 * time.Second,
		PermitWithoutStream: true,
	})
}
