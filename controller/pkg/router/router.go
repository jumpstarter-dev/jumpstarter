package router

import (
	"context"
	"sync"

	"github.com/golang-jwt/jwt/v5"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/token"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func BearerToken(ctx context.Context, psk string, issuer string, audience string) (*jwt.Token, error) {
	authorization, err := token.BearerTokenFromContext(ctx)
	if err != nil {
		return nil, err
	}

	token, err := jwt.Parse(authorization,
		func(t *jwt.Token) (interface{}, error) {
			return []byte(psk), nil
		},
		jwt.WithValidMethods([]string{
			jwt.SigningMethodHS256.Alg(),
			jwt.SigningMethodHS512.Alg(),
		}),
		jwt.WithIssuer(issuer),
		jwt.WithAudience(audience),
		jwt.WithExpirationRequired(),
	)
	if err != nil || !token.Valid {
		return nil, status.Errorf(codes.PermissionDenied, "unable to validate jwt token")
	}

	return token, nil
}

type RouterConfig struct {
	PSK string
}

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

	claims, err := token.ParseWithClaims[RouterClaims](bearer, s.config.PSK, "controller", "router")
	if err != nil {
		return err
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
		defer actual.(streamCtx).cancel()
		return forward(ctx, stream, actual.(streamCtx).stream)
	} else {
		select {
		case <-ctx.Done():
			return nil
		}
	}
}
