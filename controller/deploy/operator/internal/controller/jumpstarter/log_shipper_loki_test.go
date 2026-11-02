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
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"sync"
	"syscall"
	"testing"
	"time"
)

// lokiValidatedVersion is the single-binary Loki release used by the live
// shipper delivery tests and by CI. Keep it aligned with the checksum in
// .github/workflows/controller-tests.yaml.
const lokiValidatedVersion = "v3.7.9"

type liveProcessBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *liveProcessBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *liveProcessBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

type lokiTestServer struct {
	URL       string
	logs      *liveProcessBuffer
	startedAt time.Time
}

type lokiQueryResponse struct {
	Status string `json:"status"`
	Data   struct {
		ResultType string `json:"resultType"`
		Result     []struct {
			Stream map[string]string   `json:"stream"`
			Values [][]json.RawMessage `json:"values"`
		} `json:"result"`
	} `json:"data"`
}

type lokiEntry struct {
	Labels    map[string]string
	Line      string
	Timestamp time.Time
}

// startLokiTestServer starts a real single-binary Loki with filesystem storage.
// The tests use it instead of a mock HTTP receiver so the full push/query
// contract is exercised.
func startLokiTestServer(t *testing.T) *lokiTestServer {
	t.Helper()

	lokiBin := testBinary(t, "LOKI_BIN", "loki",
		"set LOKI_BIN or install loki to run live Loki delivery tests")
	t.Logf("starting Loki %s", lokiValidatedVersion)
	httpPort := freeLoopbackPort(t)
	grpcPort := freeAllInterfacesPort(t)
	workDir := t.TempDir()
	configPath := filepath.Join(workDir, "loki.yaml")

	config := fmt.Sprintf(`auth_enabled: false

server:
  http_listen_address: 127.0.0.1
  http_listen_port: %d
  # Loki's query frontend advertises the host address in the scheduler ring.
  # Binding the test-only gRPC listener on all interfaces lets the single
  # process connect to itself while HTTP remains loopback-only.
  grpc_listen_address: 0.0.0.0
  grpc_listen_port: %d

common:
  path_prefix: %q
  storage:
    filesystem:
      chunks_directory: %q
      rules_directory: %q
  replication_factor: 1
  ring:
    instance_addr: 127.0.0.1
    instance_port: %d
    kvstore:
      store: inmemory

schema_config:
  configs:
    - from: 2024-04-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

storage_config:
  tsdb_shipper:
    active_index_directory: %q
    cache_location: %q

limits_config:
  allow_structured_metadata: true
  discover_service_name: []
  discover_log_levels: false
`, httpPort, grpcPort,
		filepath.Join(workDir, "loki"),
		filepath.Join(workDir, "loki", "chunks"),
		filepath.Join(workDir, "loki", "rules"),
		grpcPort,
		filepath.Join(workDir, "loki", "tsdb-index"),
		filepath.Join(workDir, "loki", "tsdb-cache"),
	)

	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatalf("write Loki config: %v", err)
	}

	logs := &liveProcessBuffer{}
	cmd := exec.Command(lokiBin, "-config.file="+configPath) //nolint:gosec // test-controlled binary and paths
	cmd.Stdout = logs
	cmd.Stderr = logs
	if err := cmd.Start(); err != nil {
		t.Fatalf("start Loki: %v", err)
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

	server := &lokiTestServer{
		URL:       fmt.Sprintf("http://127.0.0.1:%d", httpPort),
		logs:      logs,
		startedAt: time.Now(),
	}
	server.waitReady(t, 60*time.Second)
	return server
}

func (s *lokiTestServer) waitReady(t *testing.T, timeout time.Duration) {
	t.Helper()
	client := &http.Client{Timeout: 2 * time.Second}
	deadline := time.Now().Add(timeout)
	var last string
	for time.Now().Before(deadline) {
		resp, err := client.Get(s.URL + "/ready")
		if err == nil {
			_ = resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return
			}
			last = resp.Status
		} else {
			last = err.Error()
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("Loki did not become ready: %s\nLoki output:\n%s", last, s.logs.String())
}

func (s *lokiTestServer) queryRange(query string, start, end time.Time) ([]lokiEntry, error) {
	values := url.Values{}
	values.Set("query", query)
	values.Set("start", strconv.FormatInt(start.UnixNano(), 10))
	values.Set("end", strconv.FormatInt(end.UnixNano(), 10))
	values.Set("limit", "100")
	values.Set("direction", "backward")

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(s.URL + "/loki/api/v1/query_range?" + values.Encode())
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Loki query returned %s", resp.Status)
	}

	var result lokiQueryResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode Loki query response: %w", err)
	}
	if result.Status != "success" {
		return nil, fmt.Errorf("Loki query status %q", result.Status)
	}

	var entries []lokiEntry
	for _, stream := range result.Data.Result {
		for _, value := range stream.Values {
			if len(value) < 2 {
				continue
			}
			var rawTimestamp, line string
			if err := json.Unmarshal(value[0], &rawTimestamp); err != nil {
				return nil, fmt.Errorf("decode Loki timestamp: %w", err)
			}
			if err := json.Unmarshal(value[1], &line); err != nil {
				return nil, fmt.Errorf("decode Loki line: %w", err)
			}
			ts, _ := strconv.ParseInt(rawTimestamp, 10, 64)
			entries = append(entries, lokiEntry{
				Labels:    stream.Stream,
				Line:      line,
				Timestamp: time.Unix(0, ts),
			})
		}
	}
	return entries, nil
}

// waitForEntries polls the real Loki query API until at least want entries
// containing marker are visible. Query visibility is asynchronous because the
// ingester may still be flushing after accepting the push.
func (s *lokiTestServer) waitForEntries(t *testing.T, marker string, want int, timeout time.Duration) []lokiEntry {
	t.Helper()
	query := fmt.Sprintf(`{component="controller",namespace="lab"} |= %q`, marker)
	deadline := time.Now().Add(timeout)
	var lastErr error
	for time.Now().Before(deadline) {
		entries, err := s.queryRange(query, s.startedAt.Add(-time.Minute), time.Now().Add(time.Minute))
		if err != nil {
			lastErr = err
		} else if len(entries) >= want {
			return entries
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %d Loki entries containing %q: %v\nLoki output:\n%s",
		want, marker, lastErr, s.logs.String())
	return nil
}

func testBinary(t *testing.T, envName, name, skipMessage string) string {
	t.Helper()
	path := os.Getenv(envName)
	if path == "" {
		var err error
		path, err = exec.LookPath(name)
		if err != nil {
			t.Skip(skipMessage)
		}
	}
	if _, err := os.Stat(path); err != nil {
		t.Skipf("%s binary %s is not available: %v", name, path, err)
	}
	return path
}

func freeAllInterfacesPort(t *testing.T) int {
	t.Helper()
	listener, err := net.Listen("tcp", "0.0.0.0:0")
	if err != nil {
		t.Fatalf("allocate all-interfaces port: %v", err)
	}
	defer func() { _ = listener.Close() }()
	return listener.Addr().(*net.TCPAddr).Port
}

func findLokiEntry(t *testing.T, entries []lokiEntry, marker string) lokiEntry {
	t.Helper()
	for _, entry := range entries {
		if bytes.Contains([]byte(entry.Line), []byte(marker)) {
			return entry
		}
	}
	t.Fatalf("no Loki entry contains marker %q: %+v", marker, entries)
	return lokiEntry{}
}

func requireLokiLabels(t *testing.T, entry lokiEntry, want map[string]string) {
	t.Helper()
	if len(entry.Labels) != len(want) {
		t.Fatalf("Loki stream labels = %v, want exactly %v", entry.Labels, want)
	}
	for key, value := range want {
		if entry.Labels[key] != value {
			t.Fatalf("Loki stream labels = %v, want %v", entry.Labels, want)
		}
	}
}

func parseLokiJSONBody(t *testing.T, entry lokiEntry) map[string]any {
	t.Helper()
	var body map[string]any
	if err := json.Unmarshal([]byte(entry.Line), &body); err != nil {
		t.Fatalf("parse Loki JSON body: %v\nline: %s", err, entry.Line)
	}
	return body
}
