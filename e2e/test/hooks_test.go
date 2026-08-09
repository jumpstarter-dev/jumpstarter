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

var _ = Describe("Hooks E2E Tests", Label("hooks"), Ordered, ContinueOnFailure, func() {
	var (
		tracker            *ProcessTracker
		exporterConfigPath string
		// runningConfig is the overlay the exporter currently running in loop
		// mode was started with, or "" when no reusable exporter is running.
		runningConfig string
	)

	exporterOverlay := func(configFile string) string {
		return filepath.Join(RepoRoot(), "e2e", "exporters", configFile)
	}

	// startHooksExporter stops the previous exporter, applies the config overlay,
	// and starts the exporter in a restart loop.
	//
	// Restarting costs several seconds per spec, so an exporter already running
	// in loop mode with the same overlay is reused. The specs leave no state
	// behind that outlives a lease, so the only thing that has to match is the
	// config. Exit-mode specs clear runningConfig because they deliberately
	// leave the exporter dead.
	startHooksExporter := func(configFile string) {
		if runningConfig == configFile {
			WaitForExporter("test-exporter-hooks")
			return
		}

		tracker.StopAll()
		time.Sleep(time.Second)

		ClearHooksConfig(exporterConfigPath)
		MergeExporterConfig(exporterConfigPath, exporterOverlay(configFile))

		tracker.StartExporterLoop("test-exporter-hooks")
		WaitForExporter("test-exporter-hooks")
		runningConfig = configFile
	}

	// startHooksExporterSingle starts without a restart loop (for exit-mode tests).
	startHooksExporterSingle := func(configFile string) {
		tracker.StopAll()
		time.Sleep(time.Second)
		runningConfig = ""

		ClearHooksConfig(exporterConfigPath)
		MergeExporterConfig(exporterConfigPath, exporterOverlay(configFile))

		tracker.StartExporterSingle("test-exporter-hooks")
		WaitForExporter("test-exporter-hooks")
	}

	BeforeAll(func() {
		tracker = NewProcessTracker()
		exporterConfigPath = SystemExporterConfigPath("test-exporter-hooks")

		CreateOIDCClient("test-client-hooks")
		CreateOIDCExporter("test-exporter-hooks", "example.com/board=hooks")
		LoginOIDCClient("test-client-hooks")
		LoginOIDCExporter("test-exporter-hooks")
	})

	AfterAll(func() {
		tracker.StopAll()

		DeleteClient("test-client-hooks")
		DeleteExporter("test-exporter-hooks")

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

	// beforeLeaseFailureOutput matches every client-visible outcome of a failing
	// beforeLease hook.
	//
	// The client and the exporter race here: the exporter ends the lease the
	// moment the hook fails, so which message the client prints depends on how
	// far it got first. It may have seen the hook's own output, the shutdown
	// notice, a dropped connection, or — if the exporter tore the lease down
	// before the client's very first RPC — nothing at all, in which case the
	// client reports the exporter as unreachable. All of these mean the hook
	// failed and the lease ended, which is what these specs assert.
	const beforeLeaseFailureOutput = `(beforeLease hook fail|Exporter shutting down|Connection to exporter lost|` +
		`did not respond to initial status check|unreachable after)`

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
				"--retry-timeout", "0",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).To(HaveOccurred())
			Expect(out).To(MatchRegexp(beforeLeaseFailureOutput))

			WaitForExporter("test-exporter-hooks")
		})

		It("B3: beforeLease onFailure=endLease releases lease and accepts new one", func() {
			startHooksExporter("exporter-hooks-before-fail-endLease.yaml")

			// First lease: shell should fail because beforeLease hook fails
			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--retry-timeout", "0",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).To(HaveOccurred())
			Expect(out).To(MatchRegexp(beforeLeaseFailureOutput))

			// The exporter should release the lease and return to Available
			WaitForExporter("test-exporter-hooks")

			// Second lease: should also fail (hook still configured to fail),
			// but this proves the exporter accepted a new lease after recovery
			out2, err2 := Jmp("shell", "--client", "test-client-hooks",
				"--retry-timeout", "0",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err2).To(HaveOccurred())
			Expect(out2).To(MatchRegexp(beforeLeaseFailureOutput))

			// Exporter should recover again
			WaitForExporter("test-exporter-hooks")
		})

		It("B4: beforeLease fail+endLease does NOT run afterLease hook", func() {
			startHooksExporter("exporter-hooks-before-fail-endLease-with-after.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--retry-timeout", "0",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).To(HaveOccurred())
			Expect(out).NotTo(ContainSubstring("AFTER_SHOULD_NOT_RUN"))

			// Exporter should still be alive and return to Available
			WaitForExporter("test-exporter-hooks")
		})

		It("B5: beforeLease onFailure=exit shuts down exporter", func() {
			startHooksExporterSingle("exporter-hooks-before-fail-exit.yaml")

			out, err := Jmp("shell", "--client", "test-client-hooks",
				"--retry-timeout", "0",
				"--selector", "example.com/board=hooks", "j", "power", "on")
			Expect(err).To(HaveOccurred())
			Expect(out).To(MatchRegexp(beforeLeaseFailureOutput))

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
				"--retry-timeout", "0",
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
				"--retry-timeout", "75",
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
				"--retry-timeout", "75",
				"--client", "test-client-hooks",
				"--selector", "example.com/board=hooks",
				"--duration", "5s", "--", "sleep", "30")
			_ = err
			Expect(out).NotTo(ContainSubstring("Error:"))
		})

		It("G3: lease timeout shortly after beforeLease hook exits cleanly", func() {
			startHooksExporter("exporter-hooks-slow-before.yaml")

			out, err := RunCmd("timeout", "60", "jmp", "shell",
				"--retry-timeout", "75",
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
				"--retry-timeout", "0",
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
