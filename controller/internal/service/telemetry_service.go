/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package service

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strings"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"
	ctrl "sigs.k8s.io/controller-runtime"
)

// JEP-0013 limits for extra_fields, mirroring the client-side limits.
const (
	maxExtraFields = 16
	maxKeyLen      = 64
	maxValueLen    = 256
)

const maxEntriesPerBatch = 500

// reservedExtraFieldKeys are the top-level log fields that the server owns.
// Allowing these through extra_fields would let an exporter shadow trusted
// values (e.g. inject a fake "exporter" key) in downstream log parsers.
var reservedExtraFieldKeys = map[string]struct{}{
	"component": {}, "exporter": {}, "severity": {}, "namespace": {},
	"ts": {}, "lease": {}, "client": {}, "operation": {}, "result": {},
	"driver_type": {},
}

// TelemetryService receives structured log entries from exporters and clients,
// logs them via structured stdout, and will forward them to Loki in a future phase.
//
// Phase 1 design: the server listens on plaintext gRPC only. TLS termination is
// expected to be handled by a sidecar (e.g. Envoy) or service mesh in production
// deployments. The Certificate field advertised via GetServiceEndpoints is reserved
// for a future phase where the telemetry binary manages its own TLS credentials.
// Do NOT configure the Certificate field in the ConfigMap for Phase 1 deployments —
// exporters that receive a certificate will attempt a TLS handshake that will fail.
type TelemetryService struct {
	pb.UnimplementedTelemetryServiceServer

	// BindAddr is the TCP address to listen on (e.g. ":9093").
	BindAddr string

	// Signer is used to validate bearer tokens on every PushLogs call.
	// Tokens are issued by the controller from the same CONTROLLER_KEY seed,
	// so the telemetry binary can verify them locally without a k8s client.
	Signer *oidc.Signer
}

// PushLogs receives a batch of structured log entries and writes them via the
// controller-runtime logger (structured JSON to stdout).
// Future phase: forward to Loki push API.
func (s *TelemetryService) PushLogs(ctx context.Context, req *pb.PushLogsRequest) (*pb.PushLogsResponse, error) {
	token, err := authentication.BearerTokenFromContext(ctx)
	if err != nil {
		return nil, err
	}

	// Validate token and extract the subject (format: exporter:namespace:name:uid).
	subject, err := s.Signer.ParseSubject(token)
	if err != nil {
		return nil, status.Errorf(codes.Unauthenticated, "invalid token: %v", err)
	}

	// Only exporter tokens are allowed to push logs. Any other validly-signed
	// token (e.g. a client token) is rejected immediately so that the identity
	// checks below always have a non-empty claimedName/claimedNamespace.
	parts := strings.SplitN(subject, ":", 4)
	if len(parts) != 4 || parts[0] != "exporter" {
		return nil, status.Errorf(codes.PermissionDenied, "token is not an exporter token")
	}
	claimedNamespace := parts[1]
	claimedName := parts[2]

	// Use a plain logger — the entry's own component field carries the source identity.
	logger := ctrl.Log.WithName("telemetry")

	entries := req.Entries
	var dropped uint32
	if len(entries) > maxEntriesPerBatch {
		dropped = uint32(len(entries) - maxEntriesPerBatch)
		entries = entries[:maxEntriesPerBatch]
	}

	var accepted uint32
	for _, entry := range entries {
		// Drop entries that claim to be from a different exporter or namespace
		// than what the token authorises. Counted as dropped rather than failing
		// the whole batch so valid entries in the same request are still written.
		if entry.Exporter != "" && entry.Exporter != claimedName {
			dropped++
			continue
		}
		if entry.Namespace != "" && entry.Namespace != claimedNamespace {
			dropped++
			continue
		}

		kvs := []any{
			"component", entry.Component,
			"exporter", entry.Exporter,
			"severity", entry.Severity,
		}
		if entry.Namespace != "" {
			kvs = append(kvs, "namespace", entry.Namespace)
		}
		if entry.Timestamp != nil {
			kvs = append(kvs, "ts", entry.Timestamp.AsTime().Format(time.RFC3339Nano))
		}
		if entry.Lease != "" {
			kvs = append(kvs, "lease", entry.Lease)
		}
		if entry.Client != "" {
			kvs = append(kvs, "client", entry.Client)
		}
		if entry.Operation != "" {
			kvs = append(kvs, "operation", entry.Operation)
		}
		if entry.Result != "" {
			kvs = append(kvs, "result", entry.Result)
		}
		if entry.DriverType != "" {
			kvs = append(kvs, "driver_type", entry.DriverType)
		}

		// Enforce extra_fields limits and strip reserved keys so an exporter
		// cannot shadow trusted fields in downstream log parsers.
		count := 0
		for k, v := range entry.ExtraFields {
			if count >= maxExtraFields {
				break
			}
			if _, reserved := reservedExtraFieldKeys[k]; reserved {
				continue
			}
			k = truncate(k, maxKeyLen)
			v = truncate(v, maxValueLen)
			kvs = append(kvs, k, v)
			count++
		}

		switch strings.ToLower(entry.Severity) {
		case "error", "critical":
			logger.Error(nil, entry.Message, kvs...)
		default:
			logger.Info(entry.Message, kvs...)
		}
		accepted++
	}

	return &pb.PushLogsResponse{
		Accepted: accepted,
		Dropped:  dropped,
	}, nil
}

// truncate returns s truncated to at most n bytes (rune-safe: truncates at rune boundary).
func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	// Walk runes to avoid cutting in the middle of a multi-byte character.
	b := 0
	for _, r := range s {
		next := b + len(string(r))
		if next > n {
			break
		}
		b = next
	}
	return s[:b]
}

// Start starts the TelemetryService gRPC server and blocks until ctx is cancelled.
func (s *TelemetryService) Start(ctx context.Context) error {
	logger := ctrl.Log.WithName("telemetry").WithValues("component", "telemetry")

	lis, err := net.Listen("tcp", s.BindAddr)
	if err != nil {
		return fmt.Errorf("telemetry: listen %s: %w", s.BindAddr, err)
	}

	srv := grpc.NewServer()
	pb.RegisterTelemetryServiceServer(srv, s)
	reflection.Register(srv)

	logger.Info("Telemetry service listening", "addr", s.BindAddr)

	errCh := make(chan error, 1)
	go func() {
		errCh <- srv.Serve(lis)
	}()

	select {
	case <-ctx.Done():
		srv.GracefulStop()
		if err := <-errCh; err != nil && !errors.Is(err, grpc.ErrServerStopped) {
			return err
		}
		return nil
	case err := <-errCh:
		return err
	}
}
