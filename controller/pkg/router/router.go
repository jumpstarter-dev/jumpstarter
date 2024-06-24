package server

import (
	"context"
	"sync"

	"github.com/google/uuid"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	st "github.com/jumpstarter-dev/jumpstarter-router/pkg/stream"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type RendezvousServer struct {
	pb.UnimplementedRendezvousServiceServer
	listenMap *sync.Map
	streamMap *sync.Map
}

type listenCtx struct {
	cancel context.CancelFunc
	stream pb.RendezvousService_ListenServer
}

type streamCtx struct {
	cancel context.CancelFunc
	stream pb.RendezvousService_StreamServer
}

func NewRendezvousServer() *RendezvousServer {
	return &RendezvousServer{
		listenMap: &sync.Map{},
		streamMap: &sync.Map{},
	}
}

func (s *RendezvousServer) Listen(req *pb.ListenRequest, stream pb.RendezvousService_ListenServer) error {
	ctx, cancel := context.WithCancel(stream.Context())

	cond := listenCtx{
		cancel: cancel,
		stream: stream,
	}

	actual, loaded := s.listenMap.Swap(req.Address, cond)
	if loaded {
		actual.(listenCtx).cancel()
	}

	select {
	case <-ctx.Done():
		return nil
	}
}

func (s *RendezvousServer) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	value, ok := s.listenMap.Load(req.Address)
	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream := uuid.New().String()

	err := value.(listenCtx).stream.Send(&pb.ListenResponse{
		Stream: stream,
	})
	if err != nil {
		return nil, err
	}

	return &pb.DialResponse{
		Stream: stream,
	}, nil
}

func (s *RendezvousServer) Stream(stream pb.RendezvousService_StreamServer) error {
	ctx := stream.Context()

	// extract connection id from context
	md, loaded := metadata.FromIncomingContext(ctx)
	if !loaded {
		return status.Errorf(codes.InvalidArgument, "missing context")
	}

	id := md.Get("stream")
	if len(id) != 1 {
		return status.Errorf(codes.InvalidArgument, "missing stream id in context")
	}

	// create new context for stream
	ctx, cancel := context.WithCancel(ctx)

	cond := streamCtx{
		cancel: cancel,
		stream: stream,
	}

	// find stream with matching id
	actual, loaded := s.streamMap.LoadOrStore(id[0], cond)
	if loaded {
		defer actual.(streamCtx).cancel()
		return st.Forward(ctx, stream, actual.(streamCtx).stream)
	}

	select {
	case <-ctx.Done():
		return nil
	}
}
