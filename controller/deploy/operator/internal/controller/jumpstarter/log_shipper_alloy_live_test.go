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
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// alloyLogBuffer serializes writes from Alloy's stdout and stderr.
type alloyLogBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *alloyLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *alloyLogBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// TestAlloyLiveLogDelivery exercises the shared Alloy process/write path with a
// real Alloy process and a real Loki. A local file source feeds known JSON
// lines into the generated loki.process/loki.write blocks; Loki's query API
// asserts the delivered stream labels, exporter omission, and JSON body.
func TestAlloyLiveLogDelivery(t *testing.T) {
	alloyBin := testBinary(t, "ALLOY_BIN", "alloy",
		"set ALLOY_BIN or install alloy to run the live Alloy delivery test")
	loki := startLokiTestServer(t)

	serverPort := freeLoopbackPort(t)
	workDir := t.TempDir()
	logPath := filepath.Join(workDir, "controller.log")
	configPath := filepath.Join(workDir, "config.alloy")
	storagePath := filepath.Join(workDir, "data")
	readyURL := fmt.Sprintf("http://127.0.0.1:%d/ready", serverPort)

	config := fmt.Sprintf(`local.file_match "test" {
	path_targets = [{
		__path__  = %q,
		component = "controller",
		namespace = "lab",
	}]
	sync_period = "100ms"
}

loki.source.file "test" {
	targets    = local.file_match.test.targets
	forward_to = [loki.process.jumpstarter.receiver]
}

%s`, logPath, fmt.Sprintf(alloyProcessWriteTemplate, loki.URL+"/loki/api/v1/push"))

	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatalf("write Alloy config: %v", err)
	}
	if strings.Contains(config, "basic_auth") || strings.Contains(config, "password") {
		t.Fatal("generated Alloy config must remain credential-free")
	}

	cmd := exec.Command( //nolint:gosec // test-controlled binary and paths
		alloyBin,
		"run",
		"--disable-reporting",
		fmt.Sprintf("--server.http.listen-addr=127.0.0.1:%d", serverPort),
		"--storage.path="+storagePath,
		configPath,
	)
	logs := &alloyLogBuffer{}
	cmd.Stdout = logs
	cmd.Stderr = logs
	if err := cmd.Start(); err != nil {
		t.Fatalf("start Alloy: %v", err)
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

	waitForHTTP(t, readyURL, 15*time.Second, logs)

	run := fmt.Sprintf("alloy-live-%d", time.Now().UnixNano())
	withMarker := run + "-with"
	withoutMarker := run + "-without"
	withExporter := fmt.Sprintf(`{"ts":"2026-11-02T10:00:00Z","level":"info","msg":"lease acquired","component":"controller","exporter":"board-1","lease_id":%q,"client":"client-1","build_id":"build-1","error":"line1\nline2"}`, withMarker)
	withoutExporter := fmt.Sprintf(`{"ts":"2026-11-02T10:00:01Z","level":"info","msg":"lease released","component":"controller","lease_id":%q,"client":"client-2","build_id":"build-2"}`, withoutMarker)
	if err := os.WriteFile(logPath, []byte(withExporter+"\n"+withoutExporter+"\n"), 0o600); err != nil {
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

func freeLoopbackPort(t *testing.T) int {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("allocate loopback port: %v", err)
	}
	defer func() { _ = listener.Close() }()
	return listener.Addr().(*net.TCPAddr).Port
}

func waitForHTTP(t *testing.T, url string, timeout time.Duration, logs *alloyLogBuffer) {
	t.Helper()
	client := &http.Client{Timeout: 2 * time.Second}
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		resp, err := client.Get(url) //nolint:gosec // test-controlled loopback URL
		if err == nil {
			_ = resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				return
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s\nAlloy output:\n%s", url, logs.String())
}

func waitForLogLine(t *testing.T, logs *alloyLogBuffer, marker string, timeout time.Duration) string {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		for _, line := range strings.Split(logs.String(), "\n") {
			if strings.Contains(line, "received log entry") && strings.Contains(line, marker) {
				return line
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for delivered entry %q\nAlloy output:\n%s", marker, logs.String())
	return ""
}

func parseEchoedEntry(t *testing.T, line string) (map[string]string, string) {
	t.Helper()
	labelsStart := strings.Index(line, `labels="`)
	entryStart := strings.Index(line, `" entry="`)
	if labelsStart < 0 || entryStart < 0 || entryStart <= labelsStart {
		t.Fatalf("cannot find labels/entry in Alloy echo line: %s", line)
	}
	rawLabels := line[labelsStart+len(`labels="`) : entryStart]
	rawLabels = strings.ReplaceAll(rawLabels, `\"`, `"`)

	labels := make(map[string]string)
	labelRE := regexp.MustCompile(`([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"`)
	for _, match := range labelRE.FindAllStringSubmatch(rawLabels, -1) {
		labels[match[1]] = match[2]
	}

	entryField := line[entryStart+len(`" entry="`):]
	end := strings.Index(entryField, `" entry_timestamp=`)
	if end < 0 {
		t.Fatalf("cannot find end of entry field in Alloy echo line: %s", line)
	}
	rawEntry := entryField[:end]
	entry, err := strconv.Unquote(`"` + rawEntry + `"`)
	if err != nil {
		t.Fatalf("unescape delivered entry: %v\nraw entry: %s", err, rawEntry)
	}
	return labels, entry
}
