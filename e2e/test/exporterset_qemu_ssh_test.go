/*
Copyright 2026. The Jumpstarter Authors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package e2e

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

const (
	exporterSetQemuSSHClientName = "test-client-exporterset-qemu-ssh"
	qemuSSHSelector              = "board=qemu-ssh-e2e"

	qemuSSHPollPeriod = time.Second
)

// Serial: boots VMs under TCG emulation; runs on a single host (localhost).
var _ = Describe("ExporterSet QEMU-SSH E2E Tests", Label("exporterset-qemu-ssh"), Ordered, Serial, func() {
	var (
		ns       string
		manifest string
	)

	BeforeAll(func() {
		ns = Namespace()
		manifest = filepath.Join(RepoRoot(), "e2e", "manifests", "exporterset-qemu-ssh-kind.yaml")
		Expect(manifest).To(BeAnExistingFile())

		By("running qemu-ssh e2e setup script")
		setupScript := filepath.Join(RepoRoot(), "e2e", "scripts", "setup-qemu-ssh-e2e.sh")
		if _, err := os.Stat(setupScript); err == nil {
			MustRunCmd("bash", setupScript)
		}

		By("waiting for qemu-ssh exporterset-controller Deployment")
		WaitForDeploymentAvailable("provisioner=qemu-ssh-jumpstarter-dev", 5*time.Minute)

		By("creating and logging in e2e client")
		EnsureOIDCClient(exporterSetQemuSSHClientName)

		By("applying ExporterSet QEMU-SSH manifest")
		MustKubectl("apply", "-f", manifest)
	})

	AfterAll(func() {
		By("cleaning up ExporterSet resources and client")
		if manifest != "" {
			_, _ = Kubectl("delete", "--ignore-not-found", "-f", manifest)
		}
		DeleteClient(exporterSetQemuSSHClientName)
	})

	AfterEach(func() {
		DumpOnFailure(250, func(maxLines int) {
			DumpExporterSetQemuSSHLogs(maxLines)
		})
	})

	It("brings an Exporter Online via SSH-deployed Podman containers", func() {
		By("waiting for ExporterSet to create an exporter")
		var exporterName string
		Eventually(func() string {
			exporterName = KubectlQuery("-n", ns, "get", "exporter",
				"-l", qemuSSHSelector,
				"-o", "jsonpath={.items[0].metadata.name}")
			return exporterName
		}, 5*time.Minute, qemuSSHPollPeriod).ShouldNot(BeEmpty())

		By(fmt.Sprintf("waiting for exporter %s Online/Registered/Available", exporterName))
		WaitForExporter(exporterName)
	})

	It("can lease, power on, and power off through the SSH-deployed exporter", func() {
		By("running power on/off through jmp shell")
		cmd := JmpCmd(
			"shell",
			"--client", exporterSetQemuSSHClientName,
			"--selector", qemuSSHSelector,
			"--duration", "5m",
			"--",
			"sh", "-c", "j qemu power on && sleep 5 && j qemu power off",
		)
		cmd.Env = append(os.Environ(), "JUMPSTARTER_GRPC_INSECURE=1")
		out, err := cmd.CombinedOutput()
		GinkgoWriter.Write(out)
		Expect(err).NotTo(HaveOccurred(), "power on/off failed: %s", string(out))
	})
})

// DumpExporterSetQemuSSHLogs prints recent logs from the qemu-ssh
// exporterset-controller and systemd journal for the Podman containers.
func DumpExporterSetQemuSSHLogs(maxLines int) {
	ns := Namespace()
	_, _ = fmt.Fprintf(GinkgoWriter, "=== ExporterSet / QEMU-SSH logs (last %d lines) ===\n", maxLines)

	// Controller logs
	out, _ := Kubectl("-n", ns, "logs",
		"-l", "provisioner=qemu-ssh-jumpstarter-dev",
		"--tail", fmt.Sprintf("%d", maxLines))
	if strings.TrimSpace(out) != "" {
		_, _ = fmt.Fprintf(GinkgoWriter, "--- exporterset-controller (qemu-ssh) ---\n%s\n", out)
	}

	// Podman container logs on the host
	for _, suffix := range []string{"runtime", "exporter"} {
		pattern := fmt.Sprintf("qemu-ssh-e2e-*-%s", suffix)
		containers, _ := RunCmd("podman", "ps", "-a", "--filter", "name="+pattern,
			"--format", "{{.Names}}")
		for _, cname := range strings.Split(strings.TrimSpace(containers), "\n") {
			if cname == "" {
				continue
			}
			_, _ = fmt.Fprintf(GinkgoWriter, "--- podman/%s ---\n", cname)
			logs, _ := RunCmd("podman", "logs", "--tail", fmt.Sprintf("%d", maxLines), cname)
			_, _ = fmt.Fprintln(GinkgoWriter, logs)
		}
	}
}
