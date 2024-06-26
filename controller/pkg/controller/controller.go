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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

type ControllerConfig struct{}

type ControllerServer struct {
	pb.UnimplementedControllerServiceServer
	clientset *kubernetes.Clientset
	listenMap *sync.Map
}

type listenCtx struct {
	cancel context.CancelFunc
	stream pb.ControllerService_ListenServer
}

func NewControllerServer(clientset *kubernetes.Clientset) (*ControllerServer, error) {
	return &ControllerServer{
		clientset: clientset,
		listenMap: &sync.Map{},
	}, nil
}

func (s *ControllerServer) audience(ctx context.Context, username string) (*url.URL, *time.Time, error) {
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
		username,
	)
}

func (s *ControllerServer) Listen(_ *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()

	aud, exp, err := s.audience(ctx, "system:serviceaccount:default:jumpstarter-exporter")
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

func (s *ControllerServer) streamToken(
	ctx context.Context,
	sub string,
	stream string,
	peer string,
	exp time.Time,
) (string, error) {
	return authn.Issue(
		ctx,
		s.clientset.CoreV1().ServiceAccounts(metav1.NamespaceDefault),
		"jumpstarter-router",
		"jumpstarter-router.example.com",
		map[string]string{"sub": sub, "stream": stream, "peer": peer},
		exp,
	)
}

func (s *ControllerServer) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	aud, exp, err := s.audience(ctx, "system:serviceaccount:default:jumpstarter-client")
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

	etoken, err := s.streamToken(
		ctx,
		req.GetUuid(),
		stream,
		aud.String(),
		*exp,
	)
	if err != nil {
		return nil, err
	}

	ctoken, err := s.streamToken(
		ctx,
		aud.String(),
		stream,
		req.GetUuid(),
		*exp,
	)
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
