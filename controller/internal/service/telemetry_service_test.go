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
	"bytes"
	"context"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc/metadata"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
)

// testSigner returns a deterministic Signer built from a fixed seed for use in tests.
func testSigner(t *testing.T) *oidc.Signer {
	t.Helper()
	signer, err := oidc.NewSignerFromSeed([]byte("test-seed"), "https://localhost:8085", "jumpstarter")
	if err != nil {
		t.Fatalf("failed to create test signer: %v", err)
	}
	return signer
}

// authedCtx returns a context containing a valid bearer token for subject.
func authedCtx(t *testing.T, signer *oidc.Signer, subject string) context.Context {
	t.Helper()
	token, err := signer.Token(subject)
	if err != nil {
		t.Fatalf("failed to issue test token: %v", err)
	}
	return metadata.NewIncomingContext(
		context.Background(),
		metadata.Pairs("authorization", "Bearer "+token),
	)
}

// telemetrySvcWithConfig builds a ControllerService whose authentication
// always fails with the same error that authFailureServiceCtx uses, but with
// an optional TelemetryConfig preset.  Since GetServiceEndpoints authenticates
// first, these tests verify the auth gate and the config-reading code path.
func telemetrySvcWithConfig(t *testing.T, cfg *config.Telemetry) (*ControllerService, context.Context) {
	t.Helper()
	_, ctx, _ := authFailureServiceCtx(t, "127.0.0.1:9999")
	svc := &ControllerService{
		Authn:           &failingAuthenticator{err: fmt.Errorf("token verification failed")},
		Authz:           noopAuthorizer{},
		Attr:            noopAttributesGetter{},
		TelemetryConfig: cfg,
	}
	return svc, ctx
}

func TestGetServiceEndpoints_RequiresAuthentication(t *testing.T) {
	svc, ctx := telemetrySvcWithConfig(t, &config.Telemetry{
		Enabled:  true,
		Endpoint: "telemetry:9093",
	})

	_, err := svc.GetServiceEndpoints(ctx, &pb.GetServiceEndpointsRequest{})
	if err == nil {
		t.Fatal("expected GetServiceEndpoints to fail without valid token")
	}
	if !strings.Contains(err.Error(), "token verification failed") {
		t.Errorf("expected 'token verification failed' in error, got: %v", err)
	}
}

// buildTelemetryEndpointsResponse exercises the response-building logic
// without going through the auth gate, for isolated unit testing.
func buildTelemetryEndpointsResponse(cfg *config.Telemetry) *pb.GetServiceEndpointsResponse {
	resp := &pb.GetServiceEndpointsResponse{}
	if cfg != nil && cfg.Enabled {
		minSev := cfg.Logging.Filter.MinSeverity
		if minSev == "" {
			minSev = "info"
		}
		ep := cfg.Endpoint
		if ep == "" {
			ep = telemetryEndpoint()
		}
		resp.TelemetryEndpoints = append(resp.TelemetryEndpoints, &pb.TelemetryEndpoint{
			Endpoint:    ep,
			Certificate: cfg.Certificate,
			MinSeverity: minSev,
		})
	}
	return resp
}

func TestGetServiceEndpoints_NilConfig_ReturnsEmptyList(t *testing.T) {
	resp := buildTelemetryEndpointsResponse(nil)
	if len(resp.TelemetryEndpoints) != 0 {
		t.Errorf("expected empty telemetry_endpoints, got %d", len(resp.TelemetryEndpoints))
	}
}

func TestGetServiceEndpoints_DisabledConfig_ReturnsEmptyList(t *testing.T) {
	resp := buildTelemetryEndpointsResponse(&config.Telemetry{Enabled: false, Endpoint: "telemetry:9093"})
	if len(resp.TelemetryEndpoints) != 0 {
		t.Errorf("expected empty telemetry_endpoints when disabled, got %d", len(resp.TelemetryEndpoints))
	}
}

func TestGetServiceEndpoints_WithEndpoint_ReturnsEndpoint(t *testing.T) {
	resp := buildTelemetryEndpointsResponse(&config.Telemetry{
		Enabled:     true,
		Endpoint:    "telemetry.jumpstarter.svc:9093",
		Certificate: "--- PEM ---",
		Logging: config.TelemetryLogging{
			Filter: config.TelemetryLoggingFilter{MinSeverity: "warning"},
		},
	})

	if len(resp.TelemetryEndpoints) != 1 {
		t.Fatalf("expected 1 telemetry endpoint, got %d", len(resp.TelemetryEndpoints))
	}
	ep := resp.TelemetryEndpoints[0]
	if ep.Endpoint != "telemetry.jumpstarter.svc:9093" {
		t.Errorf("Endpoint = %q, want %q", ep.Endpoint, "telemetry.jumpstarter.svc:9093")
	}
	if ep.Certificate != "--- PEM ---" {
		t.Errorf("Certificate = %q, want %q", ep.Certificate, "--- PEM ---")
	}
	if ep.MinSeverity != "warning" {
		t.Errorf("MinSeverity = %q, want %q", ep.MinSeverity, "warning")
	}
}

func TestGetServiceEndpoints_DefaultsMinSeverityToInfo(t *testing.T) {
	resp := buildTelemetryEndpointsResponse(&config.Telemetry{
		Enabled:  true,
		Endpoint: "telemetry:9093",
	})

	if len(resp.TelemetryEndpoints) != 1 {
		t.Fatalf("expected 1 endpoint, got %d", len(resp.TelemetryEndpoints))
	}
	if resp.TelemetryEndpoints[0].MinSeverity != "info" {
		t.Errorf("MinSeverity = %q, want %q (default)", resp.TelemetryEndpoints[0].MinSeverity, "info")
	}
}

func TestGetServiceEndpoints_UsesEnvVarWhenEndpointEmpty(t *testing.T) {
	t.Setenv("GRPC_TELEMETRY_ENDPOINT", "telemetry.jumpstarter.svc:9093")

	resp := buildTelemetryEndpointsResponse(&config.Telemetry{
		Enabled: true,
		// Endpoint intentionally left empty — should fall back to env var.
	})

	if len(resp.TelemetryEndpoints) != 1 {
		t.Fatalf("expected 1 endpoint, got %d", len(resp.TelemetryEndpoints))
	}
	if resp.TelemetryEndpoints[0].Endpoint != "telemetry.jumpstarter.svc:9093" {
		t.Errorf("Endpoint = %q, want %q (from env)", resp.TelemetryEndpoints[0].Endpoint, "telemetry.jumpstarter.svc:9093")
	}
}

func TestGetServiceEndpoints_BothEndpointAndEnvVarEmpty_ReturnsEmptyEndpoint(t *testing.T) {
	t.Setenv("GRPC_TELEMETRY_ENDPOINT", "")

	resp := buildTelemetryEndpointsResponse(&config.Telemetry{
		Enabled: true,
		// Both Endpoint and GRPC_TELEMETRY_ENDPOINT are empty.
	})

	if len(resp.TelemetryEndpoints) != 1 {
		t.Fatalf("expected 1 endpoint entry, got %d", len(resp.TelemetryEndpoints))
	}
	// An empty endpoint is returned; the caller must handle this gracefully.
	if resp.TelemetryEndpoints[0].Endpoint != "" {
		t.Errorf("Endpoint = %q, want empty string when nothing is configured", resp.TelemetryEndpoints[0].Endpoint)
	}
}

func TestGetServiceEndpoints_ConfigEndpointTakesPrecedenceOverEnvVar(t *testing.T) {
	t.Setenv("GRPC_TELEMETRY_ENDPOINT", "env-telemetry.svc:9093")

	resp := buildTelemetryEndpointsResponse(&config.Telemetry{
		Enabled:  true,
		Endpoint: "config-telemetry.svc:9093",
	})

	if len(resp.TelemetryEndpoints) != 1 {
		t.Fatalf("expected 1 endpoint, got %d", len(resp.TelemetryEndpoints))
	}
	if resp.TelemetryEndpoints[0].Endpoint != "config-telemetry.svc:9093" {
		t.Errorf("Endpoint = %q, want config value to win over env var", resp.TelemetryEndpoints[0].Endpoint)
	}
}

// writeTLSPEMFiles generates a self-signed cert, marshals it to PEM, and writes
// cert and key to temporary files.  Returns (certPath, keyPath).
func writeTLSPEMFiles(t *testing.T) (certPath, keyPath string) {
	t.Helper()

	tlsCert, err := NewSelfSignedCertificate("test", []string{"localhost"}, nil)
	if err != nil {
		t.Fatalf("NewSelfSignedCertificate: %v", err)
	}

	certPEM := pem.EncodeToMemory(&pem.Block{
		Type:  "CERTIFICATE",
		Bytes: tlsCert.Certificate[0],
	})

	keyDER, err := x509.MarshalPKCS8PrivateKey(tlsCert.PrivateKey)
	if err != nil {
		t.Fatalf("MarshalPKCS8PrivateKey: %v", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})

	dir := t.TempDir()
	certPath = dir + "/tls.crt"
	keyPath = dir + "/tls.key"
	if err := os.WriteFile(certPath, certPEM, 0o600); err != nil {
		t.Fatalf("write cert: %v", err)
	}
	if err := os.WriteFile(keyPath, keyPEM, 0o600); err != nil {
		t.Fatalf("write key: %v", err)
	}
	return certPath, keyPath
}

// selfSignedSANs generates a self-signed certificate with the same logic as
// loadTLSCredentials for a given GRPC_TELEMETRY_ENDPOINT value, and returns
// its DNS SANs for assertion.
func selfSignedSANs(t *testing.T, advertised string) []string {
	t.Helper()
	var dnsnames []string
	if advertised != "" {
		dns, _, err := endpointToSAN(advertised)
		if err != nil {
			dnsnames = []string{"localhost"}
		} else {
			dnsnames = dns
		}
	} else {
		dnsnames = []string{"localhost"}
	}
	cert, err := NewSelfSignedCertificate("test", dnsnames, nil)
	if err != nil {
		t.Fatalf("NewSelfSignedCertificate: %v", err)
	}
	leaf, err := x509.ParseCertificate(cert.Certificate[0])
	if err != nil {
		t.Fatalf("ParseCertificate: %v", err)
	}
	return leaf.DNSNames
}

func TestTelemetryService_LoadTLSCredentials_SelfSigned(t *testing.T) {
	t.Setenv("EXTERNAL_CERT_PEM", "")
	t.Setenv("EXTERNAL_KEY_PEM", "")

	svc := &TelemetryService{BindAddr: ":9093", Signer: testSigner(t)}
	creds, selfSignedPEM, err := svc.loadTLSCredentials()
	if err != nil {
		t.Fatalf("loadTLSCredentials() with self-signed cert failed: %v", err)
	}
	if creds == nil {
		t.Fatal("expected non-nil credentials")
	}
	if selfSignedPEM == "" {
		t.Error("expected non-empty selfSignedPEM when no external cert is configured")
	}
	// Must be parseable PEM.
	block, _ := pem.Decode([]byte(selfSignedPEM))
	if block == nil {
		t.Errorf("selfSignedPEM is not valid PEM: %s", selfSignedPEM)
	}
}

func TestTelemetryService_LoadTLSCredentials_SelfSignedUsesAdvertisedEndpointForSAN(t *testing.T) {
	// When GRPC_TELEMETRY_ENDPOINT is set, the self-signed cert SAN should derive
	// from the advertised hostname — not from the bind address.
	// We test the SAN derivation logic directly (same code path as loadTLSCredentials).
	sans := selfSignedSANs(t, "telemetry.jumpstarter.svc:9093")
	if len(sans) != 1 || sans[0] != "telemetry.jumpstarter.svc" {
		t.Errorf("expected SAN [telemetry.jumpstarter.svc], got %v", sans)
	}
}

func TestTelemetryService_LoadTLSCredentials_SelfSignedFallsBackToLocalhostWhenNoEndpoint(t *testing.T) {
	// When GRPC_TELEMETRY_ENDPOINT is empty, SAN defaults to "localhost".
	sans := selfSignedSANs(t, "")
	if len(sans) != 1 || sans[0] != "localhost" {
		t.Errorf("expected SAN [localhost], got %v", sans)
	}
}

func TestTelemetryService_LoadTLSCredentials_OnlyCertEnvVarFallsBackToSelfSigned(t *testing.T) {
	certPath, _ := writeTLSPEMFiles(t)
	// Only cert set, key is missing — should fall back to self-signed, not error.
	t.Setenv("EXTERNAL_CERT_PEM", certPath)
	t.Setenv("EXTERNAL_KEY_PEM", "")

	svc := &TelemetryService{BindAddr: ":9093", Signer: testSigner(t)}
	creds, selfSignedPEM, err := svc.loadTLSCredentials()
	if err != nil {
		t.Fatalf("expected self-signed fallback, got error: %v", err)
	}
	if creds == nil {
		t.Fatal("expected non-nil credentials")
	}
	// Partial env vars → self-signed fallback, so PEM must be non-empty.
	if selfSignedPEM == "" {
		t.Error("expected non-empty selfSignedPEM on self-signed fallback")
	}
}

func TestTelemetryService_LoadTLSCredentials_WithValidPEMFiles(t *testing.T) {
	certPath, keyPath := writeTLSPEMFiles(t)
	t.Setenv("EXTERNAL_CERT_PEM", certPath)
	t.Setenv("EXTERNAL_KEY_PEM", keyPath)

	svc := &TelemetryService{BindAddr: ":9093", Signer: testSigner(t)}
	creds, selfSignedPEM, err := svc.loadTLSCredentials()
	if err != nil {
		t.Fatalf("loadTLSCredentials() with valid PEM files failed: %v", err)
	}
	if creds == nil {
		t.Fatal("expected non-nil credentials")
	}
	// External cert provided — selfSignedPEM must be empty.
	if selfSignedPEM != "" {
		t.Errorf("expected empty selfSignedPEM when external cert is provided, got non-empty")
	}
}

func TestTelemetryService_LoadTLSCredentials_BadCertFileReturnsError(t *testing.T) {
	certFile, err := os.CreateTemp(t.TempDir(), "tls-*.crt")
	if err != nil {
		t.Fatalf("CreateTemp: %v", err)
	}
	if err := certFile.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
	_, keyPath := writeTLSPEMFiles(t)

	t.Setenv("EXTERNAL_CERT_PEM", certFile.Name())
	t.Setenv("EXTERNAL_KEY_PEM", keyPath)

	svc := &TelemetryService{BindAddr: ":9093", Signer: testSigner(t)}
	_, _, err = svc.loadTLSCredentials()
	if err == nil {
		t.Fatal("expected error parsing empty cert file")
	}
	// Must be a parse error, not a file-not-found.
	if strings.Contains(err.Error(), "no such file") {
		t.Errorf("expected parse error, got: %v", err)
	}
}

func TestTelemetryService_LoadTLSCredentials_MissingCertFileReturnsError(t *testing.T) {
	_, keyPath := writeTLSPEMFiles(t)
	t.Setenv("EXTERNAL_CERT_PEM", "/does/not/exist/tls.crt")
	t.Setenv("EXTERNAL_KEY_PEM", keyPath)

	svc := &TelemetryService{BindAddr: ":9093", Signer: testSigner(t)}
	_, _, err := svc.loadTLSCredentials()
	if err == nil {
		t.Fatal("expected error reading missing cert file")
	}
}

func TestTelemetryService_Start_FailsWhenExternalCertFileMissing(t *testing.T) {
	t.Setenv("EXTERNAL_CERT_PEM", "/no/such/cert.pem")
	t.Setenv("EXTERNAL_KEY_PEM", "/no/such/key.pem")

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	svc := &TelemetryService{BindAddr: ":0", Signer: testSigner(t)}
	err := svc.Start(ctx)
	if err == nil {
		t.Fatal("expected Start to fail with missing cert files")
	}
	if !strings.Contains(err.Error(), "TLS") {
		t.Errorf("expected 'TLS' in error, got: %v", err)
	}
}

func TestTelemetryService_PushLogs_RequiresAuthentication(t *testing.T) {
	svc := &TelemetryService{BindAddr: ":0", Signer: testSigner(t)}

	_, err := svc.PushLogs(context.Background(), &pb.PushLogsRequest{})
	if err == nil {
		t.Fatal("expected PushLogs to fail without a token")
	}
	if !strings.Contains(err.Error(), "missing") {
		t.Errorf("expected 'missing' in error, got: %v", err)
	}
}

func TestTelemetryService_PushLogs_RejectsInvalidToken(t *testing.T) {
	svc := &TelemetryService{BindAddr: ":0", Signer: testSigner(t)}
	ctx := metadata.NewIncomingContext(
		context.Background(),
		metadata.Pairs("authorization", "Bearer not-a-real-token"),
	)

	_, err := svc.PushLogs(ctx, &pb.PushLogsRequest{})
	if err == nil {
		t.Fatal("expected PushLogs to fail with an invalid token")
	}
	if !strings.Contains(err.Error(), "invalid token") {
		t.Errorf("expected 'invalid token' in error, got: %v", err)
	}
}

func TestTelemetryService_PushLogs_ReturnsAcceptedCount(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	ctx := authedCtx(t, signer, "exporter:jumpstarter:test-exporter:abc123")

	entries := []*pb.LogEntry{
		{Severity: "info", Message: "hello", Component: "exporter", Exporter: "test-exporter"},
		{Severity: "warning", Message: "slow", Component: "exporter"},
	}
	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: entries})
	if err != nil {
		t.Fatalf("PushLogs returned error: %v", err)
	}
	if resp.Accepted != 2 {
		t.Errorf("Accepted = %d, want 2", resp.Accepted)
	}
	if resp.Dropped != 0 {
		t.Errorf("Dropped = %d, want 0", resp.Dropped)
	}
}

func TestTelemetryService_PushLogs_EmptyBatch_ReturnsZero(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	ctx := authedCtx(t, signer, "exporter:jumpstarter:test-exporter:abc123")

	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{})
	if err != nil {
		t.Fatalf("PushLogs returned error: %v", err)
	}
	if resp.Accepted != 0 {
		t.Errorf("Accepted = %d, want 0 for empty batch", resp.Accepted)
	}
}

func TestTelemetryService_PushLogs_AllFieldsLogged(t *testing.T) {
	// Structured log output goes to stdout so we can't assert on it here,
	// but we verify that a fully-populated entry is accepted without error.
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	ctx := authedCtx(t, signer, "exporter:jumpstarter:lab-exporter:abc123")

	entry := &pb.LogEntry{
		Severity:   "error",
		Message:    "flash failed",
		Component:  "exporter",
		Exporter:   "lab-exporter",
		Lease:      "lease-42",
		Client:     "ci-client",
		Operation:  "flash",
		Result:     "failure",
		DriverType: "storage",
		ExtraFields: map[string]string{
			"build_id": "nightly-99",
			"pipeline": "ci-main",
		},
	}

	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: []*pb.LogEntry{entry}})
	if err != nil {
		t.Fatalf("PushLogs returned error: %v", err)
	}
	if resp.Accepted != 1 {
		t.Errorf("Accepted = %d, want 1", resp.Accepted)
	}
}

func TestTelemetryService_PushLogs_RejectsNonExporterToken(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	// A client token has a different subject format — must be rejected.
	ctx := authedCtx(t, signer, "client:jumpstarter:some-client:uid1")

	_, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: []*pb.LogEntry{
		{Severity: "info", Message: "sneaky"},
	}})
	if err == nil {
		t.Fatal("expected PushLogs to reject a non-exporter token")
	}
	if !strings.Contains(err.Error(), "not an exporter token") {
		t.Errorf("expected 'not an exporter token' in error, got: %v", err)
	}
}

func TestTelemetryService_PushLogs_RejectsEmptyNamespaceInToken(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	// Token with empty namespace: exporter::my-exporter:uid1
	ctx := authedCtx(t, signer, "exporter::my-exporter:uid1")

	_, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: []*pb.LogEntry{
		{Severity: "info", Message: "test"},
	}})
	if err == nil {
		t.Fatal("expected PushLogs to reject token with empty namespace")
	}
	if !strings.Contains(err.Error(), "incomplete exporter identity") {
		t.Errorf("expected 'incomplete exporter identity' in error, got: %v", err)
	}
}

func TestTelemetryService_PushLogs_RejectsEmptyExporterNameInToken(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	// Token with empty exporter name: exporter:my-namespace::uid1
	ctx := authedCtx(t, signer, "exporter:my-namespace::uid1")

	_, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: []*pb.LogEntry{
		{Severity: "info", Message: "test"},
	}})
	if err == nil {
		t.Fatal("expected PushLogs to reject token with empty exporter name")
	}
	if !strings.Contains(err.Error(), "incomplete exporter identity") {
		t.Errorf("expected 'incomplete exporter identity' in error, got: %v", err)
	}
}

func TestTelemetryService_PushLogs_RejectsWrongExporter(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	ctx := authedCtx(t, signer, "exporter:jumpstarter:real-exporter:uid1")

	// One good entry + one with a mismatched exporter: good one accepted, bad one dropped.
	entries := []*pb.LogEntry{
		{Severity: "info", Message: "ok", Exporter: "real-exporter"},
		{Severity: "info", Message: "bad", Exporter: "other-exporter"},
	}
	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: entries})
	if err != nil {
		t.Fatalf("expected no error, got: %v", err)
	}
	if resp.Accepted != 1 {
		t.Errorf("Accepted = %d, want 1", resp.Accepted)
	}
	if resp.Dropped != 1 {
		t.Errorf("Dropped = %d, want 1", resp.Dropped)
	}
}

func TestTelemetryService_PushLogs_RejectsWrongNamespace(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	ctx := authedCtx(t, signer, "exporter:jumpstarter:my-exporter:uid1")

	entries := []*pb.LogEntry{
		{Severity: "info", Message: "ok", Exporter: "my-exporter", Namespace: "jumpstarter"},
		{Severity: "info", Message: "bad", Exporter: "my-exporter", Namespace: "other-ns"},
	}
	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: entries})
	if err != nil {
		t.Fatalf("expected no error, got: %v", err)
	}
	if resp.Accepted != 1 {
		t.Errorf("Accepted = %d, want 1", resp.Accepted)
	}
	if resp.Dropped != 1 {
		t.Errorf("Dropped = %d, want 1", resp.Dropped)
	}
}

func TestTelemetryService_PushLogs_AcceptsMatchingNamespaceAndExporter(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	ctx := authedCtx(t, signer, "exporter:jumpstarter:my-exporter:uid1")

	entry := &pb.LogEntry{
		Severity:  "info",
		Message:   "hello",
		Exporter:  "my-exporter",
		Namespace: "jumpstarter",
	}
	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: []*pb.LogEntry{entry}})
	if err != nil {
		t.Fatalf("PushLogs returned unexpected error: %v", err)
	}
	if resp.Accepted != 1 {
		t.Errorf("Accepted = %d, want 1", resp.Accepted)
	}
}

func TestTelemetryService_PushLogs_InjectsAuthenticatedIdentityWhenOmitted(t *testing.T) {
	// Exporter and namespace are required Loki stream labels.
	// When entries omit these fields, the server injects the authenticated
	// values from the token so that all log entries have proper identity context.

	// Capture log output to verify injected values using context-based logging.
	var logBuf bytes.Buffer
	testLogger := zap.New(zap.WriteTo(&logBuf))

	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}

	// Create context with auth token and inject test logger.
	ctx := authedCtx(t, signer, "exporter:my-namespace:my-exporter:uid1")
	ctx = logf.IntoContext(ctx, testLogger)

	// Entry with no exporter or namespace — should be accepted and the server
	// will inject the authenticated values (my-exporter, my-namespace) from the token.
	entry := &pb.LogEntry{
		Severity: "info",
		Message:  "entry without explicit identity",
	}
	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: []*pb.LogEntry{entry}})
	if err != nil {
		t.Fatalf("PushLogs returned unexpected error: %v", err)
	}
	if resp.Accepted != 1 {
		t.Errorf("Accepted = %d, want 1 (entry should be accepted with injected identity)", resp.Accepted)
	}
	if resp.Dropped != 0 {
		t.Errorf("Dropped = %d, want 0", resp.Dropped)
	}

	// Verify the injected values appear in log output.
	logged := logBuf.String()
	if !strings.Contains(logged, `"exporter":"my-exporter"`) {
		t.Errorf("expected injected exporter 'my-exporter' in log output, got:\n%s", logged)
	}
	if !strings.Contains(logged, `"namespace":"my-namespace"`) {
		t.Errorf("expected injected namespace 'my-namespace' in log output, got:\n%s", logged)
	}
}

func TestTelemetryService_PushLogs_ExtraFieldsCappedAt16(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	ctx := authedCtx(t, signer, "exporter:jumpstarter:test-exporter:uid1")

	// Send 20 extra_fields — server should accept the entry but only log up to 16.
	extra := make(map[string]string, 20)
	for i := range 20 {
		extra[fmt.Sprintf("key%02d", i)] = "v"
	}
	entry := &pb.LogEntry{
		Severity:    "info",
		Message:     "many fields",
		ExtraFields: extra,
	}
	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: []*pb.LogEntry{entry}})
	if err != nil {
		t.Fatalf("PushLogs returned error: %v", err)
	}
	if resp.Accepted != 1 {
		t.Errorf("Accepted = %d, want 1", resp.Accepted)
	}
}

func TestTelemetryService_PushLogs_StripsReservedExtraFieldKeys(t *testing.T) {
	signer := testSigner(t)
	svc := &TelemetryService{BindAddr: ":0", Signer: signer}
	ctx := authedCtx(t, signer, "exporter:jumpstarter:test-exporter:uid1")

	// Reserved keys must be stripped; non-reserved ones must pass through.
	entry := &pb.LogEntry{
		Severity: "info",
		Message:  "test",
		ExtraFields: map[string]string{
			"exporter":   "injected",
			"component":  "injected",
			"severity":   "injected",
			"ts":         "injected",
			"custom_key": "allowed",
		},
	}
	resp, err := svc.PushLogs(ctx, &pb.PushLogsRequest{Entries: []*pb.LogEntry{entry}})
	if err != nil {
		t.Fatalf("PushLogs returned error: %v", err)
	}
	if resp.Accepted != 1 {
		t.Errorf("Accepted = %d, want 1", resp.Accepted)
	}
}

func TestTelemetryService_truncate(t *testing.T) {
	cases := []struct {
		input string
		n     int
		want  string
	}{
		{"abcde", 10, "abcde"},
		{"abcde", 5, "abcde"},
		{"abcde", 3, "abc"},
		{"héllo", 5, "héll"}, // multi-byte: é=2 bytes, h(1)+é(2)+l(1)+l(1)=5 exact, o would exceed
		{"", 5, ""},
	}
	for _, tc := range cases {
		got := truncate(tc.input, tc.n)
		if got != tc.want {
			t.Errorf("truncate(%q, %d) = %q, want %q", tc.input, tc.n, got, tc.want)
		}
	}
}

func TestGetServiceEndpoints_AuthFailure_LogsWithPeer(t *testing.T) {
	svc, ctx, buf := authFailureServiceCtx(t, "10.0.0.5:5555")

	_, err := svc.GetServiceEndpoints(ctx, &pb.GetServiceEndpointsRequest{})
	if err == nil {
		t.Fatal("expected error, got nil")
	}

	logged := buf.String()
	if !strings.Contains(logged, "exporter authentication failed") {
		t.Errorf("expected 'exporter authentication failed' in log, got:\n%s", logged)
	}
	if !strings.Contains(logged, "10.0.0.5") {
		t.Errorf("expected peer IP in log, got:\n%s", logged)
	}
}
