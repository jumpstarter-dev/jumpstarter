package router

import (
	"context"
	"log"
	"sync"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/authn"
	"google.golang.org/grpc"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

type RouterServer struct {
	pb.UnimplementedRouterServiceServer
	pending   *sync.Map
	clientset *kubernetes.Clientset
}

type streamContext struct {
	cancel context.CancelFunc
	stream pb.RouterService_StreamServer
}

func RegisterRouterServer(server *grpc.Server) error {
	config, err := rest.InClusterConfig()
	if err != nil {
		return err
	}

	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		return err
	}

	router := RouterServer{
		pending:   &sync.Map{},
		clientset: clientset,
	}

	pb.RegisterRouterServiceServer(server, &router)

	return nil
}

func (s *RouterServer) Stream(stream pb.RouterService_StreamServer) error {
	ctx := stream.Context()

	token, err := authn.BearerTokenFromContext(ctx)
	if err != nil {
		return err
	}

	aud, exp, err := authn.Authenticate(
		ctx,
		s.clientset.AuthenticationV1(),
		token,
		"https",
		"jumpstarter-router.example.com",
		"jumpstarter-router",
	)
	if err != nil {
		return err
	}

	subject := aud.Query().Get("subject")
	peer := aud.Query().Get("peer")
	streamId := aud.Query().Get("stream")

	ctx, cancel := context.WithDeadline(ctx, *exp)
	defer cancel()

	// TODO: periodically check for token revocation and call cancel

	sctx := streamContext{
		cancel: cancel,
		stream: stream,
	}

	actual, loaded := s.pending.LoadOrStore(streamId, sctx)
	if loaded {
		log.Printf("subject %s connected to peer %s on stream %s\n", subject, peer, streamId)
		defer actual.(streamContext).cancel()
		return forward(ctx, stream, actual.(streamContext).stream)
	} else {
		log.Printf("subject %s waiting for peer %s on stream %s\n", subject, peer, streamId)
		select {
		case <-ctx.Done():
			return nil
		}
	}
}
