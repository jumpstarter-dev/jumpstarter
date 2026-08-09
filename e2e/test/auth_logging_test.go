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

package e2e

import (
	"os"
	"path/filepath"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

// These specs verify the controller logs authentication failures (issue #811):
// a request presenting a corrupted token must produce a "client authentication
// failed" / "exporter authentication failed" log line that includes the peer
// address and never leaks the token value.
//
// The container is self-contained: it provisions its own client and exporter
// with legacy (controller-issued) tokens so the token lives in a local config
// file where the test can corrupt it. It is intentionally NOT labelled for the
// compat suites — old controller images do not have auth-failure logging.
var _ = Describe("Auth Failure Logging E2E Tests", Label("auth-logging"), Ordered, func() {
	const (
		clientName   = "test-client-authlog"
		exporterName = "test-exporter-authlog"
		// JWT-shaped but invalid. The distinctive payload marker lets the
		// specs assert the token value never shows up in controller logs.
		corruptedToken = "eyJhbGciOiJIUzI1NiJ9.e2e-corrupted-token-payload.e2e-bad-signature"
		tokenMarker    = "e2e-corrupted-token-payload"

		logsTimeout = 2 * time.Minute
		logsPoll    = 2 * time.Second
	)

	var (
		tracker            *ProcessTracker
		ns                 string
		tmpDir             string
		exporterConfigPath string
	)

	// sinceNow returns an RFC3339 timestamp slightly in the past, used with
	// kubectl --since-time to scope log assertions to the current spec.
	sinceNow := func() string {
		return time.Now().Add(-2 * time.Second).UTC().Format(time.RFC3339)
	}

	// logLine returns the first log line containing substr.
	logLine := func(logs, substr string) string {
		for _, line := range strings.Split(logs, "\n") {
			if strings.Contains(line, substr) {
				return line
			}
		}
		return ""
	}

	BeforeAll(func() {
		tracker = NewProcessTracker()
		ns = Namespace()

		var err error
		tmpDir, err = os.MkdirTemp("", "jmp-e2e-authlog-*")
		Expect(err).NotTo(HaveOccurred())
		exporterConfigPath = filepath.Join(tmpDir, exporterName+".yaml")

		MustJmp("admin", "create", "client", "-n", ns, clientName,
			"--unsafe", "--save")

		MustJmp("admin", "create", "exporter", "-n", ns, exporterName,
			"--out", exporterConfigPath,
			"--label", "example.com/board=authlog")

		// Give the exporter mock drivers so `jmp run` passes config
		// validation and reaches the controller registration step.
		overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml")
		MergeExporterConfig(exporterConfigPath, overlayPath)
	})

	AfterAll(func() {
		tracker.StopAll()
		_, _ = Jmp("admin", "delete", "client", "--namespace", ns, clientName, "--delete")
		_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, exporterName, "--delete")
		tracker.Cleanup()
		if tmpDir != "" {
			_ = os.RemoveAll(tmpDir)
		}
	})

	BeforeEach(func() {
		tracker.WriteLogMarker(CurrentSpecReport().FullText())
	})

	AfterEach(func() {
		if CurrentSpecReport().Failed() {
			tracker.DumpLogs(250)
			DumpControllerLogs(250)
		}
	})

	It("controller logs client authentication failures with the peer address", func() {
		configFile := filepath.Join(os.Getenv("HOME"), ".config", "jumpstarter", "clients", clientName+".yaml")
		Expect(configFile).To(BeAnExistingFile())
		SetYAMLField(configFile, "token", corruptedToken)

		since := sinceNow()

		out, err := Jmp("get", "exporters", "--client", clientName)
		Expect(err).To(HaveOccurred(),
			"expected the controller to reject the corrupted client token, got: %s", out)

		var logs string
		Eventually(func() string {
			logs = ControllerLogsSince(since)
			return logs
		}, logsTimeout, logsPoll).Should(ContainSubstring("client authentication failed"),
			"controller should log the client auth failure")

		line := logLine(logs, "client authentication failed")
		Expect(line).To(ContainSubstring(`"peer"`),
			"auth failure log line should include the peer address, got: %s", line)
		Expect(logs).NotTo(ContainSubstring(tokenMarker),
			"the token value must never appear in controller logs")
	})

	It("controller logs exporter authentication failures with the peer address", func() {
		SetYAMLField(exporterConfigPath, "token", corruptedToken)

		since := sinceNow()

		// Start the exporter once; its registration must be rejected by the
		// controller. The assertion is the controller-side log line, so the
		// spec does not depend on how the exporter process handles the
		// failure (exit vs retry) — StopAll cleans it up either way.
		tracker.StartExporterWithConfig(exporterName, exporterConfigPath)

		var logs string
		Eventually(func() string {
			logs = ControllerLogsSince(since)
			return logs
		}, logsTimeout, logsPoll).Should(ContainSubstring("exporter authentication failed"),
			"controller should log the exporter auth failure")

		line := logLine(logs, "exporter authentication failed")
		Expect(line).To(ContainSubstring(`"peer"`),
			"auth failure log line should include the peer address, got: %s", line)
		Expect(logs).NotTo(ContainSubstring(tokenMarker),
			"the token value must never appear in controller logs")
	})
})
