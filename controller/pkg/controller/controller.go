package controller

import (
	"context"
	"sync"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/router"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type ControllerConfig struct {
	Issuer     string
	Audience   string
	PublicKey  string
	PrivateKey string
}

type ControllerServer struct {
	pb.UnimplementedControllerServiceServer
	config    *ControllerConfig
	listenMap *sync.Map
}

type listenCtx struct {
	cancel context.CancelFunc
	stream pb.ControllerService_ListenServer
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

func (s *ControllerServer) Listen(_ *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()

	token, err := router.BearerToken(ctx,
		func(t *jwt.Token) (interface{}, error) { return []byte(s.config.PublicKey), nil },
		s.config.Issuer,
		s.config.Audience,
	)
	if err != nil {
		return err
	}

	sub, err := token.Claims.GetSubject()
	if err != nil {
		return status.Errorf(codes.PermissionDenied, "unable to get sub claim")
	}

	exp, err := token.Claims.GetExpirationTime()
	if err != nil {
		return status.Errorf(codes.PermissionDenied, "unable to get exp claim")
	}

	// TODO: call cancel on token revocation
	ctx, cancel := context.WithDeadline(ctx, exp.Time)
	defer cancel()

	lis := listenCtx{
		cancel: cancel,
		stream: stream,
	}

	_, loaded := s.listenMap.LoadOrStore(sub, lis)

	if loaded {
		return status.Errorf(codes.AlreadyExists, "another node is already listening on the sub")
	}

	defer s.listenMap.Delete(sub)

	select {
	case <-ctx.Done():
		return nil
	}
}

func (s *ControllerServer) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	token, err := router.BearerToken(ctx,
		func(t *jwt.Token) (interface{}, error) { return []byte(s.config.PublicKey), nil },
		s.config.Issuer,
		s.config.Audience,
	)
	if err != nil {
		return nil, err
	}

	exp, err := token.Claims.GetExpirationTime()
	if err != nil {
		return nil, status.Errorf(codes.PermissionDenied, "unable to get exp claim")
	}

	// TODO: check allowlist

	value, ok := s.listenMap.Load(req.GetUuid())
	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream := uuid.New().String()

	t := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Issuer:   s.config.Audience,
		Audience: jwt.ClaimStrings{"stream"}, // a list of stream servers for LB
		Subject:  stream,
		// inherit expiration
		// TODO: cascading revocation
		ExpiresAt: exp,
	})

	signed, err := t.SignedString([]byte(s.config.PrivateKey))
	if err != nil {
		return nil, status.Errorf(codes.Internal, "unable to issue stream token")
	}

	err = value.(listenCtx).stream.Send(&pb.ListenResponse{
		RouterEndpoint: "",
		RouterToken:    signed,
	})
	if err != nil {
		return nil, err
	}

	return &pb.DialResponse{
		RouterEndpoint: "",
		RouterToken:    signed,
	}, nil
}
