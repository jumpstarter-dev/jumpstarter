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
	"fmt"
	"path/filepath"
	"syscall"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

var _ = Describe("Direct Listener E2E Tests", Label("direct-listener"), Ordered, func() {
	var (
		tracker      *ProcessTracker
		listenerPort = 19090
		exporterDir  string
	)

	BeforeAll(func() {
		tracker = NewProcessTracker()
		exporterDir = filepath.Join(RepoRoot(), "e2e", "exporters")
	})

	AfterEach(func() {
		tracker.StopAll()

		// Wait for the listener port to be fully released before the next test.
		Eventually(func() error {
			_, err := RunCmd("nc", "-z", "127.0.0.1", fmt.Sprintf("%d", listenerPort))
			return err
		}, 10*time.Second, 500*time.Millisecond).Should(HaveOccurred(),
			"port %d should be closed after stopping exporter", listenerPort)
	})

	AfterAll(func() {
		tracker.Cleanup()
	})

	configPath := func(name string) string {
		return filepath.Join(exporterDir, name)
	}

	// --- Basic connectivity ---

	It("exporter starts and client can connect", func() {
		config := configPath("exporter-direct-listener.yaml")
		tracker.StartDirectExporter(config, listenerPort, "", false)
		WaitForDirectExporterReady(listenerPort, "")

		out, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--", "j", "power", "on")
		Expect(err).NotTo(HaveOccurred(), out)
	})

	It("client can call multiple driver methods", func() {
		config := configPath("exporter-direct-listener.yaml")
		tracker.StartDirectExporter(config, listenerPort, "", false)
		WaitForDirectExporterReady(listenerPort, "")

		out, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--", "j", "power", "on")
		Expect(err).NotTo(HaveOccurred(), out)

		out, err = Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--", "j", "power", "off")
		Expect(err).NotTo(HaveOccurred(), out)
	})

	It("client without --tls-grpc-insecure fails against insecure server", func() {
		config := configPath("exporter-direct-listener.yaml")
		tracker.StartDirectExporter(config, listenerPort, "", false)
		WaitForDirectExporterReady(listenerPort, "")

		_, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--", "j", "power", "on")
		Expect(err).To(HaveOccurred())
	})

	// --- Hooks in direct mode ---

	It("beforeLease hook executes and j commands work", func() {
		config := configPath("exporter-direct-hooks-before.yaml")
		tracker.StartDirectExporter(config, listenerPort, "", false)
		WaitForDirectExporterPort(listenerPort)

		out, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--exporter-logs", "--", "j", "power", "off")
		Expect(err).NotTo(HaveOccurred(), out)
		Expect(out).To(ContainSubstring("BEFORE_HOOK_DIRECT: executed"))
		Expect(out).To(ContainSubstring("BEFORE_HOOK_DIRECT: complete"))
	})

	It("afterLease hook runs on exporter shutdown", func() {
		config := configPath("exporter-direct-hooks-both.yaml")
		cmd, stderrBuf := tracker.StartDirectExporter(config, listenerPort, "", true)
		WaitForDirectExporterPort(listenerPort)

		out, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--exporter-logs", "--", "j", "power", "on")
		Expect(err).NotTo(HaveOccurred(), out)
		Expect(out).To(ContainSubstring("BEFORE_HOOK_DIRECT: executed"))

		// Stop the exporter (SIGTERM triggers _cleanup_after_lease).
		// Note: Kill() sends SIGKILL which does not allow cleanup hooks to run.
		if cmd.Process != nil {
			_ = cmd.Process.Signal(syscall.SIGTERM)
			_, _ = cmd.Process.Wait()
		}

		// afterLease hook output should appear in the exporter's stderr buffer
		Eventually(func() string {
			if stderrBuf == nil {
				return ""
			}
			return stderrBuf.String()
		}, 10*time.Second, 500*time.Millisecond).Should(ContainSubstring("AFTER_HOOK_DIRECT: executed"))
	})

	// --- Passphrase authentication ---

	It("correct passphrase connects", func() {
		config := configPath("exporter-direct-listener.yaml")
		tracker.StartDirectExporter(config, listenerPort, "my-secret", false)
		WaitForDirectExporterReady(listenerPort, "my-secret")

		out, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--passphrase", "my-secret", "--", "j", "power", "on")
		Expect(err).NotTo(HaveOccurred(), out)
	})

	It("wrong passphrase is rejected", func() {
		config := configPath("exporter-direct-listener.yaml")
		tracker.StartDirectExporter(config, listenerPort, "my-secret", false)
		WaitForDirectExporterReady(listenerPort, "my-secret")

		_, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--passphrase", "wrong", "--", "j", "power", "on")
		Expect(err).To(HaveOccurred())
	})

	It("missing passphrase is rejected", func() {
		config := configPath("exporter-direct-listener.yaml")
		tracker.StartDirectExporter(config, listenerPort, "my-secret", false)
		WaitForDirectExporterReady(listenerPort, "my-secret")

		_, err := Jmp("shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--", "j", "power", "on")
		Expect(err).To(HaveOccurred())
	})
})
