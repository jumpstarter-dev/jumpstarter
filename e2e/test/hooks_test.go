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

package e2e

import (
	"os"
	"path/filepath"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

var _ = Describe("Hooks E2E Tests", Label("hooks"), Ordered, func() {
	var (
		tracker            *ProcessTracker
		ns                 string
		exporterConfigPath string
	)

	exporterOverlay := func(configFile string) string {
		return filepath.Join(RepoRoot(), "e2e", "exporters", configFile)
	}

	// startHooksExporter stops the previous exporter, applies the config overlay,
	// and starts the exporter in a restart loop.
	startHooksExporter := func(configFile string) {
		tracker.StopAll()
		time.Sleep(time.Second)

		ClearHooksConfig(exporterConfigPath)
		MergeExporterConfig(exporterConfigPath, exporterOverlay(configFile))

		tracker.StartExporterLoop("test-exporter-hooks")
		WaitForExporter("test-exporter-hooks")
	}

	// startHooksExporterSingle starts without a restart loop (for exit-mode tests).
	startHooksExporterSingle := func(configFile string) {
		tracker.StopAll()
		time.Sleep(time.Second)

		ClearHooksConfig(exporterConfigPath)
		MergeExporterConfig(exporterConfigPath, exporterOverlay(configFile))

		tracker.StartExporterSingle("test-exporter-hooks")
		WaitForExporter("test-exporter-hooks")
	}

	BeforeAll(func() {
		tracker = NewProcessTracker()
		ns = Namespace()
		exporterConfigPath = "/etc/jumpstarter/exporters/test-exporter-hooks.yaml"

		// Create client and exporter for hooks tests
		MustJmp("admin", "create", "client", "-n", ns, "test-client-hooks",
			"--unsafe", "--nointeractive", "--oidc-username", "dex:test-client-hooks")

		MustJmp("admin", "create", "exporter", "-n", ns, "test-exporter-hooks",
			"--nointeractive", "--oidc-username", "dex:test-exporter-hooks",
			"--label", "example.com/board=hooks")

		MustJmp("login", "--client", "test-client-hooks",
			"--endpoint", Endpoint(), "--namespace", ns, "--name", "test-client-hooks",
			"--issuer", "https://dex.dex.svc.cluster.local:5556",
			"--username", "test-client-hooks@example.com", "--password", "password", "--unsafe")

		MustJmp("login", "--exporter", "test-exporter-hooks",
			"--endpoint", Endpoint(), "--namespace", ns, "--name", "test-exporter-hooks",
			"--issuer", "https://dex.dex.svc.cluster.local:5556",
			"--username", "test-exporter-hooks@example.com", "--password", "password")
	})

	AfterAll(func() {
		tracker.StopAll()

		// Clean up CRDs
		_, _ = Jmp("admin", "delete", "client", "--namespace", ns, "test-client-hooks", "--delete")
		_, _ = Jmp("admin", "delete", "exporter", "--namespace", ns, "test-exporter-hooks", "--delete")

		tracker.Cleanup()
	})

	AfterEach(func() {
		// Clean up temp files that may leak if assertions fail
		os.Remove("/tmp/jumpstarter-e2e-hook-python.py")
		os.Remove("/tmp/jumpstarter-e2e-hook-script.sh")
	})

	// ====================================================================
	// Group A: Basic Hook Execution
	// ====================================================================
	Context("Group A: Basic Hook Execution", func() {
		It("A1: beforeLease hook executes", func() {
			startHooksExporter("exporter-hooks-before-only.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("BEFORE_HOOK_MARKER: executed"))
		})

		It("A2: afterLease hook executes", func() {
			startHooksExporter("exporter-hooks-after-only.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("AFTER_HOOK_MARKER: executed"))
		})

		It("A3: both hooks execute in correct order", func() {
			startHooksExporter("exporter-hooks-both.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("BEFORE_HOOK:"))
			Expect(out).To(ContainSubstring("AFTER_HOOK:"))

			// Verify order: BEFORE should appear before AFTER in output
			beforeIdx := strings.Index(out, "BEFORE_HOOK:")
			afterIdx := strings.Index(out, "AFTER_HOOK:")
			Expect(beforeIdx).To(BeNumerically("<", afterIdx))
		})
	})

	// ====================================================================
	// Group B: beforeLease Failure Modes
	// ====================================================================
	Context("Group B: beforeLease Failure Modes", func() {
		It("B1: beforeLease onFailure=warn allows shell to proceed", func() {
			startHooksExporter("exporter-hooks-before-fail-warn.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("HOOK_FAIL_WARN"))

			WaitForExporter("test-exporter-hooks")
		})

		It("B2: beforeLease onFailure=endLease fails shell", func() {
			startHooksExporter("exporter-hooks-before-fail-endLease.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).To(HaveOccurred())
			Expect(out).To(MatchRegexp(`(beforeLease hook fail|Exporter shutting down|Connection to exporter lost)`))

			WaitForExporter("test-exporter-hooks")
		})

		It("B3: beforeLease onFailure=exit shuts down exporter", func() {
			startHooksExporterSingle("exporter-hooks-before-fail-exit.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).To(HaveOccurred())
			Expect(out).To(MatchRegexp(`(beforeLease hook fail|Exporter shutting down|Connection to exporter lost)`))

			// Exporter process should have exited (allow extra time on slower runners like ARM)
			Eventually(func() bool {
				return tracker.IsProcessRunning()
			}, 30*time.Second, 1*time.Second).Should(BeFalse(),
				"exporter process should have exited")

			WaitForExporterOffline("test-exporter-hooks")
		})
	})

	// ====================================================================
	// Group C: afterLease Failure Modes
	// ====================================================================
	Context("Group C: afterLease Failure Modes", func() {
		It("C1: afterLease onFailure=warn keeps exporter available", func() {
			startHooksExporter("exporter-hooks-after-fail-warn.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("HOOK_FAIL_WARN"))

			WaitForExporter("test-exporter-hooks")
		})

		It("C2: afterLease onFailure=exit shuts down exporter", func() {
			startHooksExporterSingle("exporter-hooks-after-fail-exit.yaml")

			// Shell may succeed or fail; the key is the exporter exits
			_, _ = Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")

			Eventually(func() bool {
				return tracker.IsProcessRunning()
			}, 30*time.Second, 1*time.Second).Should(BeFalse(),
				"exporter process should have exited")

			WaitForExporterOffline("test-exporter-hooks")
		})
	})

	// ====================================================================
	// Group D: Timeout Tests
	// ====================================================================
	Context("Group D: Timeout Tests", func() {
		It("D1: beforeLease timeout is treated as failure", func() {
			startHooksExporter("exporter-hooks-timeout.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("HOOK_TIMEOUT: starting"))

			WaitForExporter("test-exporter-hooks")
		})
	})

	// ====================================================================
	// Group E: j Commands in Hooks
	// ====================================================================
	Context("Group E: j Commands in Hooks", func() {
		It("E1: beforeLease can use j power on", func() {
			startHooksExporter("exporter-hooks-both.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("BEFORE_HOOK: complete"))
		})

		It("E2: afterLease can use j power off", func() {
			startHooksExporter("exporter-hooks-both.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("AFTER_HOOK: complete"))
		})

		It("E3: environment variables are available in hooks", func() {
			startHooksExporter("exporter-hooks-both.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("BEFORE_HOOK:"))
			Expect(out).To(MatchRegexp(`lease=[0-9a-f-]+`))
			Expect(out).To(MatchRegexp(`client=`))
		})
	})

	// ====================================================================
	// Group F: Custom Executors (exec field)
	// ====================================================================
	Context("Group F: Custom Executors", func() {
		It("F1: exec /bin/bash runs bash-specific syntax", func() {
			startHooksExporter("exporter-hooks-exec-bash.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("BASH_HOOK: complete"))
		})

		It("F2: .py file auto-detects Python and uses driver API", func() {
			pyScript := `import os
from jumpstarter.utils.env import env

lease = os.environ.get("LEASE_NAME", "unknown")
print(f"PYTHON_HOOK: lease={lease}")

with env() as client:
    client.power.on()
    print("PYTHON_HOOK: driver API works")

print("PYTHON_HOOK: complete")
`
			err := os.WriteFile("/tmp/jumpstarter-e2e-hook-python.py", []byte(pyScript), 0644)
			Expect(err).NotTo(HaveOccurred())

			startHooksExporter("exporter-hooks-exec-python.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("PYTHON_HOOK: driver API works"))
			Expect(out).To(ContainSubstring("PYTHON_HOOK: complete"))
		})

		It("F3: script as file path executes the file", func() {
			script := "#!/bin/sh\necho \"SCRIPTFILE_HOOK: executed from file\"\necho \"SCRIPTFILE_HOOK: complete\"\n"
			err := os.WriteFile("/tmp/jumpstarter-e2e-hook-script.sh", []byte(script), 0755)
			Expect(err).NotTo(HaveOccurred())

			startHooksExporter("exporter-hooks-exec-script-file.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("SCRIPTFILE_HOOK: complete"))
		})
	})

	// ====================================================================
	// Group G: Lease Timeout (no hooks)
	// ====================================================================
	Context("Group G: Lease Timeout", func() {
		It("G1: no hooks with lease timeout exits cleanly", func() {
			startHooksExporter("exporter-hooks-none.yaml")

			out, err := RunCmd("timeout", "60", "jmp", "shell",
				"--client", "test-client-hooks",
				"--selector", "example.com/board=hooks",
				"--duration", "10s", "--", "sleep", "30")
			// exit code may be non-zero from killed sleep, but no Error:
			_ = err
			Expect(out).NotTo(ContainSubstring("Error:"))
		})

		It("G2: lease timeout during slow beforeLease hook exits cleanly", func() {
			startHooksExporter("exporter-hooks-slow-before.yaml")

			out, err := RunCmd("timeout", "60", "jmp", "shell",
				"--client", "test-client-hooks",
				"--selector", "example.com/board=hooks",
				"--duration", "5s", "--", "sleep", "30")
			_ = err
			Expect(out).NotTo(ContainSubstring("Error:"))
		})

		It("G3: lease timeout shortly after beforeLease hook exits cleanly", func() {
			startHooksExporter("exporter-hooks-slow-before.yaml")

			out, err := RunCmd("timeout", "60", "jmp", "shell",
				"--client", "test-client-hooks",
				"--selector", "example.com/board=hooks",
				"--duration", "12s", "--", "sleep", "30")
			_ = err
			Expect(out).NotTo(ContainSubstring("Error:"))
		})
	})

	// ====================================================================
	// Group H: PR Regression Tests
	// ====================================================================
	Context("Group H: PR Regression Tests", func() {
		It("H1: infrastructure messages not visible in client output", func() {
			startHooksExporter("exporter-hooks-before-only.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("BEFORE_HOOK_MARKER: executed"))
			Expect(out).NotTo(ContainSubstring("Starting hook subprocess"))
			Expect(out).NotTo(ContainSubstring("Creating PTY"))
			Expect(out).NotTo(ContainSubstring("Hook executed successfully"))
		})

		It("H2: beforeLease fail+exit does NOT run afterLease hook", func() {
			startHooksExporterSingle("exporter-hooks-before-fail-exit-with-after.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).To(HaveOccurred())
			Expect(out).NotTo(ContainSubstring("AFTER_SHOULD_NOT_RUN"))

			WaitForExporterOffline("test-exporter-hooks")
		})

		It("H3: warning displayed when beforeLease hook fails with warn", func() {
			startHooksExporter("exporter-hooks-before-fail-warn.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("Warning:"))
		})

		It("H4: warning displayed when afterLease hook fails with warn", func() {
			startHooksExporter("exporter-hooks-after-fail-warn.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("Warning:"))
		})
	})

})
