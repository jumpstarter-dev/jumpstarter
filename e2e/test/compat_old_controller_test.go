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

var _ = Describe("Compat: Old Controller E2E Tests", Label("compat", "old-controller"), Ordered, func() {
	var (
		tracker *ProcessTracker
		ns      string
	)

	waitForCompatExporter := func() {
		WaitForExporter("compat-exporter")
	}

	BeforeAll(func() {
		tracker = NewProcessTracker()
		ns = Namespace()
	})

	AfterAll(func() {
		if tracker != nil {
			tracker.StopAll()
		}

		_ = exec.Command("pkill", "-9", "-f", "jmp run --exporter compat-").Run()

		_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-client", "--delete")
		_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-exporter", "--delete")
		_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-client-wait", "--delete")
		_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-exporter-wait", "--delete")

		if tracker != nil {
			tracker.Cleanup()
		}
	})

	// --- Core compatibility ---
	Context("Core compatibility", func() {
		It("can create client with admin cli", func() {
			out, err := Jmp("admin", "create", "client", "-n", ns, "compat-client",
				"--unsafe", "--save")
			Expect(err).NotTo(HaveOccurred(), out)
		})

		It("can create exporter with admin cli", func() {
			out, err := Jmp("admin", "create", "exporter", "-n", ns, "compat-exporter",
				"--save", "--label", "example.com/board=compat")
			Expect(err).NotTo(HaveOccurred(), out)

			overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml")
			MergeExporterConfig("/etc/jumpstarter/exporters/compat-exporter.yaml", overlayPath)
		})

		It("new exporter registers with old controller", func() {
			tracker.StartExporterLoop("compat-exporter")
			waitForCompatExporter()
		})

		It("exporter shows as Online and Registered", func() {
			waitForCompatExporter()

			out := MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Online")].status}`)
			Expect(out).To(Equal("True"))

			out = MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Registered")].status}`)
			Expect(out).To(Equal("True"))
		})

		It("new client can lease and connect through old controller", func() {
			waitForCompatExporter()
			out, err := Jmp("shell", "--client", "compat-client",
				"--selector", "example.com/board=compat", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
		})

		It("can operate on leases through old controller", func() {
			waitForCompatExporter()

			MustJmp("config", "client", "use", "compat-client")
			MustJmp("create", "lease", "--selector", "example.com/board=compat", "--duration", "1d")
			MustJmp("get", "leases")
			MustJmp("get", "exporters")

			out, err := Jmp("get", "leases", "--selector", "example.com/board=compat", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("example.com/board=compat"))

			out, err = Jmp("get", "leases", "--selector", "example.com/board=doesnotexist")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(Equal("No resources found."))

			MustJmp("delete", "leases", "--all")
		})

		It("exporter stays Online after lease cycle", func() {
			waitForCompatExporter()

			out := MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Online")].status}`)
			Expect(out).To(Equal("True"))

			out = MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/compat-exporter",
				"-o", `jsonpath={.status.conditions[?(@.type=="Registered")].status}`)
			Expect(out).To(Equal("True"))
		})
	})

	// --- Client started before exporter ---
	Context("Client started before exporter", func() {
		It("client started before exporter connects", func() {
			_ = exec.Command("pkill", "-9", "-f", "jmp run --exporter compat-exporter-wait").Run()
			time.Sleep(3 * time.Second)

			_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-exporter-wait", "--delete")
			_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-client-wait", "--delete")

			out, err := Jmp("admin", "create", "client", "-n", ns, "compat-client-wait",
				"--unsafe", "--save")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("admin", "create", "exporter", "-n", ns, "compat-exporter-wait",
				"--save", "--label", "example.com/board=compat-wait")
			Expect(err).NotTo(HaveOccurred(), out)

			overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml")
			MergeExporterConfig("/etc/jumpstarter/exporters/compat-exporter-wait.yaml", overlayPath)

			// Start client BEFORE exporter
			clientCmd := JmpCmd("shell", "--client", "compat-client-wait",
				"--selector", "example.com/board=compat-wait", "j", "power", "on")
			clientCmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			err = clientCmd.Start()
			Expect(err).NotTo(HaveOccurred())

			time.Sleep(5 * time.Second)
			Expect(clientCmd.ProcessState).To(BeNil(), "Client exited before exporter was started")

			// Now start the exporter
			tracker.StartExporterLoop("compat-exporter-wait")

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
			_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-client", "--delete")
			_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-exporter", "--delete")
			_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "compat-client-wait", "--delete")
			_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "compat-exporter-wait", "--delete")
		})
	})
})
