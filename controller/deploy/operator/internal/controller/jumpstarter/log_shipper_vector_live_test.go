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
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

type vectorLogBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *vectorLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *vectorLogBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

type vectorLokiPush struct {
	Streams []struct {
		Stream map[string]string   `json:"stream"`
		Values [][]json.RawMessage `json:"values"`
	} `json:"streams"`
}

// TestVectorLiveLogDelivery exercises the shared Vector remap/filter/sink path
// with a real Vector process. A file source feeds known JSON lines into the
// production processing blocks; a local Loki-compatible HTTP receiver asserts
// labels, exporter omission, and original JSON body preservation.
func TestVectorLiveLogDelivery(t *testing.T) {
	vectorBin := vectorTestBinary(t)

	var (
		mu       sync.Mutex
		requests []vectorLokiPush
	)
	server := newVectorLokiServer(t, &mu, &requests)
	defer server.Close()

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

%s%s`, dataDir, logPath, vectorProcessBlocks, fmt.Sprintf(vectorSinksTemplate, server.URL, "/loki/api/v1/push", server.URL, "/loki/api/v1/push"))

	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatalf("write Vector config: %v", err)
	}
	if strings.Contains(config, "<loki") || strings.Contains(config, "basic_auth") {
		t.Fatal("Vector test config must not contain placeholder endpoints or credentials")
	}

	withExporter := `{"ts":"2026-11-02T10:00:00Z","level":"info","msg":"with exporter","component":"controller","exporter":"board-1","lease_id":"lease-123","client":"client-1","build_id":"build-1","error":"line1\nline2"}`
	withoutExporter := `{"ts":"2026-11-02T10:00:01Z","level":"info","msg":"without exporter","component":"controller","lease_id":"lease-456","client":"client-2","build_id":"build-2"}`
	if err := os.WriteFile(logPath, []byte(withExporter+"\n"+withoutExporter+"\n"), 0o600); err != nil {
		t.Fatalf("write controller log: %v", err)
	}

	logs := startVectorForTest(t, vectorBin, configPath)
	got := waitForVectorPushes(t, &mu, &requests, 2, 30*time.Second)
	if len(got) < 2 {
		t.Fatalf("expected two Loki push requests, got %d\nVector output:\n%s", len(got), logs.String())
	}
	assertVectorDelivery(t, got)
}

func vectorTestBinary(t *testing.T) string {
	t.Helper()
	vectorBin := os.Getenv("VECTOR_BIN")
	if vectorBin == "" {
		var err error
		vectorBin, err = exec.LookPath("vector")
		if err != nil {
			t.Skip("set VECTOR_BIN or install vector to run the live Vector delivery test")
		}
	}
	if _, err := os.Stat(vectorBin); err != nil {
		t.Skipf("Vector binary %s is not available: %v", vectorBin, err)
	}
	return vectorBin
}

func newVectorLokiServer(t *testing.T, mu *sync.Mutex, requests *[]vectorLokiPush) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			w.WriteHeader(http.StatusOK)
			return
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if len(body) == 0 {
			// Sink healthchecks may POST an empty body.
			w.WriteHeader(http.StatusNoContent)
			return
		}
		var push vectorLokiPush
		if err := json.Unmarshal(body, &push); err != nil {
			t.Logf("Vector push body was not JSON: %v\nbody: %s", err, body)
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		mu.Lock()
		*requests = append(*requests, push)
		mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	}))
}

func startVectorForTest(t *testing.T, vectorBin, configPath string) *vectorLogBuffer {
	t.Helper()
	cmd := exec.Command(vectorBin, "-q", "--config", configPath) //nolint:gosec // test-controlled binary and paths
	logs := &vectorLogBuffer{}
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

func waitForVectorPushes(t *testing.T, mu *sync.Mutex, requests *[]vectorLokiPush, want int, timeout time.Duration) []vectorLokiPush {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		mu.Lock()
		count := len(*requests)
		mu.Unlock()
		if count >= want {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	return append([]vectorLokiPush(nil), (*requests)...)
}

func assertVectorDelivery(t *testing.T, got []vectorLokiPush) {
	t.Helper()
	withLabels, withoutLabels, withBody, withoutBody := findVectorStreams(t, got)
	if len(withLabels) != 3 || withLabels["component"] != "controller" ||
		withLabels["exporter"] != "board-1" || withLabels["namespace"] != "lab" {
		t.Fatalf("unexpected exporter stream labels: %v", withLabels)
	}
	if len(withoutLabels) != 2 || withoutLabels["component"] != "controller" ||
		withoutLabels["namespace"] != "lab" {
		t.Fatalf("unexpected exporter-less stream labels: %v", withoutLabels)
	}
	if _, ok := withoutLabels["exporter"]; ok {
		t.Fatalf("exporter-less stream must not contain exporter label: %v", withoutLabels)
	}

	var withJSON, withoutJSON map[string]any
	if err := json.Unmarshal([]byte(withBody), &withJSON); err != nil {
		t.Fatalf("parse body with exporter: %v\nbody: %s", err, withBody)
	}
	if err := json.Unmarshal([]byte(withoutBody), &withoutJSON); err != nil {
		t.Fatalf("parse body without exporter: %v\nbody: %s", err, withoutBody)
	}
	for key, want := range map[string]string{
		"lease_id": "lease-123",
		"client":   "client-1",
		"build_id": "build-1",
		"error":    "line1\nline2",
	} {
		if got, ok := withJSON[key].(string); !ok || got != want {
			t.Fatalf("with-exporter body[%q] = %v, want %q; body: %v", key, withJSON[key], want, withJSON)
		}
	}
	if got := withoutJSON["lease_id"]; got != "lease-456" {
		t.Fatalf("without-exporter body lease_id = %v, want lease-456", got)
	}
}

func findVectorStreams(t *testing.T, requests []vectorLokiPush) (map[string]string, map[string]string, string, string) {
	t.Helper()
	var withLabels, withoutLabels map[string]string
	var withBody, withoutBody string
	for _, req := range requests {
		for _, stream := range req.Streams {
			if len(stream.Values) == 0 || len(stream.Values[0]) < 2 {
				continue
			}
			var line string
			if err := json.Unmarshal(stream.Values[0][1], &line); err != nil {
				t.Fatalf("parse Loki value line: %v\nraw: %s", err, stream.Values[0][1])
			}
			if stream.Stream["exporter"] == "board-1" {
				withLabels, withBody = stream.Stream, line
			} else if stream.Stream["component"] == "controller" {
				withoutLabels, withoutBody = stream.Stream, line
			}
		}
	}
	if withLabels == nil || withoutLabels == nil {
		t.Fatalf("did not find both exporter and exporter-less streams: %+v", requests)
	}
	return withLabels, withoutLabels, withBody, withoutBody
}
