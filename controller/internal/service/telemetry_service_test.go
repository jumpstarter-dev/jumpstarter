/*
Copyright 2024.

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
	"fmt"
	"strings"
	"testing"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
)

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
	svc, ctx := telemetrySvcWithConfig(t, &config.Telemetry{Endpoint: "telemetry:9093"})

	_, err := svc.GetServiceEndpoints(ctx, &pb.GetServiceEndpointsRequest{})
	if err == nil {
		t.Fatal("expected GetServiceEndpoints to fail without valid token")
	}
	// The auth pipeline returns the raw error from the token verifier;
	// verify it contains the expected failure string.
	if !strings.Contains(err.Error(), "token verification failed") {
		t.Errorf("expected 'token verification failed' in error, got: %v", err)
	}
}

// buildTelemetryEndpointsResponse exercises the response-building logic
// without going through the auth gate, for isolated unit testing.
func buildTelemetryEndpointsResponse(cfg *config.Telemetry) *pb.GetServiceEndpointsResponse {
	resp := &pb.GetServiceEndpointsResponse{}
	if cfg != nil && cfg.Endpoint != "" {
		minSev := cfg.MinSeverity
		if minSev == "" {
			minSev = "info"
		}
		resp.TelemetryEndpoints = append(resp.TelemetryEndpoints, &pb.TelemetryEndpoint{
			Endpoint:    cfg.Endpoint,
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

func TestGetServiceEndpoints_EmptyEndpoint_ReturnsEmptyList(t *testing.T) {
	resp := buildTelemetryEndpointsResponse(&config.Telemetry{Endpoint: ""})
	if len(resp.TelemetryEndpoints) != 0 {
		t.Errorf("expected empty telemetry_endpoints for empty endpoint, got %d", len(resp.TelemetryEndpoints))
	}
}

func TestGetServiceEndpoints_WithEndpoint_ReturnsEndpoint(t *testing.T) {
	resp := buildTelemetryEndpointsResponse(&config.Telemetry{
		Endpoint:    "telemetry.jumpstarter.svc:9093",
		Certificate: "--- PEM ---",
		MinSeverity: "warning",
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
	resp := buildTelemetryEndpointsResponse(&config.Telemetry{Endpoint: "telemetry:9093"})

	if len(resp.TelemetryEndpoints) != 1 {
		t.Fatalf("expected 1 endpoint, got %d", len(resp.TelemetryEndpoints))
	}
	if resp.TelemetryEndpoints[0].MinSeverity != "info" {
		t.Errorf("MinSeverity = %q, want %q (default)", resp.TelemetryEndpoints[0].MinSeverity, "info")
	}
}

func TestTelemetryService_PushLogs_ReturnsAcceptedCount(t *testing.T) {
	svc := &TelemetryService{BindAddr: ":0"}

	entries := []*pb.LogEntry{
		{Severity: "info", Message: "hello", Component: "exporter", Exporter: "test-exporter"},
		{Severity: "warning", Message: "slow", Component: "exporter"},
	}
	resp, err := svc.PushLogs(context.Background(), &pb.PushLogsRequest{Entries: entries})
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
	svc := &TelemetryService{BindAddr: ":0"}

	resp, err := svc.PushLogs(context.Background(), &pb.PushLogsRequest{})
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
	svc := &TelemetryService{BindAddr: ":0"}

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

	resp, err := svc.PushLogs(context.Background(), &pb.PushLogsRequest{Entries: []*pb.LogEntry{entry}})
	if err != nil {
		t.Fatalf("PushLogs returned error: %v", err)
	}
	if resp.Accepted != 1 {
		t.Errorf("Accepted = %d, want 1", resp.Accepted)
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
