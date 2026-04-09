/*
Copyright 2026. The Jumpstarter Authors

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

// Package e2e provides utilities and test suites for Jumpstarter E2E testing.
package e2e

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive // ginkgo DSL
	. "github.com/onsi/gomega"    //nolint:revive // gomega DSL
	"go.yaml.in/yaml/v3"
)

const (
	defaultNamespace    = "jumpstarter-lab"
	defaultWaitTimeout  = 5 * time.Minute
	exporterPollPeriod  = 500 * time.Millisecond
	exporterPostDelay   = 2 * time.Second
	exporterProcessWait = 2 * time.Second
)

// --- Environment helpers ---

// Namespace returns the test namespace from E2E_TEST_NS (falling back to
// the default "jumpstarter-lab").
func Namespace() string {
	if ns := os.Getenv("E2E_TEST_NS"); ns != "" {
		return ns
	}
	return defaultNamespace
}

// Endpoint returns the controller gRPC endpoint from the ENDPOINT env var.
func Endpoint() string {
	return os.Getenv("ENDPOINT")
}

// LoginEndpoint returns the login HTTP endpoint from LOGIN_ENDPOINT env var.
func LoginEndpoint() string {
	return os.Getenv("LOGIN_ENDPOINT")
}

// PythonVenv returns the path to the Python venv for the current client,
// or empty string if not set.
func PythonVenv() string {
	return os.Getenv("PYTHON_VENV")
}

// PythonOldVenv returns the path to the old Python venv (for compat tests),
// or empty string if not set.
func PythonOldVenv() string {
	return os.Getenv("PYTHON_OLD_VENV")
}

// OldJmp returns the path to the old jmp binary for compat tests.
// It derives the path from PYTHON_OLD_VENV by looking for "jmp" or "j"
// in the venv's bin directory.
func OldJmp() string {
	venv := PythonOldVenv()
	if venv == "" {
		return ""
	}
	binDir := filepath.Join(venv, "bin")
	for _, name := range []string{"jmp", "j"} {
		candidate := filepath.Join(binDir, name)
		if info, err := os.Stat(candidate); err == nil && !info.IsDir() {
			return candidate
		}
	}
	return ""
}

// RepoRoot returns the repository root directory (parent of e2e/).
func RepoRoot() string {
	// Try to find it relative to the test binary or use env
	if root := os.Getenv("REPO_ROOT"); root != "" {
		return root
	}
	// Fallback: assume we're run from repo root
	wd, err := os.Getwd()
	if err != nil {
		return "."
	}
	// If we're inside e2e/test, go up two levels
	if strings.HasSuffix(wd, filepath.Join("e2e", "test")) {
		return filepath.Join(wd, "..", "..")
	}
	return wd
}

// --- Command execution helpers ---

// RunCmd executes a command and returns combined stdout+stderr and error.
func RunCmd(name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	return strings.TrimSpace(out.String()), err
}

// RunCmdSplit executes a command and returns stdout and stderr separately.
func RunCmdSplit(name string, args ...string) (stdout, stderr string, err error) {
	cmd := exec.Command(name, args...)
	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf
	err = cmd.Run()
	return strings.TrimSpace(outBuf.String()), strings.TrimSpace(errBuf.String()), err
}

// RunCmdWithEnv executes a command with extra environment variables.
func RunCmdWithEnv(env map[string]string, name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, fmt.Sprintf("%s=%s", k, v))
	}
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	return strings.TrimSpace(out.String()), err
}

// RunCmdWithEnvUnset executes a command with specific env vars removed.
func RunCmdWithEnvUnset(unset []string, name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	unsetMap := make(map[string]bool)
	for _, k := range unset {
		unsetMap[k] = true
	}
	for _, e := range os.Environ() {
		parts := strings.SplitN(e, "=", 2)
		if !unsetMap[parts[0]] {
			cmd.Env = append(cmd.Env, e)
		}
	}
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	err := cmd.Run()
	return strings.TrimSpace(out.String()), err
}

// MustRunCmd is like RunCmd but fails the test on error.
func MustRunCmd(name string, args ...string) string {
	out, err := RunCmd(name, args...)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "command %s %v failed: %s", name, args, out)
	return out
}

// Jmp runs a jmp CLI command and returns the output.
func Jmp(args ...string) (string, error) {
	return RunCmd("jmp", args...)
}

// JmpCmd creates an *exec.Cmd for the jmp CLI without starting it.
// This is useful when the caller needs process-level control (e.g.,
// Start/Wait, SysProcAttr, process group management).
func JmpCmd(args ...string) *exec.Cmd {
	return exec.Command("jmp", args...)
}

// MustJmp runs a jmp CLI command and fails the test on error.
func MustJmp(args ...string) string {
	out, err := Jmp(args...)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "jmp %v failed: %s", args, out)
	return out
}

// Kubectl runs a kubectl command and returns the output.
func Kubectl(args ...string) (string, error) {
	return RunCmd("kubectl", args...)
}

// MustKubectl runs a kubectl command and fails the test on error.
func MustKubectl(args ...string) string {
	out, err := Kubectl(args...)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "kubectl %v failed: %s", args, out)
	return out
}

// ReadYAMLField reads a top-level field from a YAML file and returns its
// string value. For scalar values the string representation is returned;
// for nested structures the re-marshalled YAML is returned.
func ReadYAMLField(filePath, field string) (string, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return "", fmt.Errorf("reading %s: %w", filePath, err)
	}
	var doc map[string]interface{}
	if err := yaml.Unmarshal(data, &doc); err != nil {
		return "", fmt.Errorf("parsing YAML from %s: %w", filePath, err)
	}
	val, ok := doc[field]
	if !ok {
		return "", fmt.Errorf("field %q not found in %s", field, filePath)
	}
	switch v := val.(type) {
	case string:
		return v, nil
	case nil:
		return "", nil
	default:
		out, err := yaml.Marshal(v)
		if err != nil {
			return "", fmt.Errorf("marshalling field %q: %w", field, err)
		}
		return strings.TrimSpace(string(out)), nil
	}
}

// MustReadYAMLField is like ReadYAMLField but fails the test on error.
func MustReadYAMLField(filePath, field string) string {
	val, err := ReadYAMLField(filePath, field)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "reading YAML field %q from %s", field, filePath)
	return val
}

// --- Process management ---

// logBuffer is a thread-safe in-memory buffer for capturing process output.
type logBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (lb *logBuffer) Write(p []byte) (int, error) {
	lb.mu.Lock()
	defer lb.mu.Unlock()
	return lb.buf.Write(p)
}

func (lb *logBuffer) String() string {
	lb.mu.Lock()
	defer lb.mu.Unlock()
	return lb.buf.String()
}

func (lb *logBuffer) WriteString(s string) {
	lb.mu.Lock()
	defer lb.mu.Unlock()
	lb.buf.WriteString(s)
}

// ProcessTracker manages background exporter processes.
type ProcessTracker struct {
	pids    []int
	logs    map[string]*logBuffer
	cancels []context.CancelFunc
}

// NewProcessTracker creates a new ProcessTracker.
func NewProcessTracker() *ProcessTracker {
	return &ProcessTracker{
		logs: make(map[string]*logBuffer),
	}
}

// getOrCreateLog returns the in-memory log buffer for the given name.
func (pt *ProcessTracker) getOrCreateLog(name string) *logBuffer {
	if lb, ok := pt.logs[name]; ok {
		return lb
	}
	lb := &logBuffer{}
	pt.logs[name] = lb
	return lb
}

// StartExporterLoop starts an exporter in a restart loop using a Go goroutine
// (instead of a bash wrapper) and tracks the process PIDs.
func (pt *ProcessTracker) StartExporterLoop(exporterName string, jmpBin ...string) {
	jmp := "jmp"
	if len(jmpBin) > 0 && jmpBin[0] != "" {
		jmp = jmpBin[0]
	}
	lb := pt.getOrCreateLog(exporterName)

	ctx, cancel := context.WithCancel(context.Background())
	pt.cancels = append(pt.cancels, cancel)

	go func() {
		restartCount := 0
		for {
			select {
			case <-ctx.Done():
				return
			default:
			}

			cmd := exec.Command(jmp, "run", "--exporter", exporterName)
			cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			cmd.Stdout = lb
			cmd.Stderr = lb

			if err := cmd.Start(); err != nil {
				lb.WriteString(fmt.Sprintf("failed to start exporter %s: %v\n", exporterName, err))
				return
			}

			pid := cmd.Process.Pid
			// Track the PID under the parent lock-free path; this is safe
			// because StopAll first cancels the context so this goroutine
			// will not spawn new processes concurrently.
			pt.pids = append(pt.pids, pid)

			if restartCount > 0 {
				GinkgoWriter.Printf("Restarted exporter %s (PID %d, restart #%d)\n", exporterName, pid, restartCount)
			} else {
				GinkgoWriter.Printf("Started exporter loop for %s (PID %d)\n", exporterName, pid)
			}

			_ = cmd.Wait()
			restartCount++

			select {
			case <-ctx.Done():
				return
			case <-time.After(exporterProcessWait):
			}
		}
	}()
}

// StartExporterSingle starts an exporter once (no restart loop) and tracks the PID.
// A background goroutine calls Wait() so the process is reaped when it exits,
// allowing IsProcessRunning() to detect that the process is no longer alive.
func (pt *ProcessTracker) StartExporterSingle(exporterName string) *exec.Cmd {
	cmd := exec.Command("jmp", "run", "--exporter", exporterName)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	err := cmd.Start()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "failed to start exporter %s", exporterName)
	pt.pids = append(pt.pids, cmd.Process.Pid)
	GinkgoWriter.Printf("Started exporter %s (PID %d)\n", exporterName, cmd.Process.Pid)

	// Reap the child process in the background so it doesn't become a zombie.
	go func() {
		_ = cmd.Wait()
	}()

	return cmd
}

// StartDirectExporter starts an exporter with --tls-grpc-listener (direct mode).
func (pt *ProcessTracker) StartDirectExporter(configFile string, port int, passphrase string, captureStderr bool) (*exec.Cmd, *logBuffer) {
	args := []string{"run", "--exporter-config", configFile,
		"--tls-grpc-listener", strconv.Itoa(port),
		"--tls-grpc-insecure"}
	if passphrase != "" {
		args = append(args, "--passphrase", passphrase)
	}

	cmd := exec.Command("jmp", args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	var stderrBuf *logBuffer
	if captureStderr {
		stderrBuf = pt.getOrCreateLog("direct-exporter-stderr")
		cmd.Stderr = stderrBuf
	}

	err := cmd.Start()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "failed to start direct exporter with config %s", configFile)
	pt.pids = append(pt.pids, cmd.Process.Pid)
	GinkgoWriter.Printf("Started direct exporter (PID %d) on port %d\n", cmd.Process.Pid, port)
	return cmd, stderrBuf
}

// WriteLogMarker writes a marker into all in-memory log buffers for correlation.
func (pt *ProcessTracker) WriteLogMarker(testName string) {
	marker := fmt.Sprintf("\n\n=== TEST START: %s @ %s ===\n", testName, time.Now().Format(time.RFC3339))
	for _, lb := range pt.logs {
		lb.WriteString(marker)
	}
}

// DumpLogs prints log lines around the most recent test start marker.
// It shows ~20 lines of context before the marker and all lines after it.
func (pt *ProcessTracker) DumpLogs(_ int) {
	const contextBefore = 20

	for name, lb := range pt.logs {
		GinkgoWriter.Printf("\n--- Exporter logs (%s) ---\n", name)
		content := lb.String()
		if content == "" {
			GinkgoWriter.Println("(no output captured)")
			continue
		}

		lines := strings.Split(content, "\n")

		// Find the last test start marker
		markerIdx := -1
		for i := len(lines) - 1; i >= 0; i-- {
			if strings.Contains(lines[i], "=== TEST START:") {
				markerIdx = i
				break
			}
		}

		start := 0
		if markerIdx >= 0 {
			start = markerIdx - contextBefore
			if start < 0 {
				start = 0
			}
		}
		for _, line := range lines[start:] {
			GinkgoWriter.Println(line)
		}
	}
}

// StopAll cancels all restart loops, kills all tracked processes, and
// any orphans matching the pattern.
func (pt *ProcessTracker) StopAll() {
	// Cancel all restart-loop goroutines first
	for _, cancel := range pt.cancels {
		cancel()
	}
	pt.cancels = nil

	for _, pid := range pt.pids {
		proc, err := os.FindProcess(pid)
		if err != nil {
			continue
		}
		_ = proc.Signal(syscall.SIGKILL)
		_, _ = proc.Wait()
	}
	pt.pids = nil

	// Kill orphaned jmp exporter processes
	_ = exec.Command("pkill", "-9", "-f", "jmp run --exporter").Run()
}

// Cleanup stops all processes.
func (pt *ProcessTracker) Cleanup() {
	pt.StopAll()
}

// IsProcessRunning checks if any tracked process is still running.
func (pt *ProcessTracker) IsProcessRunning() bool {
	for _, pid := range pt.pids {
		proc, err := os.FindProcess(pid)
		if err != nil {
			continue
		}
		// Signal 0 tests if process exists
		if proc.Signal(syscall.Signal(0)) == nil {
			return true
		}
	}
	return false
}

// --- Exporter wait helpers ---

// WaitForExporter waits for an exporter to become Online, Registered, and Available.
func WaitForExporter(name string) {
	ns := Namespace()
	exporterRef := fmt.Sprintf("exporters.jumpstarter.dev/%s", name)

	// Brief delay to avoid catching pre-disconnect state
	time.Sleep(exporterPostDelay)

	// Wait for Online + Registered conditions
	MustRunCmd("kubectl", "-n", ns, "wait", "--timeout", "5m",
		"--for=condition=Online", "--for=condition=Registered", exporterRef)

	// Poll until exporterStatus is Available
	Eventually(func() string {
		out, _ := Kubectl("-n", ns, "get", exporterRef,
			"-o", "jsonpath={.status.exporterStatus}")
		return out
	}, defaultWaitTimeout, exporterPollPeriod).Should(Equal("Available"),
		"timed out waiting for %s to reach Available status", name)
}

// WaitForExporters waits for multiple exporters in parallel.
func WaitForExporters(names ...string) {
	// Brief delay
	time.Sleep(exporterPostDelay)

	for _, name := range names {
		name := name // capture
		WaitForExporter(name)
	}
}

// WaitForExporterOffline waits for an exporter to go offline.
func WaitForExporterOffline(name string) {
	ns := Namespace()
	exporterRef := fmt.Sprintf("exporters.jumpstarter.dev/%s", name)

	Eventually(func() bool {
		out, _ := Kubectl("-n", ns, "get", exporterRef,
			"-o", `jsonpath={.status.conditions[?(@.type=="Online")].status}`)
		return out == "False" || out == "Unknown" || out == ""
	}, 200*time.Second, time.Second).Should(BeTrue(),
		"timed out waiting for %s to go offline", name)
}

// WaitForDirectExporterReady waits for a direct listener exporter to be reachable via gRPC.
func WaitForDirectExporterReady(port int, passphrase string) {
	args := []string{"shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", port), "--tls-grpc-insecure"}
	if passphrase != "" {
		args = append(args, "--passphrase", passphrase)
	}
	args = append(args, "--", "j", "--help")

	Eventually(func() error {
		_, err := Jmp(args...)
		return err
	}, 15*time.Second, 500*time.Millisecond).Should(Succeed(),
		"direct exporter on port %d did not become ready", port)
}

// WaitForDirectExporterPort waits for a TCP port to become available (without draining LogStream).
func WaitForDirectExporterPort(port int) {
	Eventually(func() error {
		_, err := RunCmd("nc", "-z", "127.0.0.1", strconv.Itoa(port))
		return err
	}, 15*time.Second, 500*time.Millisecond).Should(Succeed(),
		"port %d did not become available", port)
}

// --- Debug helpers ---

// DumpControllerLogs prints the last N lines of controller/router logs.
func DumpControllerLogs(maxLines int) {
	ns := Namespace()
	tail := strconv.Itoa(maxLines)

	GinkgoWriter.Println("\n--- Controller logs ---")
	out, err := Kubectl("-n", ns, "logs", "-l", "component=controller", "--tail="+tail)
	if err != nil {
		out, _ = Kubectl("-n", ns, "logs", "-l", "control-plane=controller-manager", "--tail="+tail)
	}
	GinkgoWriter.Println(out)

	GinkgoWriter.Println("\n--- Router logs ---")
	out, err = Kubectl("-n", ns, "logs", "-l", "component=router", "--tail="+tail)
	if err != nil {
		out, _ = Kubectl("-n", ns, "logs", "-l", "control-plane=controller-router", "--tail="+tail)
	}
	GinkgoWriter.Println(out)
}

// --- Exporter config helpers ---

// MergeExporterConfig merges an overlay YAML into an exporter config file
// using native Go YAML parsing (no external yq dependency).
func MergeExporterConfig(exporterConfigPath, overlayFile string) {
	baseData, err := os.ReadFile(exporterConfigPath)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "reading exporter config %s", exporterConfigPath)

	overlayData, err := os.ReadFile(overlayFile)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "reading overlay %s", overlayFile)

	var base, overlay map[string]interface{}
	ExpectWithOffset(1, yaml.Unmarshal(baseData, &base)).To(Succeed())
	ExpectWithOffset(1, yaml.Unmarshal(overlayData, &overlay)).To(Succeed())

	if base == nil {
		base = make(map[string]interface{})
	}
	// Shallow merge: overlay keys overwrite base keys
	for k, v := range overlay {
		base[k] = v
	}

	merged, err := yaml.Marshal(base)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "marshalling merged config")
	ExpectWithOffset(1, os.WriteFile(exporterConfigPath, merged, 0644)).To(Succeed())
}

// ClearHooksConfig removes the hooks section from an exporter config
// using native Go YAML parsing.
func ClearHooksConfig(exporterConfigPath string) {
	data, err := os.ReadFile(exporterConfigPath)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "reading exporter config %s", exporterConfigPath)

	var doc map[string]interface{}
	ExpectWithOffset(1, yaml.Unmarshal(data, &doc)).To(Succeed())

	delete(doc, "hooks")

	out, err := yaml.Marshal(doc)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "marshalling config")
	ExpectWithOffset(1, os.WriteFile(exporterConfigPath, out, 0644)).To(Succeed())
}
