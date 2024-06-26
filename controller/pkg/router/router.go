package router

import (
	"context"
	"log"
	"sync"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-router/pkg/authn"
	authnv1c "k8s.io/client-go/kubernetes/typed/authentication/v1"
)

type RouterServer struct {
	pb.UnimplementedRouterServiceServer
	pending *sync.Map
	authn   authnv1c.AuthenticationV1Interface
}

type streamCtx struct {
	cancel context.CancelFunc
	stream pb.RouterService_StreamServer
}

func NewRouterServer(authn authnv1c.AuthenticationV1Interface) (*RouterServer, error) {
	return &RouterServer{
		pending: &sync.Map{},
		authn:   authn,
	}, nil
}

func (s *RouterServer) Stream(stream pb.RouterService_StreamServer) error {
	ctx := stream.Context()

	token, err := authn.BearerTokenFromContext(ctx)
	if err != nil {
		return err
	}

	aud, exp, err := authn.Authenticate(
		ctx,
		s.authn,
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

	sctx := streamCtx{
		cancel: cancel,
		stream: stream,
	}

	actual, loaded := s.pending.LoadOrStore(streamId, sctx)
	if loaded {
		log.Printf("subject %s connected to peer %s on stream %s\n", subject, peer, streamId)
		defer actual.(streamCtx).cancel()
		return forward(ctx, stream, actual.(streamCtx).stream)
	} else {
		log.Printf("subject %s waiting for peer %s on stream %s\n", subject, peer, streamId)
		select {
		case <-ctx.Done():
			return nil
		}
	}
}
