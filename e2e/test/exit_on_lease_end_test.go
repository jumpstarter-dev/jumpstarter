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
		exporterConfigPath string
	)

	BeforeAll(func() {
		tracker = NewProcessTracker()
		exporterConfigPath = SystemExporterConfigPath("test-exporter-exit-on-lease-end")

		// Legacy (token) auth — no OIDC/dex dependency.
		CreateLegacyClient("test-client-exit-on-lease-end")
		CreateLegacyExporter("test-exporter-exit-on-lease-end", exporterConfigPath,
			"example.com/board=exit-on-lease-end")

		// Merge the base exporter drivers + exitOnLeaseEnd overlay
		overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter-exit-on-lease-end.yaml")
		MergeExporterConfig(exporterConfigPath, overlayPath)
	})

	AfterAll(func() {
		tracker.StopAll()

		DeleteClient("test-client-exit-on-lease-end")
		DeleteExporter("test-exporter-exit-on-lease-end")

		tracker.Cleanup()
	})

	BeforeEach(func() {
		tracker.WriteLogMarker(CurrentSpecReport().FullText())
	})

	AfterEach(func() {
		DumpOnFailure(250, tracker.DumpLogs)
		if CurrentSpecReport().Failed() {
			DumpControllerLogs(250)
		}
		// Stop any running exporter between tests and wait until it is gone,
		// then wait for the controller to process the disconnection.
		tracker.StopAll()
		Eventually(func() bool {
			return tracker.IsProcessRunning()
		}, 10*time.Second, 100*time.Millisecond).Should(BeFalse(),
			"exporter process should stop after StopAll")
		WaitForExporterOffline("test-exporter-exit-on-lease-end")
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
})
