package log

import (
	"context"
	"net"

	"google.golang.org/grpc/peer"
	ctrllog "sigs.k8s.io/controller-runtime/pkg/log"
)

// PeerAddr returns the remote IP address from the gRPC peer info stored in ctx,
// or "unknown" if unavailable. Port number and transport paths (e.g. Unix socket
// paths) are intentionally stripped to avoid leaking internal details.
func PeerAddr(ctx context.Context) string {
	p, ok := peer.FromContext(ctx)
	if !ok || p.Addr == nil {
		return "unknown"
	}
	host, _, err := net.SplitHostPort(p.Addr.String())
	if err != nil {
		return "unknown"
	}
	return host
}

// LogContext returns ctx with its logger enriched with the peer address under
// the "peer" key ("unknown" when no usable address is available, so all log
// lines share a uniform shape). The gRPC interceptors apply this to every RPC;
// it owns the peer enrichment, so loggers derived from ctx (including the auth
// package's failure logs) must not add "peer" again.
func LogContext(ctx context.Context) context.Context {
	return ctrllog.IntoContext(ctx, ctrllog.FromContext(ctx, "peer", PeerAddr(ctx)))
}
