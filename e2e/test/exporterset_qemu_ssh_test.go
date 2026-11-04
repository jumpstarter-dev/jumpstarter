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
		// Host-side teardown (Podman containers, quadlets, SSH key + sshd
		// override) and the OIDC client must run even if a k8s delete below
		// fails on a stuck finalizer — otherwise containers and root SSH
		// access leak on a shared runner. DeferCleanup runs these at teardown
		// (LIFO, so after the verification below) on every path, including
		// failures, without masking the original error.
		setupScript := filepath.Join(RepoRoot(), "e2e", "scripts", "setup-qemu-ssh-e2e.sh")
		DeferCleanup(func() {
			By("cleaning up host-side Podman/quadlet/SSH leftovers")
			out, err := RunCmd("bash", setupScript, "--cleanup")
			Expect(err).NotTo(HaveOccurred(), "host cleanup failed: %s", out)
		})
		DeferCleanup(DeleteClient, exporterSetQemuSSHClientName)

		By("cleaning up ExporterSet resources")
		// Delete ExporterSet before VirtualTargetClass so the remote-cleanup
		// finalizer can still resolve SSH credentials.
		// NOTE: VTC must exist when the ExporterSet finalizer runs — the
		// controller intentionally refuses cleanup if the class was deleted
		// first (it cannot recover SSH user/port). A future validating
		// webhook should block VTC deletion while ExporterSets reference it.
		// It is therefore NOT deferred: if the ExporterSet delete above fails,
		// deleting the VTC would wedge the stuck ExporterSet permanently.
		out, err := Kubectl("delete", "--ignore-not-found", "--timeout=2m",
			"-n", ns, "exportersets.virtualtarget.jumpstarter.dev", "qemu-ssh-e2e")
		Expect(err).NotTo(HaveOccurred(), "ExporterSet cleanup failed: %s", out)

		By("verifying controller cleanup removed e2e containers")
		// Check before the deferred host --cleanup so a silent controller
		// failure (e.g. SSH connect returned nil) is not masked by the script.
		out, err = RunCmd("sudo", "podman", "ps", "-a",
			"--filter", "name=qemu-ssh-e2e",
			"--format", "{{.Names}}")
		Expect(err).NotTo(HaveOccurred(), "podman ps failed: %s", out)
		Expect(strings.TrimSpace(out)).To(BeEmpty(),
			"controller Cleanup should have removed all e2e containers, found: %s", out)

		out, err = Kubectl("delete", "--ignore-not-found", "--timeout=30s",
			"-n", ns, "virtualtargetclasses.virtualtarget.jumpstarter.dev", "qemu-ssh-e2e")
		Expect(err).NotTo(HaveOccurred(), "VirtualTargetClass cleanup failed: %s", out)
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

	// Runs before the power-cycle lease test so it starts from a never-leased
	// exporter. A prior lease would leave an exporter that lingers as
	// Online=True for up to the controller's ~1m offline timeout after
	// exitOnLeaseEnd takes it down, and the settle below could mistake that
	// dying exporter for a healthy target.
	It("recycles a terminal exporter and refills the pool (ExitAndReplace)", func() {
		// recycleStrategy ExitAndReplace + minAvailableReplicas 1: with
		// exitOnLeaseEnd, releasing a lease makes the exporter exit and go
		// offline; the controller deletes it and scale-up deploys a fresh
		// replacement on the host.
		By("recording the current Available exporter")
		var oldName, oldUID string
		Eventually(func() string {
			oldName = KubectlQuery("-n", ns, "get", "exporter",
				"-l", qemuSSHSelector,
				"-o", "jsonpath={.items[0].metadata.name}")
			return oldName
		}, 5*time.Minute, qemuSSHPollPeriod).ShouldNot(BeEmpty())
		// Online + Registered + Available with no lease, so we only lease a
		// genuinely ready exporter.
		WaitForExporter(oldName)
		oldUID = KubectlQuery("-n", ns, "get", "exporter", oldName, "-o", "jsonpath={.metadata.uid}")
		Expect(oldUID).NotTo(BeEmpty())

		By(fmt.Sprintf("leasing and releasing %s so exitOnLeaseEnd makes it terminal", oldName))
		// A bare acquire/release is enough to end a lease and trigger the exit;
		// no QEMU boot needed, which keeps the recycle assertion fast.
		cmd := JmpCmd(
			"shell",
			"--client", exporterSetQemuSSHClientName,
			"--selector", qemuSSHSelector,
			"--duration", "2m",
			"--",
			"sh", "-c", "true",
		)
		cmd.Env = append(os.Environ(), "JUMPSTARTER_GRPC_INSECURE=1")
		out, err := cmd.CombinedOutput()
		GinkgoWriter.Write(out)
		Expect(err).NotTo(HaveOccurred(), "lease acquire/release failed: %s", string(out))

		By("waiting for the old exporter to be deleted and a single replacement to appear")
		var newName string
		Eventually(func(g Gomega) {
			_, err := Kubectl("-n", ns, "get", "exporter", oldName)
			g.Expect(err).To(HaveOccurred(),
				"old exporter %s should be deleted after ExitAndReplace", oldName)

			names := strings.Fields(strings.TrimSpace(KubectlQuery("-n", ns, "get", "exporter",
				"-l", qemuSSHSelector,
				"-o", "jsonpath={range .items[*]}{.metadata.name}{' '}{end}")))
			g.Expect(names).To(HaveLen(1), "expected exactly one exporter after recycle, got %v", names)
			newName = names[0]
			g.Expect(newName).NotTo(Equal(oldName))
			newUID := KubectlQuery("-n", ns, "get", "exporter", newName, "-o", "jsonpath={.metadata.uid}")
			g.Expect(newUID).NotTo(Equal(oldUID), "replacement exporter should have a new UID")
		}, 6*time.Minute, qemuSSHPollPeriod).Should(Succeed())

		By(fmt.Sprintf("waiting for replacement exporter %s to become Online", newName))
		WaitForExporter(newName)

		By(fmt.Sprintf("verifying %s containers are up and %s containers are gone", newName, oldName))
		Eventually(func(g Gomega) {
			// Replacement containers must be running. Filter by the full
			// (unique) exporter name, not the shared qemu-ssh-e2e prefix, so a
			// lingering old container can't satisfy the check.
			up, err := RunCmd("sudo", "podman", "ps",
				"--filter", "name="+newName,
				"--format", "{{.Names}} {{.Status}}")
			g.Expect(err).NotTo(HaveOccurred(), "podman ps failed")
			g.Expect(up).To(ContainSubstring(newName+"-exporter"),
				"replacement containers not up:\n%s\nall:\n%s", up, podmanQemuSSHAll())
			g.Expect(up).To(ContainSubstring(newName+"-runtime"),
				"replacement containers not up:\n%s\nall:\n%s", up, podmanQemuSSHAll())
			g.Expect(strings.ToLower(up)).To(ContainSubstring("up"),
				"replacement containers not up:\n%s", up)

			// The old exporter's containers must be fully removed by Cleanup
			// (ps -a catches stopped-but-not-removed leftovers, not just running).
			old, err := RunCmd("sudo", "podman", "ps", "-a",
				"--filter", "name="+oldName,
				"--format", "{{.Names}} {{.Status}}")
			g.Expect(err).NotTo(HaveOccurred(), "podman ps -a failed")
			g.Expect(strings.TrimSpace(old)).To(BeEmpty(),
				"old exporter %s containers still present after recycle:\n%s", oldName, old)
		}, 3*time.Minute, qemuSSHPollPeriod).Should(Succeed())
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
