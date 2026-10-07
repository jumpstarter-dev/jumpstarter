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
	"bufio"
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

// Serial: boots VMs under TCG emulation on a single host (the CI runner).
var _ = Describe("ExporterSet QEMU-SSH E2E Tests", Label("exporterset-qemu-ssh"), Ordered, Serial, func() {
	var (
		ns       string
		manifest string
		setupEnv map[string]string
	)

	BeforeAll(func() {
		ns = Namespace()

		By("running qemu-ssh e2e setup script")
		setupScript := filepath.Join(RepoRoot(), "e2e", "scripts", "setup-qemu-ssh-e2e.sh")
		Expect(setupScript).To(BeAnExistingFile())
		MustRunCmd("bash", setupScript)

		setupEnv = loadQemuSSHEnv()
		manifest = setupEnv["RENDERED_MANIFEST"]
		Expect(manifest).NotTo(BeEmpty(), "RENDERED_MANIFEST missing from .e2e/qemu-ssh.env")
		Expect(manifest).To(BeAnExistingFile())
		Expect(setupEnv["HOST_IP"]).NotTo(BeEmpty(), "HOST_IP missing from .e2e/qemu-ssh.env")

		By(fmt.Sprintf("using host IP %s arch %s", setupEnv["HOST_IP"], setupEnv["GUEST_ARCH"]))

		By("waiting for qemu-ssh exporterset-controller Deployment")
		WaitForDeploymentAvailable("provisioner=qemu-ssh-jumpstarter-dev", 5*time.Minute)

		By("creating and logging in e2e client")
		EnsureOIDCClient(exporterSetQemuSSHClientName)

		By("applying rendered ExporterSet QEMU-SSH manifest")
		MustKubectl("apply", "-f", manifest)
	})

	AfterAll(func() {
		By("cleaning up ExporterSet resources and client")
		// Delete ExporterSet before VirtualTargetClass so the remote-cleanup
		// finalizer can still resolve SSH credentials. Bound waits so a stuck
		// finalizer cannot burn the suite timeout (host cleanup sweeps leftovers).
		_, _ = Kubectl("delete", "--ignore-not-found", "--timeout=2m",
			"-n", ns, "exportersets.virtualtarget.jumpstarter.dev", "qemu-ssh-e2e")
		_, _ = Kubectl("patch", "--ignore-not-found",
			"-n", ns, "exportersets.virtualtarget.jumpstarter.dev", "qemu-ssh-e2e",
			"--type=merge", "-p", `{"metadata":{"finalizers":null}}`)
		_, _ = Kubectl("delete", "--ignore-not-found", "--wait=false",
			"-n", ns, "exportersets.virtualtarget.jumpstarter.dev", "qemu-ssh-e2e")
		_, _ = Kubectl("delete", "--ignore-not-found", "--timeout=30s",
			"-n", ns, "virtualtargetclasses.virtualtarget.jumpstarter.dev", "qemu-ssh-e2e")
		DeleteClient(exporterSetQemuSSHClientName)

		By("cleaning up host-side Podman/quadlet leftovers")
		setupScript := filepath.Join(RepoRoot(), "e2e", "scripts", "setup-qemu-ssh-e2e.sh")
		_, _ = RunCmd("bash", setupScript, "--cleanup")
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

		By("verifying Podman containers are running on the host")
		Eventually(func(g Gomega) {
			out, err := RunCmd("sudo", "podman", "ps",
				"--filter", "name=qemu-ssh-e2e",
				"--format", "{{.Names}} {{.Status}}")
			g.Expect(err).NotTo(HaveOccurred(), "podman ps failed")
			g.Expect(out).To(ContainSubstring("-exporter"),
				"running containers:\n%s\nall containers:\n%s",
				out, podmanQemuSSHAll())
			g.Expect(out).To(ContainSubstring("-runtime"),
				"running containers:\n%s\nall containers:\n%s",
				out, podmanQemuSSHAll())
			g.Expect(strings.ToLower(out)).To(ContainSubstring("up"),
				"running containers:\n%s", out)
		}, 2*time.Minute, qemuSSHPollPeriod).Should(Succeed())
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

// loadQemuSSHEnv reads KEY=VALUE pairs written by setup-qemu-ssh-e2e.sh.
func loadQemuSSHEnv() map[string]string {
	GinkgoHelper()
	path := filepath.Join(RepoRoot(), ".e2e", "qemu-ssh.env")
	f, err := os.Open(path)
	Expect(err).NotTo(HaveOccurred(), "open %s", path)
	defer f.Close() //nolint:errcheck

	vals := map[string]string{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		vals[k] = v
	}
	Expect(sc.Err()).NotTo(HaveOccurred())
	return vals
}

func podmanQemuSSHAll() string {
	out, _ := RunCmd("sudo", "podman", "ps", "-a",
		"--filter", "name=qemu-ssh-e2e",
		"--format", "{{.Names}} {{.Status}}")
	return out
}

// DumpExporterSetQemuSSHLogs prints recent logs from the qemu-ssh
// exporterset-controller and Podman containers for failure diagnosis.
func DumpExporterSetQemuSSHLogs(maxLines int) {
	ns := Namespace()
	_, _ = fmt.Fprintf(GinkgoWriter, "=== ExporterSet / QEMU-SSH logs (last %d lines) ===\n", maxLines)

	out, _ := Kubectl("-n", ns, "logs",
		"-l", "provisioner=qemu-ssh-jumpstarter-dev",
		"--tail", fmt.Sprintf("%d", maxLines))
	if strings.TrimSpace(out) != "" {
		_, _ = fmt.Fprintf(GinkgoWriter, "--- exporterset-controller (qemu-ssh) ---\n%s\n", out)
	}

	_, _ = fmt.Fprintf(GinkgoWriter, "--- podman ps -a (qemu-ssh-e2e) ---\n%s\n", podmanQemuSSHAll())

	units, _ := RunCmd("sudo", "systemctl", "list-units", "--all", "--no-pager",
		"--no-legend", "qemu-ssh-e2e-*.service")
	if strings.TrimSpace(units) != "" {
		_, _ = fmt.Fprintf(GinkgoWriter, "--- systemctl qemu-ssh-e2e ---\n%s\n", units)
	}

	containers, _ := RunCmd("sudo", "podman", "ps", "-a",
		"--filter", "name=qemu-ssh-e2e",
		"--format", "{{.Names}}")
	for _, cname := range strings.Split(strings.TrimSpace(containers), "\n") {
		if cname == "" {
			continue
		}
		_, _ = fmt.Fprintf(GinkgoWriter, "--- podman/%s ---\n", cname)
		logs, _ := RunCmd("sudo", "podman", "logs", "--tail", fmt.Sprintf("%d", maxLines), cname)
		_, _ = fmt.Fprintln(GinkgoWriter, logs)
	}
}
