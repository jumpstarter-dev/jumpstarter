package stream

import (
	"context"
	"errors"
	"io"

	"golang.org/x/sync/errgroup"
)

type Stream[T any] interface {
	Send(T) error
	Recv() (T, error)
}

func pipe[T any, A Stream[T], B Stream[T]](a A, b B) error {
	for {
		msg, err := a.Recv()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
		err = b.Send(msg)
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
	}
}

func Forward[T any, A Stream[T], B Stream[T]](ctx context.Context, a A, b B) error {
	g, _ := errgroup.WithContext(ctx)
	g.Go(func() error { return pipe(a, b) })
	g.Go(func() error { return pipe(b, a) })
	return g.Wait()
}
