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
	"crypto/tls"
	"encoding/pem"
	"errors"
	"fmt"
	"net"
	"os"
	"strings"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/log"
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
// TLS: the server always uses TLS. When EXTERNAL_CERT_PEM and EXTERNAL_KEY_PEM
// env vars point to certificate/key files (mounted by the operator from a Secret),
// those are loaded. Otherwise a self-signed certificate is generated — traffic is
// still encrypted, but clients cannot verify the server identity without the CA cert
// in the ConfigMap telemetry.certificate field.
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
	if claimedNamespace == "" || claimedName == "" {
		return nil, status.Errorf(codes.PermissionDenied, "token has incomplete exporter identity")
	}

	// Use context-based logger so tests can inject their own via logf.IntoContext.
	logger := log.FromContext(ctx).WithName("telemetry")

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

		// Always log the authenticated identity. After the mismatch checks
		// above, any non-empty entry fields already match the token; using
		// the token values makes the server the source of truth for Loki
		// stream labels even when the entry omitted them.
		kvs := []any{
			"component", entry.Component,
			"exporter", claimedName,
			"namespace", claimedNamespace,
			"severity", entry.Severity,
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

// loadTLSCredentials loads TLS credentials for the gRPC server.
// It reads EXTERNAL_CERT_PEM and EXTERNAL_KEY_PEM env vars (file paths set by the
// operator via Secret volume mounts). When either is absent it falls back to a
// self-signed certificate so that traffic is always encrypted.
//
// selfSignedPEM is non-empty only when a self-signed certificate was generated.
// Callers should log it so the operator can copy it into the ConfigMap's
// telemetry.certificate field — exporters need this PEM to verify the TLS connection.
func (s *TelemetryService) loadTLSCredentials() (creds credentials.TransportCredentials, selfSignedPEM string, err error) {
	certPEMPath := os.Getenv("EXTERNAL_CERT_PEM")
	keyPEMPath := os.Getenv("EXTERNAL_KEY_PEM")

	var cert *tls.Certificate
	if certPEMPath != "" && keyPEMPath != "" {
		certPEMBytes, readErr := os.ReadFile(certPEMPath)
		if readErr != nil {
			return nil, "", fmt.Errorf("failed to read external certificate file: %w", readErr)
		}
		keyPEMBytes, readErr := os.ReadFile(keyPEMPath)
		if readErr != nil {
			return nil, "", fmt.Errorf("failed to read external key file: %w", readErr)
		}
		parsedCert, parseErr := tls.X509KeyPair(certPEMBytes, keyPEMBytes)
		if parseErr != nil {
			return nil, "", fmt.Errorf("failed to parse external certificate: %w", parseErr)
		}
		cert = &parsedCert
	} else {
		// Derive the TLS SAN from the advertised endpoint (what clients connect to),
		// not from the bind address (which is a local port like ":9093").
		// Same pattern as the router and controller services.
		// IMPORTANT: GRPC_TELEMETRY_ENDPOINT must be set on the telemetry pod itself
		// so the SAN matches the endpoint the controller advertises to exporters.
		advertised := telemetryEndpoint()
		var dnsnames []string
		var ipaddresses []net.IP
		if advertised != "" {
			var sanErr error
			dnsnames, ipaddresses, sanErr = endpointToSAN(advertised)
			if sanErr != nil {
				dnsnames = []string{"localhost"}
			}
		} else {
			// No advertised endpoint configured — development/local mode.
			dnsnames = []string{"localhost"}
		}
		var genErr error
		cert, genErr = NewSelfSignedCertificate("jumpstarter telemetry", dnsnames, ipaddresses)
		if genErr != nil {
			return nil, "", genErr
		}
		// Encode the leaf cert as PEM so the caller can log it for the operator.
		selfSignedPEM = string(pem.EncodeToMemory(&pem.Block{
			Type:  "CERTIFICATE",
			Bytes: cert.Certificate[0],
		}))
	}
	return credentials.NewServerTLSFromCert(cert), selfSignedPEM, nil
}

// Start starts the TelemetryService gRPC server and blocks until ctx is cancelled.
func (s *TelemetryService) Start(ctx context.Context) error {
	logger := ctrl.Log.WithName("telemetry").WithValues("component", "telemetry")

	creds, selfSignedPEM, err := s.loadTLSCredentials()
	if err != nil {
		return fmt.Errorf("telemetry: load TLS credentials: %w", err)
	}
	if selfSignedPEM != "" {
		// Log the self-signed cert so the operator can copy it into the controller
		// ConfigMap's telemetry.certificate field. Exporters need this PEM to verify
		// the TLS connection — a self-signed cert is not trusted by the system CA pool.
		logger.Info("Using self-signed TLS certificate; copy certPEM into the controller ConfigMap telemetry.certificate so exporters can verify TLS",
			"certPEM", selfSignedPEM)
	}

	lis, err := net.Listen("tcp", s.BindAddr)
	if err != nil {
		return fmt.Errorf("telemetry: listen %s: %w", s.BindAddr, err)
	}

	srv := grpc.NewServer(grpc.Creds(creds))
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
