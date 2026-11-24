package service

import (
	"errors"
	"io"

	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc/status"
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

// Forward relays a and b until both directions end cleanly, or either peer
// goes away. It does not wait for a pipe blocked in Recv: a server stream's
// Recv only unblocks when its peer sends, or when its handler returns, so
// waiting here would keep the surviving peer connected indefinitely.
func Forward(a pb.RouterService_StreamServer, b pb.RouterService_StreamServer) error {
	errs := make(chan error, 2)
	go func() { errs <- pipe(a, b) }()
	go func() { errs <- pipe(b, a) }()
	// Each pipe copies one direction and reports once. A nil means that
	// direction half-closed cleanly, so keep relaying the other; when both
	// are nil the stream is done.
	for range 2 {
		select {
		case err := <-errs:
			if err != nil {
				return err
			}
		case <-a.Context().Done():
			return status.FromContextError(a.Context().Err()).Err()
		case <-b.Context().Done():
			return status.FromContextError(b.Context().Err()).Err()
		}
	}
	return nil
}
