package controller

import (
	"context"
	"log"
	"sync"
	"time"

	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/authn"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

type ControllerServer struct {
	pb.UnimplementedControllerServiceServer
	clientset *kubernetes.Clientset
	listenMap *sync.Map
}

type listenCtx struct {
	cancel context.CancelFunc
	stream pb.ControllerService_ListenServer
}

func RegisterControllerServer(server *grpc.Server, config *rest.Config) error {
	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		return err
	}

	controller := ControllerServer{
		clientset: clientset,
		listenMap: &sync.Map{},
	}

	pb.RegisterControllerServiceServer(server, &controller)

	return nil
}

func (s *ControllerServer) authenticate(ctx context.Context) (*authn.TokenParam, error) {
	token, err := authn.BearerTokenFromContext(ctx)
	if err != nil {
		return nil, err
	}

	return authn.Authenticate(
		ctx,
		s.clientset.AuthenticationV1(),
		token,
		"jumpstarter-controller.example.com",
	)
}

func (s *ControllerServer) Listen(_ *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()

	param, err := s.authenticate(ctx)
	if err != nil {
		return err
	}

	// TODO: periodically check for token revocation and revoke derived stream tokens

	ctx, cancel := context.WithDeadline(ctx, param.Expiration)
	defer cancel()

	lctx := listenCtx{
		cancel: cancel,
		stream: stream,
	}

	_, loaded := s.listenMap.LoadOrStore(param.Subject, lctx)

	if loaded {
		return status.Errorf(codes.AlreadyExists, "exporter is already listening")
	}

	log.Printf("subject %s listening\n", param.Subject)

	defer s.listenMap.Delete(param.Subject)

	select {
	case <-ctx.Done():
		log.Printf("subject %s left\n", param.Subject)
		return nil
	}
}

func (s *ControllerServer) streamToken(
	ctx context.Context,
	stream string,
	exp time.Time,
) (string, error) {
	return authn.Issue(
		ctx,
		s.clientset.CoreV1().ServiceAccounts(metav1.NamespaceDefault),
		"jumpstarter-router.example.com",
		authn.TokenParam{
			Subject:    stream,
			Expiration: exp,
		},
	)
}

func (s *ControllerServer) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	param, err := s.authenticate(ctx)
	if err != nil {
		return nil, err
	}

	// TODO: check (client, exporter) tuple against leases

	log.Printf("subject %s connecting to %s\n", param.Subject, req.GetUuid())

	value, ok := s.listenMap.Load(req.GetUuid())
	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream := uuid.New().String()

	token, err := s.streamToken(
		ctx,
		stream,
		param.Expiration,
	)
	if err != nil {
		return nil, err
	}

	// TODO: find best router from list
	endpoint := "unix:/tmp/jumpstarter-router.sock"

	// TODO: check listener matches subject
	err = value.(listenCtx).stream.Send(&pb.ListenResponse{
		RouterEndpoint: endpoint,
		RouterToken:    token,
	})
	if err != nil {
		return nil, err
	}

	return &pb.DialResponse{
		RouterEndpoint: endpoint,
		RouterToken:    token,
	}, nil
}
