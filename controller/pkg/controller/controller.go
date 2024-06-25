package controller

import (
	"context"
	"log"
	"sync"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/router"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/token"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type ControllerConfig struct{}

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
		config:    config,
		listenMap: &sync.Map{},
	}, nil
}

func (s *ControllerServer) Listen(_ *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()

	bearer, err := token.BearerTokenFromContext(ctx)
	if err != nil {
		return err
	}

	// TODO: use k8s token introspection endpoint
	claims, err := token.ParseWithClaims[jwt.RegisteredClaims](bearer, "k8s-key", "k8s", "controller")
	if err != nil {
		return err
	}

	sub, err := claims.GetSubject()
	if err != nil {
		return status.Errorf(codes.PermissionDenied, "unable to get sub claim")
	}

	exp, err := claims.GetExpirationTime()
	if err != nil {
		return status.Errorf(codes.PermissionDenied, "unable to get exp claim")
	}

	// TODO: periodically check for token revocation and revoke derived stream tokens

	ctx, cancel := context.WithDeadline(ctx, exp.Time)
	defer cancel()

	lctx := listenCtx{
		cancel: cancel,
		stream: stream,
	}

	_, loaded := s.listenMap.LoadOrStore(sub, lctx)

	if loaded {
		return status.Errorf(codes.AlreadyExists, "exporter is already listening")
	}

	log.Printf("subject %s listening\n", sub)

	defer s.listenMap.Delete(sub)

	select {
	case <-ctx.Done():
		log.Printf("subject %s left\n", sub)
		return nil
	}
}

func (s *ControllerServer) streamToken(sub string, peer string, exp *jwt.NumericDate, stream string) (string, error) {
	stoken := jwt.NewWithClaims(jwt.SigningMethodHS256, router.RouterClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			// TODO: use proper iss/aud
			Issuer:    "controller",
			Audience:  jwt.ClaimStrings{"router"},
			Subject:   sub,
			ExpiresAt: exp,
		},
		Stream: stream,
		Peer:   peer,
	})

	signed, err := stoken.SignedString([]byte("stream-key"))
	if err != nil {
		return "", status.Errorf(codes.Internal, "unable to issue stream token")
	}

	return signed, nil
}

func (s *ControllerServer) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	bearer, err := token.BearerTokenFromContext(ctx)
	if err != nil {
		return nil, err
	}

	// TODO: use k8s token introspection endpoint
	claims, err := token.ParseWithClaims[jwt.RegisteredClaims](bearer, "k8s-key", "k8s", "controller")
	if err != nil {
		return nil, err
	}

	sub, err := claims.GetSubject()
	if err != nil {
		return nil, status.Errorf(codes.PermissionDenied, "unable to get sub claim")
	}

	exp, err := claims.GetExpirationTime()
	if err != nil {
		return nil, status.Errorf(codes.PermissionDenied, "unable to get exp claim")
	}

	// TODO: check (client, exporter) tuple against leases

	log.Printf("subject %s connecting to %s\n", sub, req.GetUuid())

	value, ok := s.listenMap.Load(req.GetUuid())
	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream := uuid.New().String()

	etoken, err := s.streamToken(req.GetUuid(), sub, exp, stream)
	if err != nil {
		return nil, err
	}

	ctoken, err := s.streamToken(sub, req.GetUuid(), exp, stream)
	if err != nil {
		return nil, err
	}

	// TODO: find best router from list
	endpoint := "unix:/tmp/jumpstarter-router.sock"

	// TODO: check listener matches subject
	err = value.(listenCtx).stream.Send(&pb.ListenResponse{
		RouterEndpoint: endpoint,
		RouterToken:    etoken,
	})
	if err != nil {
		return nil, err
	}

	return &pb.DialResponse{
		RouterEndpoint: endpoint,
		RouterToken:    ctoken,
	}, nil
}
