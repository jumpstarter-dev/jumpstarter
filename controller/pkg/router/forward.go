package router

import (
	"context"
	"errors"
	"io"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"golang.org/x/sync/errgroup"
)

func pipe(a pb.RouterService_StreamServer, b pb.RouterService_StreamServer) error {
	for {
		msg, err := a.Recv()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
		err = b.Send(&pb.StreamResponse{
			Payload: msg.GetPayload(),
		})
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
	}
}

func forward(ctx context.Context, a pb.RouterService_StreamServer, b pb.RouterService_StreamServer) error {
	g, _ := errgroup.WithContext(ctx)
	g.Go(func() error { return pipe(a, b) })
	g.Go(func() error { return pipe(b, a) })
	return g.Wait()
}
