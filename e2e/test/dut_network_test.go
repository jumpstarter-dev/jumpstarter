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
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

func hasPrivileges() bool {
	if os.Getuid() == 0 {
		return true
	}
	err := exec.Command("sudo", "-n", "true").Run() //nolint:gosec
	return err == nil
}

func needsSudo() bool {
	return os.Getuid() != 0
}

func sudoArgs(args ...string) (string, []string) {
	if needsSudo() {
		return "sudo", args
	}
	return args[0], args[1:]
}

var _ = Describe("DUT Network E2E Tests", Label("dut-network"), Ordered, func() {
	var (
		tracker      *ProcessTracker
		listenerPort = 19091
		exporterDir  string
	)

	const (
		dutNs      = "jmp-e2e-dut"
		extNs      = "jmp-e2e-ext"
		vethHost   = "jmp-vhost"
		vethDut    = "jmp-vdut"
		vethUp     = "jmp-vup"
		vethExt    = "jmp-vext"
		nftTable   = "jumpstarter_jmp_vhost"
		dutIP      = "192.168.200.10"
		gatewayIP  = "192.168.200.1"
		extIP      = "10.99.0.1"
		upstreamIP = "10.99.0.2"
		subnet     = "192.168.200.0/24"
	)

	setupNetworkNamespaces := func() {
		runOrFail("ip", "netns", "add", dutNs)
		runOrFail("ip", "netns", "add", extNs)

		runOrFail("ip", "link", "add", vethHost, "type", "veth", "peer", "name", vethDut)
		runOrFail("ip", "link", "set", vethDut, "netns", dutNs)
		runOrFail("ip", "link", "set", vethHost, "address", "02:00:00:00:00:01")

		runOrFail("ip", "link", "add", vethUp, "type", "veth", "peer", "name", vethExt)
		runOrFail("ip", "link", "set", vethExt, "netns", extNs)

		runOrFail("ip", "addr", "add", upstreamIP+"/24", "dev", vethUp)
		runOrFail("ip", "link", "set", vethUp, "up")

		runInNs(extNs, "ip", "addr", "add", extIP+"/24", "dev", vethExt)
		runInNs(extNs, "ip", "link", "set", vethExt, "up")
		runInNs(extNs, "ip", "link", "set", "lo", "up")
		runInNs(extNs, "ip", "route", "add", subnet, "via", upstreamIP)

		// Configure DUT ns with static IP
		runInNs(dutNs, "ip", "addr", "add", dutIP+"/24", "dev", vethDut)
		runInNs(dutNs, "ip", "link", "set", vethDut, "up")
		runInNs(dutNs, "ip", "link", "set", "lo", "up")
		runInNs(dutNs, "ip", "route", "add", "default", "via", gatewayIP)
	}

	teardownNetworkNamespaces := func() {
		runIgnoreErr("ip", "link", "del", vethHost)
		runIgnoreErr("ip", "link", "del", vethUp)
		runIgnoreErr("ip", "netns", "del", dutNs)
		runIgnoreErr("ip", "netns", "del", extNs)
		runIgnoreErr("nft", "delete", "table", "ip", nftTable)
		runIgnoreErr("rm", "-rf", "/tmp/jmp-e2e-dut-network")
	}

	BeforeAll(func() {
		if runtime.GOOS != "linux" {
			Skip("requires Linux")
		}
		if !hasPrivileges() {
			Skip("requires root or passwordless sudo")
		}
		tracker = NewProcessTracker()
		exporterDir = filepath.Join(RepoRoot(), "e2e", "exporters")
		teardownNetworkNamespaces()
		setupNetworkNamespaces()

		configPath := filepath.Join(exporterDir, "exporter-dut-network.yaml")
		tracker.StartDirectExporter(configPath, listenerPort, "", false)
		WaitForDirectExporterReady(listenerPort, "")
	})

	AfterAll(func() {
		tracker.StopAll()
		teardownNetworkNamespaces()

		Eventually(func() error {
			conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", listenerPort), 500*time.Millisecond)
			if err != nil {
				return nil
			}
			conn.Close()
			return fmt.Errorf("port %d is still open", listenerPort)
		}, 10*time.Second, 500*time.Millisecond).Should(Succeed(),
			"port %d should be closed after stopping exporter", listenerPort)

		tracker.Cleanup()
	})

	BeforeEach(func() {
		tracker.WriteLogMarker(CurrentSpecReport().FullText())
	})

	AfterEach(func() {
		if CurrentSpecReport().Failed() {
			tracker.DumpLogs(250)
		}
	})

	jmpShell := func(args ...string) (string, error) {
		shellArgs := []string{"shell", "--tls-grpc", fmt.Sprintf("127.0.0.1:%d", listenerPort),
			"--tls-grpc-insecure", "--"}
		shellArgs = append(shellArgs, args...)
		return Jmp(shellArgs...)
	}

	extractJSON := func(raw string) string {
		start := strings.Index(raw, "{")
		if start < 0 {
			return raw
		}
		return raw[start:]
	}

	Context("Network status", func() {
		It("should report network status via CLI", func() {
			out, err := jmpShell("j", "dut-network", "status")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring(vethHost))
			Expect(out).To(ContainSubstring("masquerade"))

			var status map[string]interface{}
			err = json.Unmarshal([]byte(extractJSON(out)), &status)
			Expect(err).NotTo(HaveOccurred())
			Expect(status["interface_status"]).NotTo(BeNil())
		})
	})

	Context("DHCP leases", func() {
		It("should show leases via CLI", func() {
			out, err := jmpShell("j", "dut-network", "leases")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).NotTo(BeEmpty())
		})
	})

	Context("NAT rules", func() {
		It("should show active NAT rules", func() {
			out, err := jmpShell("j", "dut-network", "nat-rules")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("masquerade"))
			Expect(out).To(ContainSubstring(nftTable))
		})
	})

	Context("Connectivity", func() {
		It("should allow DUT to reach external via NAT", func() {
			Eventually(func() error {
				_, err := runInNsCapture(dutNs, "ping", "-c", "1", "-W", "2", extIP)
				return err
			}, 10*time.Second, 1*time.Second).Should(Succeed(),
				"DUT should be able to ping external IP %s via NAT", extIP)
		})
	})

	Context("IP lookup", func() {
		It("should return error for unknown MAC", func() {
			out, err := jmpShell("j", "dut-network", "get-ip", "ff:ff:ff:ff:ff:ff")
			Expect(err).To(HaveOccurred())
			Expect(out).To(ContainSubstring("No lease found"))
		})
	})

	Context("Lease management", func() {
		It("should add and remove a static lease via CLI", func() {
			out, err := jmpShell("j", "dut-network", "add-lease",
				"02:00:00:00:00:99", "192.168.200.99", "-n", "e2e-test")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("Added"))

			out, err = jmpShell("j", "dut-network", "remove-lease", "02:00:00:00:00:99")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("Removed"))
		})
	})

	Context("DNS management", func() {
		It("should add, list, and remove DNS entries via CLI", func() {
			out, err := jmpShell("j", "dut-network", "add-dns",
				"e2e-test.lab.local", "10.0.0.42")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("Added"))

			out, err = jmpShell("j", "dut-network", "dns-entries")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("e2e-test.lab.local"))
			Expect(out).To(ContainSubstring("10.0.0.42"))

			out, err = jmpShell("j", "dut-network", "remove-dns", "e2e-test.lab.local")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("Removed"))

			out, err = jmpShell("j", "dut-network", "dns-entries")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).NotTo(ContainSubstring("e2e-test.lab.local"))
		})
	})

	Context("TCP connectivity", func() {
		It("should allow TCP connections from DUT to external via NAT", func() {
			serverScript := "import socket; " +
				"s=socket.socket(); " +
				"s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); " +
				"s.bind(('',9998)); " +
				"s.listen(1); " +
				"s.settimeout(10); " +
				"conn,_=s.accept(); " +
				"conn.sendall(b'E2E_OK'); " +
				"conn.close(); " +
				"s.close()"

			fullArgs := []string{"ip", "netns", "exec", extNs, "python3", "-c", serverScript}
			bin, cmdArgs := sudoArgs(fullArgs...)
			listener := exec.Command(bin, cmdArgs...) //nolint:gosec
			listener.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
			Expect(listener.Start()).To(Succeed())
			defer func() {
				_ = syscall.Kill(-listener.Process.Pid, syscall.SIGKILL)
				_ = listener.Wait()
			}()

			time.Sleep(500 * time.Millisecond)

			clientScript := fmt.Sprintf(
				"import socket; "+
					"s=socket.create_connection(('%s',9998),timeout=5); "+
					"data=s.recv(10); "+
					"s.close(); "+
					"print(data.decode())",
				extIP)
			out, err := runInNsCapture(dutNs, "python3", "-c", clientScript)
			Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("TCP connection failed: %s", out))
			Expect(out).To(ContainSubstring("E2E_OK"))
		})
	})
})

func runOrFail(args ...string) {
	bin, cmdArgs := sudoArgs(args...)
	cmd := exec.Command(bin, cmdArgs...) //nolint:gosec
	out, err := cmd.CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(),
		fmt.Sprintf("command %v failed: %s", args, string(out)))
}

func runIgnoreErr(args ...string) {
	bin, cmdArgs := sudoArgs(args...)
	cmd := exec.Command(bin, cmdArgs...) //nolint:gosec
	_ = cmd.Run()
}

func runInNs(ns string, args ...string) {
	fullArgs := append([]string{"ip", "netns", "exec", ns}, args...)
	bin, cmdArgs := sudoArgs(fullArgs...)
	cmd := exec.Command(bin, cmdArgs...) //nolint:gosec
	out, err := cmd.CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(),
		fmt.Sprintf("command in ns %s failed: %v -> %s", ns, args, string(out)))
}

func runInNsCapture(ns string, args ...string) (string, error) {
	fullArgs := append([]string{"ip", "netns", "exec", ns}, args...)
	bin, cmdArgs := sudoArgs(fullArgs...)
	cmd := exec.Command(bin, cmdArgs...) //nolint:gosec
	out, err := cmd.CombinedOutput()
	return string(out), err
}
