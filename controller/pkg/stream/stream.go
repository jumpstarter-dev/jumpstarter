package stream

import (
	"context"
	"sync"

	"github.com/golang-jwt/jwt/v5"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/router"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type StreamConfig struct {
	Principal string
	Router    struct {
		PublicKey string
		Principal string
	}
}

type StreamServer struct {
	pb.UnimplementedStreamServiceServer
	Map    *sync.Map
	config *StreamConfig
}

type streamCtx struct {
	cancel context.CancelFunc
	stream pb.StreamService_StreamServer
}

func NewStreamServer(config *StreamConfig) (*StreamServer, error) {
	return &StreamServer{
		Map:    &sync.Map{},
		config: config,
	}, nil
}

func (s *StreamServer) Stream(stream pb.StreamService_StreamServer) error {
	ctx := stream.Context()

	token, err := router.BearerToken(ctx, func(t *jwt.Token) (interface{}, error) {
		return []byte(s.config.Router.PublicKey), nil
	}, s.config.Router.Principal, s.config.Principal)
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

	ctx, cancel := context.WithDeadline(ctx, exp.Time)

	cond := streamCtx{
		cancel: cancel,
		stream: stream,
	}

	actual, loaded := s.Map.LoadOrStore(sub, cond)
	if loaded {
		defer actual.(streamCtx).cancel()
		return forward(ctx, stream, actual.(streamCtx).stream)
	}

	select {
	case <-ctx.Done():
		return nil
	}
}
