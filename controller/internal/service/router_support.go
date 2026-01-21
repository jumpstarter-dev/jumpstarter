package service

import (
	"context"
	"errors"
	"io"

	pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
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
			Payload:   msg.GetPayload(),
			FrameType: msg.GetFrameType(),
		})
		if err != nil {
			return err
		}
	}
}

func Forward(ctx context.Context, a pb.RouterService_StreamServer, b pb.RouterService_StreamServer) error {
	g, ctx := errgroup.WithContext(ctx)
	g.Go(func() error { return pipe(a, b) })
	g.Go(func() error { return pipe(b, a) })
	// In case both tasks return nil
	// Reference: https://pkg.go.dev/golang.org/x/sync/errgroup#WithContext
	// The derived Context is canceled the first time a function
	// passed to Go returns a non-nil error or the first time
	// Wait returns, whichever occurs first.
	go func() {
		_ = g.Wait()
	}()
	// Return on first error
	<-ctx.Done()
	return g.Wait()
}
