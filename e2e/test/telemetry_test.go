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
	"encoding/json"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

// Telemetry cannot look up Exporters or Clients, so it accepts only the token
// the controller issues from GetServiceEndpoints. These specs log an exporter
// and a client in through dex, whose credentials telemetry never sees, and
// check that the exporter's metrics and both sides' logs reach telemetry.
//
// Not labelled compat: older controllers issue no telemetry token.
var _ = Describe("Telemetry E2E Tests", Label("telemetry"), Ordered, func() {
	const (
		exporterName = "test-exporter-telemetry"
		clientName   = "test-client-telemetry"
		selector     = "example.com/board=telemetry"

		timeout = 2 * time.Minute
		poll    = 2 * time.Second
	)

	var (
		tracker *ProcessTracker
		since   string
	)

	telemetryLogs := func(args ...string) string {
		out, _ := Kubectl(append([]string{"-n", Namespace(), "logs", "-l", "app=jumpstarter-telemetry"}, args...)...)
		return out
	}

	// metrics returns what Prometheus would scrape from telemetry.
	metrics := func() string {
		pod, _ := Kubectl("-n", Namespace(), "get", "pods", "-l", "app=jumpstarter-telemetry",
			"-o", "jsonpath={.items[0].metadata.name}")
		out, _ := Kubectl("get", "--raw",
			fmt.Sprintf("/api/v1/namespaces/%s/pods/%s:8080/proxy/metrics", Namespace(), strings.TrimSpace(pod)))
		return out
	}

	// pushedLogs returns the PushLogs entries telemetry wrote since the lane
	// started. Without a Loki URL, telemetry writes them to stdout.
	pushedLogs := func() []map[string]any {
		var entries []map[string]any
		for _, line := range strings.Split(telemetryLogs("--since-time="+since, "--tail=-1"), "\n") {
			var entry map[string]any
			if json.Unmarshal([]byte(line), &entry) == nil && entry["component"] != nil {
				entries = append(entries, entry)
			}
		}
		return entries
	}

	BeforeAll(func() {
		tracker = NewProcessTracker()
		since = time.Now().Add(-2 * time.Second).UTC().Format(time.RFC3339)

		CreateOIDCClient(clientName)
		CreateOIDCExporter(exporterName, selector)
		LoginOIDCClient(clientName)
		LoginOIDCExporter(exporterName)
		MergeExporterConfig(SystemExporterConfigPath(exporterName),
			filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml"))

		tracker.StartExporterLoop(exporterName)
		WaitForExporter(exporterName)

		// One lease for both specs: the power-on produces the metric, and the
		// shell pushes the client's logs.
		out, err := Jmp("shell", "--client", clientName, "--selector", selector, "j", "power", "on")
		Expect(err).NotTo(HaveOccurred(), out)
	})

	AfterAll(func() {
		tracker.StopAll()
		DeleteClient(clientName)
		DeleteExporter(exporterName)
		tracker.Cleanup()
	})

	AfterEach(func() {
		DumpOnFailure(250, tracker.DumpLogs, func(maxLines int) {
			GinkgoWriter.Println("\n--- Telemetry logs ---")
			GinkgoWriter.Println(telemetryLogs("--tail=" + strconv.Itoa(maxLines)))
		})
	})

	It("reports metrics from an exporter logged in through dex", func() {
		Eventually(metrics, timeout, poll).Should(ContainSubstring(
			`jumpstarter_operation_duration_seconds_count{driver_type="power",exporter="`+exporterName+
				`",operation="on",result="success"}`),
			"telemetry scrapes no power-on metric from the exporter")
	})

	It("receives logs from that exporter and from jmp shell", func() {
		Eventually(pushedLogs, timeout, poll).Should(SatisfyAll(
			ContainElement(SatisfyAll(HaveKeyWithValue("component", "exporter"), HaveKeyWithValue("exporter", exporterName))),
			ContainElement(SatisfyAll(HaveKeyWithValue("component", "cli"), HaveKeyWithValue("client", clientName))),
		))
	})
})
