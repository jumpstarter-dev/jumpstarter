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
	// FIXME: check bootstrap token
	sub := req.GetUuid()

	t := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Issuer:    s.config.Principal,
		Audience:  jwt.ClaimStrings{s.config.Router.Principal},
		Subject:   sub,
		ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
	})

	signed, err := t.SignedString([]byte(s.config.PrivateKey))
	if err != nil {
		return nil, status.Errorf(codes.Internal, "unable to get issue router token")
	}

	return &pb.RegisterResponse{
		RouterEndpoint: "", // TODO: ignored for now
		RouterToken:    signed,
	}, nil

}
