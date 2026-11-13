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

package jumpstarter

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestPromtailLiveLogDelivery exercises the shared Promtail JSON pipeline and
// label-drop behavior with a real Promtail process and a real Loki. A static
// file source is used here; Kubernetes discovery/__path__ handling is covered
// by TestKubernetesShipperDeployment.
func TestPromtailLiveLogDelivery(t *testing.T) {
	promtailBin := testBinary(t, "PROMTAIL_BIN", "promtail",
		"set PROMTAIL_BIN or install promtail to run the live Promtail delivery test")
	loki := startLokiTestServer(t)
	t.Logf("starting Promtail %s", promtailValidatedVersion)

	workDir := t.TempDir()
	logPath := filepath.Join(workDir, "controller.log")
	positionsPath := filepath.Join(workDir, "positions.yaml")
	configPath := filepath.Join(workDir, "promtail.yaml")
	httpPort := freeLoopbackPort(t)
	if err := os.WriteFile(logPath, nil, 0o600); err != nil {
		t.Fatalf("create controller log: %v", err)
	}

	config := fmt.Sprintf(`server:
  http_listen_address: 127.0.0.1
  http_listen_port: %d
  grpc_listen_port: 0

positions:
  filename: %q

clients:
  - url: %q

scrape_configs:
  - job_name: jumpstarter
    static_configs:
      - targets: [localhost]
        labels:
          __path__: %q
          namespace: lab
    pipeline_stages:
`+promtailPipelineStages, httpPort, positionsPath, loki.URL+"/loki/api/v1/push", logPath)

	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatalf("write Promtail config: %v", err)
	}
	if strings.Contains(config, "basic_auth") || strings.Contains(config, "password") {
		t.Fatal("Promtail test config must remain credential-free")
	}

	cmd := exec.Command(promtailBin, "-config.file="+configPath) //nolint:gosec // test-controlled binary and paths
	logs := &liveProcessBuffer{}
	cmd.Stdout = logs
	cmd.Stderr = logs
	if err := cmd.Start(); err != nil {
		t.Fatalf("start Promtail: %v", err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Signal(syscall.SIGTERM)
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
	})

	waitForHTTP(t, fmt.Sprintf("http://127.0.0.1:%d/ready", httpPort), 15*time.Second, logs)

	run := fmt.Sprintf("promtail-live-%d", time.Now().UnixNano())
	withMarker := run + "-with"
	withoutMarker := run + "-without"
	withExporter := fmt.Sprintf(`{"ts":"2026-11-02T10:00:00Z","level":"info","msg":"with exporter","component":"controller","exporter":"board-1","lease_id":%q,"client":"client-1","build_id":"build-1","error":"line1\nline2"}`, withMarker)
	withoutExporter := fmt.Sprintf(`{"ts":"2026-11-02T10:00:01Z","level":"info","msg":"without exporter","component":"controller","lease_id":%q,"client":"client-2","build_id":"build-2"}`, withoutMarker)
	criLine := func(line string) string {
		return fmt.Sprintf("%s stdout F %s", time.Now().UTC().Format(time.RFC3339Nano), line)
	}
	if err := os.WriteFile(logPath, []byte(criLine(withExporter)+"\n"+criLine(withoutExporter)+"\n"), 0o600); err != nil {
		t.Fatalf("write controller log: %v", err)
	}

	entries := loki.waitForEntries(t, run, 2, 60*time.Second)

	withEntry := findLokiEntry(t, entries, withMarker)
	requireLokiLabels(t, withEntry, map[string]string{
		"component": "controller",
		"exporter":  "board-1",
		"namespace": "lab",
	})
	withoutEntry := findLokiEntry(t, entries, withoutMarker)
	requireLokiLabels(t, withoutEntry, map[string]string{
		"component": "controller",
		"namespace": "lab",
	})
	if _, ok := withoutEntry.Labels["exporter"]; ok {
		t.Fatalf("exporter-less stream must not contain exporter label: %v", withoutEntry.Labels)
	}

	withBody := parseLokiJSONBody(t, withEntry)
	for key, want := range map[string]string{
		"lease_id": withMarker,
		"client":   "client-1",
		"build_id": "build-1",
		"error":    "line1\nline2",
	} {
		if got, ok := withBody[key].(string); !ok || got != want {
			t.Fatalf("with-exporter body[%q] = %v, want %q; body: %v", key, withBody[key], want, withBody)
		}
	}
	withoutBody := parseLokiJSONBody(t, withoutEntry)
	if got := withoutBody["lease_id"]; got != withoutMarker {
		t.Fatalf("without-exporter body lease_id = %v, want %s", got, withoutMarker)
	}
	if got := withoutBody["client"]; got != "client-2" {
		t.Fatalf("without-exporter body client = %v, want client-2", got)
	}
}
