package mock

import (
	"context"
	"fmt"
	"io"
	"time"

	"golang.org/x/term"
)

type MockConsole struct {
	timeout time.Duration
	stdin   io.WriteCloser
	stdout  io.ReadCloser
}

type ContextReader struct {
	ctx context.Context
	r   io.Reader
}

func (r *ContextReader) Read(p []byte) (n int, err error) {
	type result struct {
		n   int
		err error
	}
	ch := make(chan result, 1)

	go func() {
		n, err := r.r.Read(p)
		ch <- result{n, err}
	}()

	select {
	case <-r.ctx.Done():
		return 0, nil
	case res := <-ch:
		return res.n, res.err
	}
}

type ReadWriter struct {
	io.Reader
	io.Writer
}

func newMockConsole() (*MockConsole, error) {
	ir, iw := io.Pipe()
	or, ow := io.Pipe()

	terminal := term.NewTerminal(ReadWriter{Reader: ir, Writer: ow}, "[mock@jumpstarter:~]$ ")

	go func() {
		for {
			line, err := terminal.ReadLine()
			if err != nil {
				break
			}
			if line == "ip a show dev eth0" {
				fmt.Fprintf(terminal,
					`1: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    link/ether 00:00:00:00:00:00 brd ff:ff:ff:ff:ff:ff
    inet 192.0.2.2/24 metric 2048 brd 192.0.2.255 scope global dynamic eth0
       valid_lft forever preferred_lft forever
`) // FIXME: check err
			} else {
				fmt.Fprintf(terminal, "Error: Not Implemented\n") // FIXME: check err
			}
		}
	}()

	return &MockConsole{timeout: time.Second, stdin: iw, stdout: or}, nil
}

func (c *MockConsole) Read(p []byte) (n int, err error) {
	ctx, cancel := context.WithTimeout(context.Background(), c.timeout)
	defer cancel()
	reader := ContextReader{ctx: ctx, r: c.stdout}
	return reader.Read(p)
}

func (c *MockConsole) Write(p []byte) (int, error) {
	return c.stdin.Write(p)
}

func (c *MockConsole) SetReadTimeout(t time.Duration) error {
	c.timeout = t
	return nil
}

func (c *MockConsole) Close() error {
	return nil
}
