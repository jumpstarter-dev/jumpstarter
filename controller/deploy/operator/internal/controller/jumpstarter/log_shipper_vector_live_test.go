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

// TestVectorLiveLogDelivery exercises the shared Vector remap/filter/sink path
// with a real Vector process and a real Loki. A file source feeds known JSON
// and non-JSON lines into the production processing blocks; Loki's query API
// asserts labels, exporter omission, component fallback, and body preservation.
func TestVectorLiveLogDelivery(t *testing.T) {
	vectorBin := vectorTestBinary(t)
	loki := startLokiTestServer(t)

	workDir := t.TempDir()
	logPath := filepath.Join(workDir, "controller.log")
	configPath := filepath.Join(workDir, "vector.yaml")
	dataDir := filepath.Join(workDir, "data")
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		t.Fatalf("create Vector data dir: %v", err)
	}

	config := fmt.Sprintf(`data_dir: %q

sources:
  jumpstarter_file:
    type: file
    include: [%q]
    read_from: beginning

transforms:
  jumpstarter_pods:
    type: remap
    inputs: [jumpstarter_file]
    source: |
      .kubernetes.pod_namespace = "lab"
      .kubernetes.pod_labels.component = "controller"

%s%s`, dataDir, logPath, vectorProcessBlocks, fmt.Sprintf(vectorSinksTemplate, loki.URL, "/loki/api/v1/push", loki.URL, "/loki/api/v1/push"))

	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatalf("write Vector config: %v", err)
	}
	if strings.Contains(config, "<loki") || strings.Contains(config, "basic_auth") {
		t.Fatal("Vector test config must not contain placeholder endpoints or credentials")
	}

	run := fmt.Sprintf("vector-live-%d", time.Now().UnixNano())
	withMarker := run + "-with"
	withoutMarker := run + "-without"
	nonJSONMarker := run + "-nonjson"
	missingMarker := run + "-missing"
	nonStringMarker := run + "-nonstring"
	emptyMarker := run + "-empty"
	withExporter := fmt.Sprintf(`{"ts":"2026-11-02T10:00:00Z","level":"info","msg":"with exporter","component":"controller","exporter":"board-1","lease_id":%q,"client":"client-1","build_id":"build-1","error":"line1\nline2"}`, withMarker)
	withoutExporter := fmt.Sprintf(`{"ts":"2026-11-02T10:00:01Z","level":"info","msg":"without exporter","component":"controller","lease_id":%q,"client":"client-2","build_id":"build-2"}`, withoutMarker)
	nonJSON := fmt.Sprintf("I1103 19:10:01.000000       1 vector-live.go:1] %s non-JSON line", nonJSONMarker)
	missingComponent := fmt.Sprintf(`{"ts":"2026-11-02T10:00:02Z","level":"info","msg":"missing component","lease_id":%q}`, missingMarker)
	nonStringComponent := fmt.Sprintf(`{"ts":"2026-11-02T10:00:03Z","level":"info","msg":"non-string component","component":123,"lease_id":%q}`, nonStringMarker)
	emptyComponent := fmt.Sprintf(`{"ts":"2026-11-02T10:00:04Z","level":"info","msg":"empty component","component":"","exporter":"board-empty","lease_id":%q}`, emptyMarker)
	logLines := []string{withExporter, withoutExporter, nonJSON, missingComponent, nonStringComponent, emptyComponent}
	if err := os.WriteFile(logPath, []byte(strings.Join(logLines, "\n")+"\n"), 0o600); err != nil {
		t.Fatalf("write controller log: %v", err)
	}

	vectorLogs := startVectorForTest(t, vectorBin, configPath)
	entries := loki.waitForEntries(t, run, len(logLines), 60*time.Second)
	if logs := vectorLogs.String(); strings.Contains(logs, "template_failed") || strings.Contains(logs, "Missing fields") {
		t.Fatalf("Vector reported a label template failure:\n%s", logs)
	}

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

	nonJSONEntry := findLokiEntry(t, entries, nonJSONMarker)
	requireLokiLabels(t, nonJSONEntry, map[string]string{
		"component": "controller",
		"namespace": "lab",
	})
	if nonJSONEntry.Line != nonJSON {
		t.Fatalf("non-JSON body = %q, want unchanged %q", nonJSONEntry.Line, nonJSON)
	}

	missingEntry := findLokiEntry(t, entries, missingMarker)
	requireLokiLabels(t, missingEntry, map[string]string{
		"component": "controller",
		"namespace": "lab",
	})
	missingBody := parseLokiJSONBody(t, missingEntry)
	if got := missingBody["lease_id"]; got != missingMarker {
		t.Fatalf("missing-component body lease_id = %v, want %s", got, missingMarker)
	}

	nonStringEntry := findLokiEntry(t, entries, nonStringMarker)
	requireLokiLabels(t, nonStringEntry, map[string]string{
		"component": "controller",
		"namespace": "lab",
	})
	nonStringBody := parseLokiJSONBody(t, nonStringEntry)
	if got := nonStringBody["component"]; got != float64(123) {
		t.Fatalf("non-string-component body component = %v, want 123", got)
	}

	emptyEntry := findLokiEntry(t, entries, emptyMarker)
	requireLokiLabels(t, emptyEntry, map[string]string{
		"component": "controller",
		"exporter":  "board-empty",
		"namespace": "lab",
	})
	emptyBody := parseLokiJSONBody(t, emptyEntry)
	if got := emptyBody["lease_id"]; got != emptyMarker {
		t.Fatalf("empty-component body lease_id = %v, want %s", got, emptyMarker)
	}
}

func vectorTestBinary(t *testing.T) string {
	t.Helper()
	return testBinary(t, "VECTOR_BIN", "vector",
		"set VECTOR_BIN or install vector to run the live Vector delivery test")
}

func startVectorForTest(t *testing.T, vectorBin, configPath string) *liveProcessBuffer {
	t.Helper()
	cmd := exec.Command(vectorBin, "-q", "--config", configPath) //nolint:gosec // test-controlled binary and paths
	logs := &liveProcessBuffer{}
	cmd.Stdout = logs
	cmd.Stderr = logs
	if err := cmd.Start(); err != nil {
		t.Fatalf("start Vector: %v", err)
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
	return logs
}
