package router

import (
	"context"
	"strings"
	"sync"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type RouterConfig struct {
	Principal  string
	PrivateKey string
	Controller struct {
		PublicKey string
		Principal string
	}
	Stream struct {
		Principal string
	}
}

type RouterServer struct {
	pb.UnimplementedRouterServiceServer
	listenMap *sync.Map
	config    *RouterConfig
}

type listenCtx struct {
	cancel context.CancelFunc
	stream pb.RouterService_ListenServer
}

func NewRouterServer(config *RouterConfig) (*RouterServer, error) {
	return &RouterServer{
		listenMap: &sync.Map{},
		config:    config,
	}, nil
}

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

func (s *RouterServer) Listen(_ *pb.ListenRequest, stream pb.RouterService_ListenServer) error {
	ctx := stream.Context()

	token, err := BearerToken(ctx,
		func(t *jwt.Token) (interface{}, error) { return []byte(s.config.Controller.PublicKey), nil },
		s.config.Controller.Principal,
		s.config.Principal,
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

	select {
	case <-ctx.Done():
		return nil
	}
}

func (s *RouterServer) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	token, err := BearerToken(ctx,
		func(t *jwt.Token) (interface{}, error) { return []byte(s.config.Controller.PublicKey), nil },
		s.config.Controller.Principal,
		s.config.Principal,
	)
	if err != nil {
		return nil, err
	}

	exp, err := token.Claims.GetExpirationTime()
	if err != nil {
		return nil, status.Errorf(codes.PermissionDenied, "unable to get exp claim")
	}

	// TODO: check allowlist

	value, ok := s.listenMap.Load(req.GetSub())
	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream := uuid.New().String()

	t := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Issuer:   s.config.Principal,
		Audience: jwt.ClaimStrings{s.config.Stream.Principal}, // a list of stream servers for LB
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
		Token: signed,
	})
	if err != nil {
		return nil, err
	}

	return &pb.DialResponse{
		Token: signed,
	}, nil
}
