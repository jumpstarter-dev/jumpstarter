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

// Package e2e provides utilities and test suites for Jumpstarter E2E testing.
package e2e

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive // ginkgo DSL
	. "github.com/onsi/gomega"    //nolint:revive // gomega DSL
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
// JS_NAMESPACE, then the default "jumpstarter-lab").
func Namespace() string {
	if ns := os.Getenv("E2E_TEST_NS"); ns != "" {
		return ns
	}
	if ns := os.Getenv("JS_NAMESPACE"); ns != "" {
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

// Method returns the deployment method from the METHOD env var (operator or helm).
func Method() string {
	return os.Getenv("METHOD")
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
func OldJmp() string {
	return os.Getenv("OLD_JMP")
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

// Yq runs the yq tool (via go run) and returns stdout only.
// Stderr is discarded to avoid Go toolchain messages contaminating output.
func Yq(args ...string) (string, error) {
	goArgs := append([]string{"run", "github.com/mikefarah/yq/v4@latest"}, args...)
	stdout, stderr, err := RunCmdSplit("go", goArgs...)
	if err != nil {
		return stdout + "\n" + stderr, err
	}
	return stdout, nil
}

// MustYq runs the yq tool and fails the test on error.
func MustYq(args ...string) string {
	out, err := Yq(args...)
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "yq %v failed: %s", args, out)
	return out
}

// --- Process management ---

// ProcessTracker manages background exporter processes.
type ProcessTracker struct {
	pids    []int
	logsDir string
}

// NewProcessTracker creates a new ProcessTracker with a temp log directory.
func NewProcessTracker() *ProcessTracker {
	dir, err := os.MkdirTemp("", "jumpstarter-e2e-logs-")
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	return &ProcessTracker{logsDir: dir}
}

// StartExporterLoop starts an exporter in a restart loop (bash wrapper)
// and tracks the wrapper PID.
func (pt *ProcessTracker) StartExporterLoop(exporterName string, jmpBin ...string) {
	jmp := "jmp"
	if len(jmpBin) > 0 && jmpBin[0] != "" {
		jmp = jmpBin[0]
	}
	logFile := filepath.Join(pt.logsDir, exporterName+".log")

	script := fmt.Sprintf(`while true; do %s run --exporter %s >> %s 2>&1; sleep 2; done`,
		jmp, exporterName, logFile)
	cmd := exec.Command("bash", "-c", script)
	// Detach from parent process group so signals aren't forwarded
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	err := cmd.Start()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "failed to start exporter loop for %s", exporterName)
	pt.pids = append(pt.pids, cmd.Process.Pid)
	GinkgoWriter.Printf("Started exporter loop for %s (PID %d)\n", exporterName, cmd.Process.Pid)
}

// StartExporterSingle starts an exporter once (no restart loop) and tracks the PID.
func (pt *ProcessTracker) StartExporterSingle(exporterName string) *exec.Cmd {
	cmd := exec.Command("jmp", "run", "--exporter", exporterName)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	err := cmd.Start()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "failed to start exporter %s", exporterName)
	pt.pids = append(pt.pids, cmd.Process.Pid)
	GinkgoWriter.Printf("Started exporter %s (PID %d)\n", exporterName, cmd.Process.Pid)
	return cmd
}

// StartDirectExporter starts an exporter with --tls-grpc-listener (direct mode).
func (pt *ProcessTracker) StartDirectExporter(configFile string, port int, passphrase string, captureStderr bool) (*exec.Cmd, string) {
	args := []string{"run", "--exporter-config", configFile,
		"--tls-grpc-listener", strconv.Itoa(port),
		"--tls-grpc-insecure"}
	if passphrase != "" {
		args = append(args, "--passphrase", passphrase)
	}

	cmd := exec.Command("jmp", args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	var stderrFile string
	if captureStderr {
		stderrFile = filepath.Join(pt.logsDir, "direct-exporter-stderr.log")
		f, err := os.Create(stderrFile)
		ExpectWithOffset(1, err).NotTo(HaveOccurred())
		cmd.Stderr = f
	}

	err := cmd.Start()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), "failed to start direct exporter with config %s", configFile)
	pt.pids = append(pt.pids, cmd.Process.Pid)
	GinkgoWriter.Printf("Started direct exporter (PID %d) on port %d\n", cmd.Process.Pid, port)
	return cmd, stderrFile
}

// WriteLogMarker writes a marker into all exporter log files for correlation.
func (pt *ProcessTracker) WriteLogMarker(testName string) {
	marker := fmt.Sprintf("=== TEST START: %s @ %s ===\n", testName, time.Now().Format(time.RFC3339))
	entries, err := os.ReadDir(pt.logsDir)
	if err != nil {
		return
	}
	for _, e := range entries {
		if !e.IsDir() {
			f, err := os.OpenFile(filepath.Join(pt.logsDir, e.Name()), os.O_APPEND|os.O_WRONLY, 0644)
			if err == nil {
				_, _ = f.WriteString(marker)
				f.Close()
			}
		}
	}
}

// DumpLogs prints the last N lines of log files (for debugging failures).
func (pt *ProcessTracker) DumpLogs(maxLines int) {
	entries, err := os.ReadDir(pt.logsDir)
	if err != nil {
		return
	}
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		GinkgoWriter.Printf("\n--- Exporter logs (%s) ---\n", e.Name())
		data, err := os.ReadFile(filepath.Join(pt.logsDir, e.Name()))
		if err != nil {
			GinkgoWriter.Printf("(error reading: %v)\n", err)
			continue
		}
		lines := strings.Split(string(data), "\n")
		start := 0
		if len(lines) > maxLines {
			start = len(lines) - maxLines
		}
		for _, line := range lines[start:] {
			GinkgoWriter.Println(line)
		}
	}
}

// StopAll kills all tracked processes and any orphans matching the pattern.
func (pt *ProcessTracker) StopAll() {
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

// Cleanup stops all processes and removes temp directories.
func (pt *ProcessTracker) Cleanup() {
	pt.StopAll()
	if pt.logsDir != "" {
		os.RemoveAll(pt.logsDir)
	}
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

// MergeExporterConfig merges an overlay YAML into an exporter config file.
func MergeExporterConfig(exporterConfigPath, overlayFile string) {
	MustYq("-i", fmt.Sprintf(". * load(\"%s\")", overlayFile), exporterConfigPath)
}

// ClearHooksConfig removes the hooks section from an exporter config.
func ClearHooksConfig(exporterConfigPath string) {
	MustYq("-i", "del(.hooks)", exporterConfigPath)
}
