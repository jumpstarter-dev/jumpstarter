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
	"path/filepath"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

var _ = Describe("Exit On Lease End E2E Tests", Label("exit-on-lease-end"), Ordered, func() {
	var (
		tracker            *ProcessTracker
		ns                 string
		exporterConfigPath string
	)

	BeforeAll(func() {
		tracker = NewProcessTracker()
		ns = Namespace()
		exporterConfigPath = SystemExporterConfigPath("test-exporter-exit-on-lease-end")

		// Create client and exporter using legacy (token) auth — no OIDC/dex dependency.
		MustJmp("admin", "create", "client", "-n", ns, "test-client-exit-on-lease-end",
			"--unsafe", "--save")

		MustJmp("admin", "create", "exporter", "-n", ns, "test-exporter-exit-on-lease-end",
			"--out", exporterConfigPath,
			"--label", "example.com/board=exit-on-lease-end")

		// Merge the base exporter drivers + exitOnLeaseEnd overlay
		overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter-exit-on-lease-end.yaml")
		MergeExporterConfig(exporterConfigPath, overlayPath)
	})

	AfterAll(func() {
		tracker.StopAll()

		// Clean up CRDs
		_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "test-client-exit-on-lease-end", "--delete")
		_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "test-exporter-exit-on-lease-end", "--delete")

		tracker.Cleanup()
	})

	BeforeEach(func() {
		tracker.WriteLogMarker(CurrentSpecReport().FullText())
	})

	AfterEach(func() {
		if CurrentSpecReport().Failed() {
			tracker.DumpLogs(250)
			DumpControllerLogs(250)
		}
		// Stop any running exporter between tests
		tracker.StopAll()
		time.Sleep(time.Second)
	})

	It("exporter exits after serving one lease", func() {
		// Start the exporter in single mode (no restart loop) since we
		// expect the process to exit on its own after the lease ends.
		tracker.StartExporterSingle("test-exporter-exit-on-lease-end")
		WaitForExporter("test-exporter-exit-on-lease-end")

		// Run a short-lived shell session that creates a lease, runs a
		// command, then exits — releasing the lease.
		out, err := Jmp("shell", "--client", "test-client-exit-on-lease-end",
			"--selector", "example.com/board=exit-on-lease-end", "j", "power", "on")
		Expect(err).NotTo(HaveOccurred(), out)

		// The exporter should exit after the lease ends.
		Eventually(func() bool {
			return tracker.IsProcessRunning()
		}, 60*time.Second, 1*time.Second).Should(BeFalse(),
			"exporter process should have exited after lease ended")

		// Verify the exporter goes offline in the controller.
		WaitForExporterOffline("test-exporter-exit-on-lease-end")
	})

	It("exporter does not exit before any lease is served", func() {
		// Start the exporter and verify it stays alive without any lease.
		tracker.StartExporterSingle("test-exporter-exit-on-lease-end")
		WaitForExporter("test-exporter-exit-on-lease-end")

		// Wait some time to make sure the exporter stays running without
		// exiting prematurely (no lease has been served yet).
		Consistently(func() bool {
			return tracker.IsProcessRunning()
		}, 10*time.Second, 1*time.Second).Should(BeTrue(),
			"exporter should remain running before any lease is served")
	})

	It("exporter serves exactly one lease then exits and goes offline", func() {
		// Serve a single lease and verify that the exporter process
		// terminates and the controller marks it offline.
		tracker.StartExporterSingle("test-exporter-exit-on-lease-end")
		WaitForExporter("test-exporter-exit-on-lease-end")

		out, err := Jmp("shell", "--client", "test-client-exit-on-lease-end",
			"--selector", "example.com/board=exit-on-lease-end", "j", "power", "on")
		Expect(err).NotTo(HaveOccurred(), out)

		// The exporter should exit after the lease ends.
		Eventually(func() bool {
			return tracker.IsProcessRunning()
		}, 60*time.Second, 1*time.Second).Should(BeFalse(),
			"exporter process should have exited after lease ended")

		// Verify the controller reflects the exporter as offline.
		WaitForExporterOffline("test-exporter-exit-on-lease-end")
	})
})
