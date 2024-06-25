package controller

import (
	"context"
	"time"

	"github.com/golang-jwt/jwt/v5"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type ControllerConfig struct {
	Principal  string
	PrivateKey string
	Router     struct {
		Principal string
	}
}

type ControllerServer struct {
	pb.UnimplementedControllerServiceServer
	config *ControllerConfig
}

func NewControllerServer(config *ControllerConfig) (*ControllerServer, error) {
	return &ControllerServer{
		config: config,
	}, nil
}

func (s *ControllerServer) Register(ctx context.Context, req *pb.RegisterRequest) (*pb.RegisterResponse, error) {
	// TODO: check token
	return &pb.RegisterResponse{}, nil
}
