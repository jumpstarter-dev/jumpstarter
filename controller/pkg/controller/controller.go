package controller

import (
	"context"
	"log"
	"net/url"
	"sync"
	"time"

	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/authn"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	authnv1 "k8s.io/api/authentication/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

type ControllerConfig struct{}

type key int

var audKey key
var expKey key

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

func (s *ControllerServer) audience(ctx context.Context, group string) (*url.URL, *time.Time, error) {
	token, err := authn.BearerTokenFromContext(ctx)
	if err != nil {
		return nil, nil, err
	}

	return authn.Authenticate(
		ctx,
		s.clientset.AuthenticationV1(),
		token,
		"https",
		"jumpstarter-controller.example.com",
		group,
	)
}

func (s *ControllerServer) Listen(_ *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()

	aud, exp, err := s.audience(ctx, "jumpstarter-exporter")
	if err != nil {
		return err
	}

	// TODO: periodically check for token revocation and revoke derived stream tokens

	ctx, cancel := context.WithDeadline(ctx, *exp)
	defer cancel()

	lctx := listenCtx{
		cancel: cancel,
		stream: stream,
	}

	_, loaded := s.listenMap.LoadOrStore(aud.String(), lctx)

	if loaded {
		return status.Errorf(codes.AlreadyExists, "exporter is already listening")
	}

	log.Printf("subject %s listening\n", aud.String())

	defer s.listenMap.Delete(aud.String())

	select {
	case <-ctx.Done():
		log.Printf("subject %s left\n", aud.String())
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
	aud, exp, err := s.audience(ctx, "jumpstarter-client")
	if err != nil {
		return nil, err
	}

	// TODO: check (client, exporter) tuple against leases

	log.Printf("subject %s connecting to %s\n", aud.String(), req.GetUuid())

	value, ok := s.listenMap.Load(req.GetUuid())
	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream := uuid.New().String()

	etoken, err := s.streamToken(req.GetUuid(), aud.String(), stream, int64(exp.Sub(time.Now()).Seconds()))
	if err != nil {
		return nil, err
	}

	ctoken, err := s.streamToken(aud.String(), req.GetUuid(), stream, int64(exp.Sub(time.Now()).Seconds()))
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
