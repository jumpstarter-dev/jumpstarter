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
	exporterSetQemuClientName = "test-client-exporterset-qemu"
	exporterSetQemuSelector   = "board=x86-64-virtual-e2e"
	exporterSetQemuManifest   = "e2e/manifests/exporterset-qemu-kind.yaml"
)

var _ = Describe("ExporterSet QEMU E2E Tests", Label("exporterset-qemu"), Ordered, func() {
	var (
		ns        string
		manifest  string
		imagePath string
		script    string
	)

	BeforeAll(func() {
		ns = Namespace()
		manifest = filepath.Join(RepoRoot(), exporterSetQemuManifest)
		script = filepath.Join(RepoRoot(), "e2e", "scripts", "qemu_flash_boot.py")

		By("ensuring Alpine guest image is available")
		out := MustRunCmd("bash", filepath.Join(RepoRoot(), "e2e", "scripts", "ensure-qemu-guest-image.sh"))
		lines := strings.Split(strings.TrimSpace(out), "\n")
		imagePath = lines[len(lines)-1]
		Expect(imagePath).NotTo(BeEmpty())
		Expect(imagePath).To(BeAnExistingFile())

		By("waiting for exporterset-controller Deployment")
		WaitForDeploymentAvailable("component=exporterset-controller", 5*time.Minute)

		By("creating and logging in e2e client")
		EnsureOIDCClient(exporterSetQemuClientName)

		By("applying ExporterSet QEMU kind manifest")
		MustKubectl("apply", "-f", manifest)
	})

	AfterAll(func() {
		By("cleaning up ExporterSet resources and client")
		_, _ = Kubectl("delete", "--ignore-not-found", "-f", manifest)
		DeleteClient(exporterSetQemuClientName)
	})

	AfterEach(func() {
		DumpOnFailure(250, DumpExporterSetQemuLogs)
	})

	It("brings an Exporter Online with a Ready Pod", func() {
		By("waiting for ExporterSet to create an exporter")
		var exporterName string
		Eventually(func() string {
			out, _ := Kubectl("-n", ns, "get", "exporter",
				"-l", exporterSetQemuSelector,
				"-o", "jsonpath={.items[0].metadata.name}")
			exporterName = out
			return out
		}, 5*time.Minute, 5*time.Second).ShouldNot(BeEmpty())

		By(fmt.Sprintf("waiting for exporter %s Online/Registered/Available", exporterName))
		WaitForExporter(exporterName)

		By("waiting for Pod Ready")
		Eventually(func() string {
			out, _ := Kubectl("-n", ns, "get", "pod", exporterName,
				"-o", "jsonpath={.status.phase}")
			return out
		}, 5*time.Minute, 5*time.Second).Should(Equal("Running"))

		Eventually(func() string {
			out, _ := Kubectl("-n", ns, "get", "pod", exporterName,
				"-o", "jsonpath={.status.containerStatuses[*].ready}")
			return out
		}, 5*time.Minute, 5*time.Second).Should(ContainSubstring("true"))
	})

	It("leases, flashes Alpine, and boots to a console login marker", func() {
		By("checking shared volume capacity is large enough for Alpine (~128Mi)")
		Eventually(func() string {
			out, _ := Kubectl("-n", ns, "get", "pod",
				"-l", exporterSetQemuSelector,
				"-o", "jsonpath={.items[0].spec.volumes[?(@.name==\"shared\")].emptyDir.sizeLimit}")
			return out
		}, 2*time.Minute, 5*time.Second).ShouldNot(BeEmpty())

		sizeLimit, _ := Kubectl("-n", ns, "get", "pod",
			"-l", exporterSetQemuSelector,
			"-o", "jsonpath={.items[0].spec.volumes[?(@.name==\"shared\")].emptyDir.sizeLimit}")
		// Without the storage follow-up (#924), SizeLimit stays at 100Mi and
		// flashing Alpine evicts the Pod. Skip until capacity is available.
		if sizeLimit == "" || sizeLimit == "100Mi" {
			Skip(fmt.Sprintf("shared emptyDir SizeLimit=%q is too small for Alpine flash; needs #924 storage work", sizeLimit))
		}

		By("running flash+boot helper under jmp shell")
		// Long timeout: Kind uses TCG emulation without KVM.
		cmd := JmpCmd(
			"shell",
			"--client", exporterSetQemuClientName,
			"--selector", exporterSetQemuSelector,
			"--duration", "1h",
			"--",
			"python3", script,
			"--timeout", "900",
			"--disk-size", "10G",
			imagePath,
		)
		cmd.Env = append(os.Environ(), "JUMPSTARTER_GRPC_INSECURE=1")
		out, err := cmd.CombinedOutput()
		GinkgoWriter.Write(out)
		Expect(err).NotTo(HaveOccurred(), "qemu_flash_boot.py failed: %s", string(out))
		Expect(string(out)).To(ContainSubstring("OK: matched marker"))
	})

	It("power cycles QEMU then rotates the Pod/Exporter and stays responsive", func() {
		By("recording the current Running Pod name and UID")
		var oldName, oldUID string
		Eventually(func(g Gomega) {
			out, err := Kubectl("-n", ns, "get", "pod",
				"-l", exporterSetQemuSelector,
				"--field-selector=status.phase=Running",
				"-o", "jsonpath={.items[0].metadata.name}")
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(out).NotTo(BeEmpty())
			oldName = out
			uid, err := Kubectl("-n", ns, "get", "pod", oldName,
				"-o", "jsonpath={.metadata.uid}")
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(uid).NotTo(BeEmpty())
			oldUID = uid
		}, 2*time.Minute, 5*time.Second).Should(Succeed())

		By("running j qemu power on / power off under jmp shell")
		// One lease: start QEMU via the runtime sidecar, stop it, then release
		// so exitOnLeaseEnd completes the Pod and ExitAndReplace recycles it.
		MustJmp("shell", "--client", exporterSetQemuClientName,
			"--selector", exporterSetQemuSelector,
			"--duration", "5m",
			"--", "sh", "-c", "j qemu power on && j qemu power off")

		By("waiting for the old Pod/Exporter to be deleted and a single replacement Running")
		Eventually(func(g Gomega) {
			// Old Completed instance must be gone (controller deletes Exporter → cascade Pod).
			_, err := Kubectl("-n", ns, "get", "pod", oldName)
			g.Expect(err).To(HaveOccurred(), "old Pod %s should be deleted after ExitAndReplace", oldName)

			_, err = Kubectl("-n", ns, "get", "exporter", oldName)
			g.Expect(err).To(HaveOccurred(), "old Exporter %s should be deleted after ExitAndReplace", oldName)

			podNames, err := Kubectl("-n", ns, "get", "pod",
				"-l", exporterSetQemuSelector,
				"--field-selector=status.phase=Running",
				"-o", "jsonpath={range .items[*]}{.metadata.name}{' '}{end}")
			g.Expect(err).NotTo(HaveOccurred())
			names := strings.Fields(strings.TrimSpace(podNames))
			g.Expect(names).To(HaveLen(1), "expected exactly one Running Pod, got %v", names)

			uid, err := Kubectl("-n", ns, "get", "pod", names[0],
				"-o", "jsonpath={.metadata.uid}")
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(uid).NotTo(Equal(oldUID), "replacement Pod should have a new UID")

			ready, err := Kubectl("-n", ns, "get", "pod", names[0],
				"-o", "jsonpath={.status.containerStatuses[*].ready}")
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(ready).To(ContainSubstring("true"))

			exporters, err := Kubectl("-n", ns, "get", "exporter",
				"-l", exporterSetQemuSelector,
				"-o", "jsonpath={range .items[*]}{.metadata.name}{' '}{end}")
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(strings.Fields(strings.TrimSpace(exporters))).To(HaveLen(1),
				"expected exactly one Exporter after recycle, got %q", exporters)
		}, 5*time.Minute, 5*time.Second).Should(Succeed())

		By("waiting for the replacement exporter to become Available")
		var exporterName string
		Eventually(func() string {
			out, _ := Kubectl("-n", ns, "get", "exporter",
				"-l", exporterSetQemuSelector,
				"-o", "jsonpath={.items[0].metadata.name}")
			exporterName = out
			return out
		}, 2*time.Minute, 5*time.Second).ShouldNot(BeEmpty())
		WaitForExporter(exporterName)

		By("verifying the replacement still responds to qemu power on/off")
		MustJmp("shell", "--client", exporterSetQemuClientName,
			"--selector", exporterSetQemuSelector,
			"--duration", "5m",
			"--", "sh", "-c", "j qemu power on && j qemu power off")
	})
})

// DumpExporterSetQemuLogs prints recent logs from exporterset-controller and
// virtual QEMU pods for failure diagnosis.
func DumpExporterSetQemuLogs(maxLines int) {
	ns := Namespace()
	_, _ = fmt.Fprintf(GinkgoWriter, "=== ExporterSet / QEMU pod logs (last %d lines) ===\n", maxLines)

	out, _ := Kubectl("-n", ns, "get", "pods",
		"-l", exporterSetQemuSelector,
		"-o", "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}")
	for _, name := range strings.Split(strings.TrimSpace(out), "\n") {
		if name == "" {
			continue
		}
		_, _ = fmt.Fprintf(GinkgoWriter, "--- pod/%s exporter (main) ---\n", name)
		logs, _ := Kubectl("-n", ns, "logs", name, "-c", "exporter",
			"--tail", fmt.Sprintf("%d", maxLines))
		_, _ = fmt.Fprintln(GinkgoWriter, logs)
		_, _ = fmt.Fprintf(GinkgoWriter, "--- pod/%s target-runtime (sidecar) ---\n", name)
		logs, _ = Kubectl("-n", ns, "logs", name, "-c", "target-runtime",
			"--tail", fmt.Sprintf("%d", maxLines))
		_, _ = fmt.Fprintln(GinkgoWriter, logs)
	}

	out, _ = Kubectl("-n", ns, "get", "deploy",
		"-o", "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}")
	for _, name := range strings.Split(strings.TrimSpace(out), "\n") {
		if !strings.Contains(name, "exporterset") {
			continue
		}
		_, _ = fmt.Fprintf(GinkgoWriter, "--- deploy/%s ---\n", name)
		logs, _ := Kubectl("-n", ns, "logs", "deploy/"+name, "--tail", fmt.Sprintf("%d", maxLines))
		_, _ = fmt.Fprintln(GinkgoWriter, logs)
	}
}
