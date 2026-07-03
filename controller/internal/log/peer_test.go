package log

import (
	"bytes"
	"context"
	"net"
	"strings"
	"testing"

	grpcpeer "google.golang.org/grpc/peer"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	ctrlzap "sigs.k8s.io/controller-runtime/pkg/log/zap"
)

// fakeAddr implements net.Addr for injecting peer addresses into context.
type fakeAddr struct {
	network string
	addr    string
}

func (a fakeAddr) Network() string { return a.network }
func (a fakeAddr) String() string  { return a.addr }

// ctxWithPeer creates a context with a gRPC peer carrying the given address.
func ctxWithPeer(addr string) context.Context {
	return grpcpeer.NewContext(context.Background(), &grpcpeer.Peer{
		Addr: fakeAddr{network: "tcp", addr: addr},
	})
}

// withCapturedLog creates a buffer-backed logr.Logger for use in tests and
// returns the buffer plus a context enriched with that logger. It does NOT
// mutate the global logf.Log, so tests are isolated from each other.
func withCapturedLog(t *testing.T) (*bytes.Buffer, context.Context) {
	t.Helper()
	var buf bytes.Buffer
	logger := ctrlzap.New(ctrlzap.UseDevMode(true), ctrlzap.WriteTo(&buf))
	ctx := logf.IntoContext(context.Background(), logger)
	return &buf, ctx
}

// peerAddrUnknown is the expected return value when PeerAddr cannot determine
// the remote IP (no peer, nil Addr, unparsable address, etc.).
const peerAddrUnknown = "unknown"

// ---------------------------------------------------------------------------
// PeerAddr tests
// ---------------------------------------------------------------------------

func TestPeerAddr(t *testing.T) {
	tests := []struct {
		name     string
		ctx      context.Context
		expected string
	}{
		{
			name:     "no peer in context returns unknown",
			ctx:      context.Background(),
			expected: peerAddrUnknown,
		},
		{
			name:     "peer with host:port returns host only",
			ctx:      ctxWithPeer("10.0.0.5:43210"),
			expected: "10.0.0.5",
		},
		{
			name:     "peer with IPv6 [host]:port returns host only",
			ctx:      ctxWithPeer("[::1]:8080"),
			expected: "::1",
		},
		{
			name:     "peer with bare address (no port) returns unknown",
			ctx:      ctxWithPeer("no-port-here"),
			expected: peerAddrUnknown,
		},
		{
			name:     "peer with empty address returns unknown",
			ctx:      ctxWithPeer(""),
			expected: peerAddrUnknown,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := PeerAddr(tc.ctx)
			if got != tc.expected {
				t.Errorf("PeerAddr() = %q, want %q", got, tc.expected)
			}
		})
	}
}

// TestPeerAddr_NilAddr covers the edge case of a peer with a nil Addr.
func TestPeerAddr_NilAddr(t *testing.T) {
	ctx := grpcpeer.NewContext(context.Background(), &grpcpeer.Peer{
		Addr: nil,
	})
	// PeerAddr must not panic when p.Addr is nil and should return "unknown".
	got := PeerAddr(ctx)
	if got != peerAddrUnknown {
		t.Errorf("PeerAddr with nil Addr = %q, want %q", got, peerAddrUnknown)
	}
}

// TestPeerAddr_UnixSocket covers peers connected over a unix socket.
func TestPeerAddr_UnixSocket(t *testing.T) {
	ctx := grpcpeer.NewContext(context.Background(), &grpcpeer.Peer{
		Addr: &net.UnixAddr{Name: "/var/run/jumpstarter.sock", Net: "unix"},
	})
	got := PeerAddr(ctx)
	// A Unix socket path should not be returned; it should return "unknown"
	// because SplitHostPort will fail on "/var/run/jumpstarter.sock".
	if got != peerAddrUnknown {
		t.Errorf("PeerAddr for unix socket = %q, want %q", got, peerAddrUnknown)
	}
}

// ---------------------------------------------------------------------------
// LogContext tests
// ---------------------------------------------------------------------------

func TestLogContext_WithPeer_EnrichesContextWithPeerIP(t *testing.T) {
	buf, ctx := withCapturedLog(t)

	ctx = grpcpeer.NewContext(ctx, &grpcpeer.Peer{
		Addr: fakeAddr{network: "tcp", addr: "10.20.30.40:9090"},
	})

	enriched := LogContext(ctx)

	// Use the enriched context's logger and emit a message.
	logf.FromContext(enriched).Info("test message")

	logged := buf.String()
	if !strings.Contains(logged, "10.20.30.40") {
		t.Errorf("expected peer IP '10.20.30.40' in log output, got:\n%s", logged)
	}
	// Port should be stripped.
	if strings.Contains(logged, "9090") {
		t.Errorf("expected port to be stripped from peer address, got:\n%s", logged)
	}
}

func TestLogContext_WithoutPeer_LogsUnknownPeer(t *testing.T) {
	buf, ctx := withCapturedLog(t)

	enriched := LogContext(ctx)

	logf.FromContext(enriched).Info("test message")

	logged := buf.String()
	// The peer key is always present; without peer info it falls back to
	// "unknown" so all log lines share a uniform shape.
	if !strings.Contains(logged, "peer") || !strings.Contains(logged, "unknown") {
		t.Errorf("expected 'peer' key with value 'unknown' without peer info, got:\n%s", logged)
	}
}

func TestLogContext_IPv6Peer_StripsPort(t *testing.T) {
	buf, ctx := withCapturedLog(t)

	ctx = grpcpeer.NewContext(ctx, &grpcpeer.Peer{
		Addr: fakeAddr{network: "tcp", addr: "[::1]:8082"},
	})

	enriched := LogContext(ctx)
	logf.FromContext(enriched).Info("ipv6 test")

	logged := buf.String()
	if !strings.Contains(logged, "::1") {
		t.Errorf("expected IPv6 address '::1' in log, got:\n%s", logged)
	}
}

func TestLogContext_NilAddr_LogsUnknownPeer(t *testing.T) {
	buf, ctx := withCapturedLog(t)

	ctx = grpcpeer.NewContext(ctx, &grpcpeer.Peer{Addr: nil})

	// Should not panic and should fall back to peer="unknown".
	enriched := LogContext(ctx)
	logf.FromContext(enriched).Info("nil addr test")

	logged := buf.String()
	if !strings.Contains(logged, "peer") || !strings.Contains(logged, "unknown") {
		t.Errorf("expected 'peer' key with value 'unknown' for nil Addr, got:\n%s", logged)
	}
}

func TestLogContext_UnixSocket_ReturnsUnknownPeer(t *testing.T) {
	buf, ctx := withCapturedLog(t)

	ctx = grpcpeer.NewContext(ctx, &grpcpeer.Peer{
		Addr: &net.UnixAddr{Name: "/var/run/test.sock", Net: "unix"},
	})

	enriched := LogContext(ctx)
	logf.FromContext(enriched).Info("unix socket test")

	logged := buf.String()
	// The peer key should be present but with value "unknown" because
	// SplitHostPort fails on unix paths.
	if !strings.Contains(logged, "unknown") {
		t.Errorf("expected 'unknown' peer for unix socket, got:\n%s", logged)
	}
	// The socket path itself should NOT appear.
	if strings.Contains(logged, "/var/run/test.sock") {
		t.Errorf("unix socket path should not leak into log, got:\n%s", logged)
	}
}
