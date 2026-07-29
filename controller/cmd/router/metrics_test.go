/*
Copyright 2026. The Jumpstarter Authors.

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

package main

import (
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestMetricsEndpointServesPrometheusText(t *testing.T) {
	addr, err := startMetricsServer("127.0.0.1:0")
	if err != nil {
		t.Fatalf("startMetricsServer: %v", err)
	}
	if addr == "" {
		t.Fatal("expected non-empty listen address")
	}

	client := &http.Client{Timeout: 2 * time.Second}
	var resp *http.Response
	var lastErr error
	for i := 0; i < 20; i++ {
		resp, lastErr = client.Get("http://" + addr + "/metrics")
		if lastErr == nil {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if lastErr != nil {
		t.Fatalf("GET /metrics: %v", lastErr)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	ct := resp.Header.Get("Content-Type")
	if !strings.Contains(ct, "text/plain") && !strings.Contains(ct, "openmetrics") {
		t.Fatalf("unexpected Content-Type %q", ct)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	text := string(body)
	// Default Go process metrics should appear; do not require undocumented jumpstarter_* series.
	if !strings.Contains(text, "go_") && !strings.Contains(text, "process_") && !strings.Contains(text, "promhttp_") {
		t.Fatalf("expected Prometheus exposition with go_/process_ metrics, got:\n%s", text)
	}
}

func TestMetricsServerDisabledWhenAddrZero(t *testing.T) {
	addr, err := startMetricsServer("0")
	if err != nil {
		t.Fatalf("startMetricsServer(0): %v", err)
	}
	if addr != "" {
		t.Fatalf("expected empty addr when disabled, got %q", addr)
	}
}
