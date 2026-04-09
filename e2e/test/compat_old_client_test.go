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
	"os/exec"
	"path/filepath"
	"syscall"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

var _ = Describe("Compat: Old Client E2E Tests", Label("compat", "old-client"), Ordered, func() {
	var (
		tracker    *ProcessTracker
		ns         string
		oldJmpPath string
	)

	waitForCompatExporter := func() {
		time.Sleep(exporterPostDelay)
		MustKubectl("-n", ns, "wait", "--timeout", "5m",
			"--for=condition=Online", "--for=condition=Registered",
			"exporters.jumpstarter.dev/compat-old-exporter")
	}

	BeforeAll(func() {
		oldJmpPath = OldJmpPath()
		if oldJmpPath == "" {
			Skip("PYTHON_OLD_VENV not set or no jmp/j binary found; skipping old-client compat tests")
		}

		tracker = NewProcessTracker()
		ns = Namespace()
	})

	AfterAll(func() {
		if tracker != nil {
			tracker.StopAll()
		}

		// Kill orphaned processes
		_ = exec.Command("pkill", "-9", "-f", "jmp run --exporter compat-old-").Run()

		// Clean up CRDs
		_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-old-client", "--delete")
		_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-old-exporter", "--delete")
		_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-old-client-wait", "--delete")
		_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-old-exporter-wait", "--delete")

		if tracker != nil {
			tracker.Cleanup()
		}
	})

	// --- Setup ---
	Context("Setup", func() {
		It("creates resources", func() {
			out, err := Jmp("admin", "create", "client", "-n", ns, "compat-old-client",
				"--unsafe", "--save")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("admin", "create", "exporter", "-n", ns, "compat-old-exporter",
				"--save", "--label", "example.com/board=compat-old")
			Expect(err).NotTo(HaveOccurred(), out)

			overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml")
			MergeExporterConfig("/etc/jumpstarter/exporters/compat-old-exporter.yaml", overlayPath)
		})
	})

	// --- Registration ---
	Context("Old exporter registration", func() {
		It("old exporter registers with new controller", func() {
			tracker.StartExporterLoop("compat-old-exporter", oldJmpPath)
			waitForCompatExporter()
		})

		It("old exporter shows as Online (not incorrectly offline)", func() {
			waitForCompatExporter()

			out := MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-old-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Online")].status}`)
			Expect(out).To(Equal("True"))

			out = MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-old-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Registered")].status}`)
			Expect(out).To(Equal("True"))
		})
	})

	// --- Lease cycles ---
	Context("Lease cycles", func() {
		It("old client can connect through new controller", func() {
			waitForCompatExporter()
			out, err := OldJmp("shell", "--client", "compat-old-client",
				"--selector", "example.com/board=compat-old", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
		})

		It("old exporter stays Online after lease completes", func() {
			waitForCompatExporter()
			out := MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-old-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Online")].status}`)
			Expect(out).To(Equal("True"))
		})

		It("new client can connect to old exporter", func() {
			waitForCompatExporter()
			out, err := Jmp("shell", "--client", "compat-old-client",
				"--selector", "example.com/board=compat-old", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
		})

		It("old exporter still Online after multiple lease cycles", func() {
			waitForCompatExporter()

			out := MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-old-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Online")].status}`)
			Expect(out).To(Equal("True"))

			out = MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-old-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Registered")].status}`)
			Expect(out).To(Equal("True"))
		})
	})

	// --- Reconnect after offline ---
	Context("Reconnect after offline", func() {
		It("stop exporter and wait for offline", func() {
			tracker.StopAll()
			_ = exec.Command("pkill", "-9", "-f", "jmp run --exporter compat-old-exporter$").Run()

			MustKubectl("-n", ns, "wait", "--timeout", "5m",
				"--for=condition=Online=False",
				"exporters.jumpstarter.dev/compat-old-exporter")
		})

		It("old exporter recovers Online after reconnect", func() {
			tracker.StartExporterLoop("compat-old-exporter", oldJmpPath)
			waitForCompatExporter()
		})

		It("lease works after reconnect", func() {
			waitForCompatExporter()
			out, err := OldJmp("shell", "--client", "compat-old-client",
				"--selector", "example.com/board=compat-old", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
		})
	})

	// --- Client started before exporter ---
	Context("Client started before exporter", func() {
		It("client started before exporter connects", func() {
			_ = exec.Command("pkill", "-9", "-f", "jmp run --exporter compat-old-exporter-wait").Run()
			time.Sleep(3 * time.Second)

			// Clean up from previous runs
			_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-old-exporter-wait", "--delete")
			_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-old-client-wait", "--delete")

			out, err := Jmp("admin", "create", "client", "-n", ns, "compat-old-client-wait",
				"--unsafe", "--save")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("admin", "create", "exporter", "-n", ns, "compat-old-exporter-wait",
				"--save", "--label", "example.com/board=compat-old-wait")
			Expect(err).NotTo(HaveOccurred(), out)

			overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml")
			MergeExporterConfig("/etc/jumpstarter/exporters/compat-old-exporter-wait.yaml", overlayPath)

			// Start client BEFORE exporter
			clientCmd := JmpCmd("shell", "--client", "compat-old-client-wait",
				"--selector", "example.com/board=compat-old-wait", "j", "power", "on")
			clientCmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			err = clientCmd.Start()
			Expect(err).NotTo(HaveOccurred())

			// Wait to ensure client is actively waiting
			time.Sleep(5 * time.Second)
			Expect(clientCmd.ProcessState).To(BeNil(), "Client exited before exporter was started")

			// Now start the old exporter
			tracker.StartExporterLoop("compat-old-exporter-wait", oldJmpPath)

			// Wait for client to complete (with timeout)
			done := make(chan error, 1)
			go func() { done <- clientCmd.Wait() }()

			select {
			case err := <-done:
				Expect(err).NotTo(HaveOccurred(), "client shell failed")
			case <-time.After(120 * time.Second):
				_ = clientCmd.Process.Kill()
				Fail("Client shell timed out waiting for exporter (120s)")
			}
		})
	})

	// --- Cleanup ---
	Context("Cleanup", func() {
		It("cleans up resources", func() {
			_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-old-client", "--delete")
			_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-old-exporter", "--delete")
			_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-old-client-wait", "--delete")
			_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-old-exporter-wait", "--delete")
		})
	})
})
