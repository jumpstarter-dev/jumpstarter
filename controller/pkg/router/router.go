package router

import (
	"context"
	"strings"
	"sync"

	"github.com/golang-jwt/jwt/v5"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func BearerToken(ctx context.Context, keyFunc jwt.Keyfunc, issuer string, audience string) (*jwt.Token, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "missing metadata")
	}

	authorizations := md.Get("authorization")

	if len(authorizations) < 1 {
		return nil, status.Errorf(codes.Unauthenticated, "missing authorization header")
	}

	// https://www.rfc-editor.org/rfc/rfc7230#section-3.2.2
	// A sender MUST NOT generate multiple header fields with the same field name in a message
	if len(authorizations) > 1 {
		return nil, status.Errorf(codes.InvalidArgument, "multiple authorization headers")
	}

	// Invariant: len(authorizations) == 1
	authorization := authorizations[0]

	// Reference: https://github.com/golang-jwt/jwt/blob/62e504c2/request/extractor.go#L93
	if len(authorization) < 7 || !strings.EqualFold(authorization[:7], "Bearer ") {
		return nil, status.Errorf(codes.InvalidArgument, "malformed authorization header")
	}

	// Invariant: len(authorization) >= 7
	token, err := jwt.Parse(authorization[7:],
		keyFunc,
		jwt.WithValidMethods([]string{"HS256", "HS384", "HS512"}),
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
	Issuer    string
	Audience  string
	PublicKey string
}

type RouterServer struct {
	pb.UnimplementedRouterServiceServer
	Map    *sync.Map
	config *RouterConfig
}

type streamCtx struct {
	cancel context.CancelFunc
	stream pb.RouterService_StreamServer
}

func NewRouterServer(config *RouterConfig) (*RouterServer, error) {
	return &RouterServer{
		Map:    &sync.Map{},
		config: config,
	}, nil
}

func (s *RouterServer) Stream(stream pb.RouterService_StreamServer) error {
	ctx := stream.Context()

	token, err := BearerToken(ctx, func(t *jwt.Token) (interface{}, error) {
		return []byte(s.config.PublicKey), nil
	}, s.config.Issuer, s.config.Audience)
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
