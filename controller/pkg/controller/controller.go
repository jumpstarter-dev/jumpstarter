package controller

import (
	"context"
	"fmt"
	"log"
	"net/url"
	"slices"
	"sync"
	"time"

	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	jtoken "github.com/jumpstarter-dev/jumpstarter-router/pkg/token"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	authnv1 "k8s.io/api/authentication/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

type ControllerConfig struct{}

type key int

var audienceKey key

type ControllerServer struct {
	pb.UnimplementedControllerServiceServer
	config    *ControllerConfig
	clientset kubernetes.Clientset
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

func (s *ControllerServer) audience(ctx context.Context) (string, error) {
	token, err := jtoken.BearerTokenFromContext(ctx)
	if err != nil {
		return "", err
	}
	// TODO: parse  audience from token
	audience := "https://jumpstarter-controller.example.com/users/testuser"
	review, err := s.clientset.AuthenticationV1().TokenReviews().Create(
		ctx,
		&authnv1.TokenReview{
			Spec: authnv1.TokenReviewSpec{
				Token:     token,
				Audiences: []string{audience},
			},
		},
		metav1.CreateOptions{},
	)
	if err != nil ||
		!review.Status.Authenticated ||
		!slices.Contains(review.Status.Audiences, audience) {
		return "", status.Errorf(codes.Unauthenticated, codes.Unauthenticated.String())
	}
	return audience, nil
}

func (s *ControllerServer) UnaryServerInterceptor(
	ctx context.Context,
	req any,
	info *grpc.UnaryServerInfo,
	handler grpc.UnaryHandler,
) (any, error) {
	aud, err := s.audience(ctx)
	if err != nil {
		return nil, err
	}
	return handler(context.WithValue(ctx, audienceKey, aud), req)
}

type wrappedServerStream struct {
	grpc.ServerStream
	audience string
}

func (ss *wrappedServerStream) Context() context.Context {
	return context.WithValue(ss.ServerStream.Context(), audienceKey, ss.audience)
}

func (s *ControllerServer) StreamServerInterceptor(
	srv any,
	ss grpc.ServerStream,
	info *grpc.StreamServerInfo,
	handler grpc.StreamHandler,
) error {
	aud, err := s.audience(ss.Context())
	if err != nil {
		return err
	}
	return handler(srv, &wrappedServerStream{ss, aud})
}

func (s *ControllerServer) Listen(_ *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()

	audience := ctx.Value(audienceKey).(string)

	// TODO: periodically check for token revocation and revoke derived stream tokens

	ctx, cancel := context.WithDeadline(ctx, time.Now().Add(time.Hour))
	defer cancel()

	lctx := listenCtx{
		cancel: cancel,
		stream: stream,
	}

	_, loaded := s.listenMap.LoadOrStore(audience, lctx)

	if loaded {
		return status.Errorf(codes.AlreadyExists, "exporter is already listening")
	}

	log.Printf("subject %s listening\n", audience)

	defer s.listenMap.Delete(audience)

	select {
	case <-ctx.Done():
		log.Printf("subject %s left\n", audience)
		return nil
	}
}

func (s *ControllerServer) streamToken(sub string, peer string, stream string, exp int64) (string, error) {
	query := url.Values{}
	query.Set("subject", sub)
	query.Set("stream", stream)
	query.Set("peer", peer)

	audience := url.URL{
		Scheme:   "https",
		Host:     "jumpstarter-router.example.com",
		RawQuery: query.Encode(),
	}

	token, err := s.clientset.CoreV1().ServiceAccounts(metav1.NamespaceDefault).CreateToken(
		context.TODO(),
		"jumpstarter-streams",
		&authnv1.TokenRequest{
			Spec: authnv1.TokenRequestSpec{
				Audiences:         []string{audience.String()},
				ExpirationSeconds: &exp,
			},
		},
		metav1.CreateOptions{},
	)
	if err != nil {
		return "", status.Errorf(codes.Internal, "failed to issue stream token")
	}

	return token.Status.Token, nil
}

func (s *ControllerServer) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	audience := ctx.Value(audienceKey).(string)

	// TODO: check (client, exporter) tuple against leases

	log.Printf("subject %s connecting to %s\n", audience, req.GetUuid())

	value, ok := s.listenMap.Load(req.GetUuid())
	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream := uuid.New().String()

	etoken, err := s.streamToken(req.GetUuid(), audience, stream, 3600)
	if err != nil {
		return nil, err
	}

	ctoken, err := s.streamToken(audience, req.GetUuid(), stream, 3600)
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
