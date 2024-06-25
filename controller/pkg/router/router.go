package router

import (
	"context"
	"log"
	"sync"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/token"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type RouterConfig struct{}

type RouterServer struct {
	pb.UnimplementedRouterServiceServer
	pending *sync.Map
	config  *RouterConfig
}

type streamCtx struct {
	cancel context.CancelFunc
	stream pb.RouterService_StreamServer
}

func NewRouterServer(config *RouterConfig) (*RouterServer, error) {
	return &RouterServer{
		pending: &sync.Map{},
		config:  config,
	}, nil
}

func (s *RouterServer) Stream(stream pb.RouterService_StreamServer) error {
	ctx := stream.Context()

	bearer, err := token.BearerTokenFromContext(ctx)
	if err != nil {
		return err
	}

	// TODO: use proper psk/iss/aud
	claims, err := token.ParseWithClaims[RouterClaims](bearer, "stream-key", "controller", "router")
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

	ctx, cancel := context.WithDeadline(ctx, exp.Time)
	defer cancel()

	// TODO: periodically check for token revocation and call cancel

	sctx := streamCtx{
		cancel: cancel,
		stream: stream,
	}

	actual, loaded := s.pending.LoadOrStore(claims.Stream, sctx)
	if loaded {
		log.Printf("subject %s connected to stream %s\n", sub, claims.Stream)
		defer actual.(streamCtx).cancel()
		return forward(ctx, stream, actual.(streamCtx).stream)
	} else {
		log.Printf("subject %s waiting on stream %s\n", sub, claims.Stream)
		select {
		case <-ctx.Done():
			return nil
		}
	}
}
