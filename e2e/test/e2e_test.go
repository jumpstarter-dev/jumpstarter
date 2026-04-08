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
	"fmt"
	"os"
	"path/filepath"
	"strings"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

var _ = Describe("Core E2E Tests", Label("core"), Ordered, func() {
	var tracker *ProcessTracker

	BeforeAll(func() {
		tracker = NewProcessTracker()
	})

	AfterAll(func() {
		tracker.StopAll()
		DumpControllerLogs(250)
		tracker.Cleanup()
	})

	BeforeEach(func() {
		tracker.WriteLogMarker(CurrentSpecReport().FullText())
	})

	AfterEach(func() {
		if CurrentSpecReport().Failed() {
			tracker.DumpLogs(250)
			DumpControllerLogs(250)
		}
	})

	// -------------------------------------------------------------------
	// Login endpoint
	// -------------------------------------------------------------------
	Context("Login endpoint", func() {
		It("serves landing page", func() {
			out, err := RunCmd("curl", "-s", fmt.Sprintf("http://%s", LoginEndpoint()))
			Expect(err).NotTo(HaveOccurred())
			Expect(out).To(ContainSubstring("Jumpstarter"))
			Expect(out).To(ContainSubstring("jmp login"))
		})
	})

	// -------------------------------------------------------------------
	// Client and Exporter creation
	// -------------------------------------------------------------------
	Context("Admin CLI resource creation", func() {
		It("can create clients with admin cli", func() {
			ns := Namespace()

			out, err := Jmp("admin", "create", "client", "-n", ns, "test-client-oidc",
				"--unsafe", "--nointeractive", "--oidc-username", "dex:test-client-oidc")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("admin", "create", "client", "-n", ns, "test-client-sa",
				"--unsafe", "--nointeractive",
				"--oidc-username", fmt.Sprintf("dex:system:serviceaccount:%s:test-client-sa", ns))
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("admin", "create", "client", "-n", ns, "test-client-legacy",
				"--unsafe", "--save")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("config", "client", "list", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-client-legacy"))
		})

		It("can create exporters with admin cli", func() {
			ns := Namespace()

			out, err := Jmp("admin", "create", "exporter", "-n", ns, "test-exporter-oidc",
				"--nointeractive", "--oidc-username", "dex:test-exporter-oidc",
				"--label", "example.com/board=oidc")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("admin", "create", "exporter", "-n", ns, "test-exporter-sa",
				"--nointeractive",
				"--oidc-username", fmt.Sprintf("dex:system:serviceaccount:%s:test-exporter-sa", ns),
				"--label", "example.com/board=sa")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("admin", "create", "exporter", "-n", ns, "test-exporter-legacy",
				"--save", "--label", "example.com/board=legacy")
			Expect(err).NotTo(HaveOccurred(), out)

			exporterConfigPath := "/etc/jumpstarter/exporters/test-exporter-legacy.yaml"
			overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml")
			MergeExporterConfig(exporterConfigPath, overlayPath)

			out, err = Jmp("config", "exporter", "list", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-exporter-legacy"))
		})
	})

	// -------------------------------------------------------------------
	// OIDC / SA login
	// -------------------------------------------------------------------
	Context("OIDC and SA login", func() {
		It("can login with oidc test-client-oidc", func() {
			out, err := Jmp("login", "--client", "test-client-oidc",
				"--endpoint", Endpoint(), "--namespace", Namespace(), "--name", "test-client-oidc",
				"--issuer", "https://dex.dex.svc.cluster.local:5556",
				"--username", "test-client-oidc@example.com", "--password", "password", "--unsafe")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("config", "client", "list", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-client-oidc"))
		})

		It("can login with oidc test-client-oidc-provisioning", func() {
			out, err := Jmp("login", "--client", "test-client-oidc-provisioning-example-com",
				"--endpoint", Endpoint(), "--namespace", Namespace(), "--name", "",
				"--issuer", "https://dex.dex.svc.cluster.local:5556",
				"--username", "test-client-oidc-provisioning@example.com", "--password", "password", "--unsafe")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("config", "client", "list", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-client-oidc-provisioning-example-com"))
		})

		It("can login with oidc test-client-sa", func() {
			ns := Namespace()
			token := MustKubectl("create", "-n", ns, "token", "test-client-sa")

			out, err := Jmp("login", "--client", "test-client-sa",
				"--endpoint", Endpoint(), "--namespace", ns, "--name", "test-client-sa",
				"--issuer", "https://dex.dex.svc.cluster.local:5556",
				"--connector-id", "kubernetes", "--token", token, "--unsafe")
			Expect(err).NotTo(HaveOccurred(), out)

			out, err = Jmp("config", "client", "list", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-client-sa"))
		})

		It("can login with oidc test-exporter-oidc", func() {
			out, err := Jmp("login", "--exporter", "test-exporter-oidc", "--name", "test-exporter-oidc",
				"--endpoint", Endpoint(), "--namespace", Namespace(),
				"--issuer", "https://dex.dex.svc.cluster.local:5556",
				"--username", "test-exporter-oidc@example.com", "--password", "password")
			Expect(err).NotTo(HaveOccurred(), out)

			exporterConfigPath := "/etc/jumpstarter/exporters/test-exporter-oidc.yaml"
			overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml")
			MergeExporterConfig(exporterConfigPath, overlayPath)

			out, err = Jmp("config", "exporter", "list", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-exporter-oidc"))
		})

		It("can login with oidc test-exporter-sa", func() {
			ns := Namespace()
			token := MustKubectl("create", "-n", ns, "token", "test-exporter-sa")

			out, err := Jmp("login", "--exporter", "test-exporter-sa",
				"--endpoint", Endpoint(), "--namespace", ns, "--name", "test-exporter-sa",
				"--issuer", "https://dex.dex.svc.cluster.local:5556",
				"--connector-id", "kubernetes", "--token", token)
			Expect(err).NotTo(HaveOccurred(), out)

			overlayPath := filepath.Join(RepoRoot(), "e2e", "exporters", "exporter.yaml")
			MergeExporterConfig("/etc/jumpstarter/exporters/test-exporter-oidc.yaml", overlayPath)
			MergeExporterConfig("/etc/jumpstarter/exporters/test-exporter-sa.yaml", overlayPath)
			MergeExporterConfig("/etc/jumpstarter/exporters/test-exporter-legacy.yaml", overlayPath)

			out, err = Jmp("config", "exporter", "list", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-exporter-sa"))
		})

		It("can login with simplified login", Label("operator-only"), func() {
			if Method() != "operator" {
				Skip("CA certificate injection only configured with operator deployment")
			}

			// Remove existing client config first
			_, _ = Jmp("config", "client", "delete", "test-client-oidc")

			out, err := Jmp("login",
				fmt.Sprintf("test-client-oidc@http://%s", LoginEndpoint()),
				"--insecure-tls", "--nointeractive",
				"--username", "test-client-oidc@example.com", "--password", "password", "--unsafe")
			Expect(err).NotTo(HaveOccurred(), out)

			// Verify CA certificate is populated in client config
			clientConfig := filepath.Join(os.Getenv("HOME"), ".config", "jumpstarter", "clients", "test-client-oidc.yaml")
			Expect(clientConfig).To(BeAnExistingFile())

			caOut := MustYq(".tls.ca", clientConfig)
			Expect(caOut).NotTo(BeEmpty())
			Expect(caOut).NotTo(Equal("null"))

			// Verify the new client is set as the default
			out, err = Jmp("config", "client", "list")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(MatchRegexp(`(?m)^\s*\*\s+test-client-oidc\s`))
		})
	})

	// -------------------------------------------------------------------
	// Running exporters
	// -------------------------------------------------------------------
	Context("Exporter lifecycle", func() {
		It("can run exporters", func() {
			tracker.StartExporterLoop("test-exporter-oidc")
			tracker.StartExporterLoop("test-exporter-sa")
			tracker.StartExporterLoop("test-exporter-legacy")
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")
		})

		It("can specify client config only using environment variables", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")

			ns := Namespace()
			endpoint := MustKubectl("-n", ns, "get", "clients.jumpstarter.dev", "test-client-legacy",
				"-o", "jsonpath={.status.endpoint}")
			tokenB64 := MustKubectl("-n", ns, "get", "secrets", "test-client-legacy-client",
				"-o", "jsonpath={.data.token}")
			token := MustRunCmd("bash", "-c", fmt.Sprintf("echo '%s' | base64 -d", tokenB64))

			env := map[string]string{
				"JMP_NAMESPACE":    ns,
				"JMP_DRIVERS_ALLOW": "*",
				"JMP_NAME":         "test-client-legacy",
				"JMP_ENDPOINT":     endpoint,
				"JMP_TOKEN":        token,
			}
			out, err := RunCmdWithEnv(env, "jmp", "shell",
				"--selector", "example.com/board=oidc", "j", "power", "on")
			Expect(err).NotTo(HaveOccurred(), out)
		})

		It("legacy client config contains CA certificate and works with secure TLS", Label("operator-only"), func() {
			if Method() != "operator" {
				Skip("CA certificate injection only available with operator deployment")
			}

			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")

			configFile := filepath.Join(os.Getenv("HOME"), ".config", "jumpstarter", "clients", "test-client-legacy.yaml")
			Expect(configFile).To(BeAnExistingFile())

			caOut := MustYq(".tls.ca", configFile)
			Expect(caOut).NotTo(BeEmpty())
			Expect(caOut).NotTo(Equal("null"))

			// Test without JUMPSTARTER_GRPC_INSECURE
			out, err := RunCmdWithEnvUnset([]string{"JUMPSTARTER_GRPC_INSECURE"},
				"jmp", "get", "exporters", "--client", "test-client-legacy", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-exporter-legacy"))
		})
	})

	// -------------------------------------------------------------------
	// Lease operations
	// -------------------------------------------------------------------
	Context("Lease operations", func() {
		It("can operate on leases", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")
			MustJmp("config", "client", "use", "test-client-oidc")

			MustJmp("create", "lease", "--selector", "example.com/board=oidc", "--duration", "1d")
			MustJmp("get", "leases")
			MustJmp("get", "exporters")

			// Verify label selector filtering (regression test for #36)
			out, err := Jmp("get", "leases", "--selector", "example.com/board=oidc", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("example.com/board=oidc"))

			out, err = Jmp("get", "leases", "--selector", "example.com/board=doesnotexist")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(Equal("No resources found."))

			// Test complex selectors with matchExpressions
			MustJmp("create", "lease", "--selector", "example.com/board=sa,!nonexistent", "--duration", "1d")

			out, err = Jmp("get", "leases", "--selector", "example.com/board=sa", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("example.com/board=sa"))

			out, err = Jmp("get", "leases", "--selector", "!nonexistent", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("!nonexistent"))

			out, err = Jmp("get", "leases", "--selector", "example.com/board=sa,!production")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(Equal("No resources found."))

			out, err = Jmp("get", "leases", "--selector", "example.com/board=sa,!nonexistent,region=us")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(Equal("No resources found."))

			MustJmp("delete", "leases", "--all")
		})

		It("paginated lease listing returns all leases", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")
			MustJmp("config", "client", "use", "test-client-oidc")

			for i := 1; i <= 101; i++ {
				out, err := Jmp("create", "lease", "--selector", "example.com/board=oidc", "--duration", "1d")
				Expect(err).NotTo(HaveOccurred(), out)
			}

			out, err := Jmp("get", "leases", "-o", "name")
			Expect(err).NotTo(HaveOccurred(), out)
			lines := strings.Split(strings.TrimSpace(out), "\n")
			Expect(lines).To(HaveLen(101))

			MustJmp("delete", "leases", "--all")
		})

		It("paginated exporter listing returns all exporters", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")
			MustJmp("config", "client", "use", "test-client-oidc")

			ns := Namespace()
			for i := 1; i <= 101; i++ {
				name := fmt.Sprintf("pagination-exp-%d", i)
				out, err := Jmp("admin", "create", "exporter", "-n", ns, name,
					"--nointeractive", "-l", "pagination=true",
					"--oidc-username", fmt.Sprintf("dex:%s", name))
				Expect(err).NotTo(HaveOccurred(), out)
			}

			out, err := Jmp("get", "exporters", "--selector", "pagination=true", "-o", "name")
			Expect(err).NotTo(HaveOccurred(), out)
			lines := strings.Split(strings.TrimSpace(out), "\n")
			Expect(lines).To(HaveLen(101))

			for i := 1; i <= 101; i++ {
				MustJmp("admin", "delete", "exporter", "--namespace", ns, fmt.Sprintf("pagination-exp-%d", i), "--delete")
			}
		})

		It("lease listing shows expires at and remaining columns", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")
			MustJmp("config", "client", "use", "test-client-oidc")

			MustJmp("create", "lease", "--selector", "example.com/board=oidc", "--duration", "1d")

			out, err := RunCmdWithEnv(map[string]string{"COLUMNS": "200"},
				"jmp", "get", "leases")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("EXPIRES AT"))
			Expect(out).To(ContainSubstring("REMAINING"))

			MustJmp("delete", "leases", "--all")
		})

		It("can transfer lease to another client", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")
			MustJmp("config", "client", "use", "test-client-oidc")

			out := MustJmp("create", "lease", "--selector", "example.com/board=oidc",
				"--duration", "1d", "-o", "yaml")
			leaseName := MustRunCmd("bash", "-c",
				fmt.Sprintf("echo '%s' | go run github.com/mikefarah/yq/v4@latest '.name'", out))

			ns := Namespace()
			MustKubectl("-n", ns, "wait", "--timeout", "60s", "--for=condition=Ready",
				fmt.Sprintf("leases.jumpstarter.dev/%s", leaseName))

			out, err := Jmp("update", "lease", leaseName, "--to-client", "test-client-legacy", "-o", "yaml")
			Expect(err).NotTo(HaveOccurred(), out)
			Expect(out).To(ContainSubstring("test-client-legacy"))

			MustJmp("delete", "leases", "--client", "test-client-legacy", "--all")
		})
	})

	// -------------------------------------------------------------------
	// Lease and connect
	// -------------------------------------------------------------------
	Context("Lease and connect", func() {
		It("can lease and connect to exporters", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")

			MustJmp("shell", "--client", "test-client-oidc", "--selector", "example.com/board=oidc", "j", "power", "on")
			MustJmp("shell", "--client", "test-client-sa", "--selector", "example.com/board=sa", "j", "power", "on")
			MustJmp("shell", "--client", "test-client-legacy", "--selector", "example.com/board=legacy", "j", "power", "on")

			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")
			MustJmp("shell", "--client", "test-client-oidc-provisioning-example-com",
				"--selector", "example.com/board=oidc", "j", "power", "on")
		})

		It("can lease and connect to exporters by name", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")

			MustJmp("shell", "--client", "test-client-oidc", "--name", "test-exporter-oidc", "j", "power", "on")
			MustJmp("shell", "--client", "test-client-sa", "--name", "test-exporter-sa", "j", "power", "on")
			MustJmp("shell", "--client", "test-client-legacy", "--name", "test-exporter-legacy", "j", "power", "on")

			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")

			// --name and --selector together should work when they match
			MustJmp("shell", "--client", "test-client-oidc",
				"--name", "test-exporter-oidc", "--selector", "example.com/board=oidc",
				"j", "power", "on")
		})

		It("fails fast when requesting non-existent exporter by name", func() {
			WaitForExporters("test-exporter-oidc", "test-exporter-sa", "test-exporter-legacy")

			out, err := RunCmd("timeout", "20s", "jmp", "shell",
				"--client", "test-client-oidc", "--name", "test-exporter-does-not-exist",
				"j", "power", "on")
			Expect(err).To(HaveOccurred())
			// Should not be a timeout (exit code 124)
			Expect(out).To(ContainSubstring("cannot be satisfied"))
		})
	})

	// -------------------------------------------------------------------
	// Admin get/delete
	// -------------------------------------------------------------------
	Context("Admin CLI get and delete", func() {
		It("can get CRDs with admin cli", func() {
			ns := Namespace()
			MustJmp("admin", "get", "client", "--namespace", ns)
			MustJmp("admin", "get", "exporter", "--namespace", ns)
			MustJmp("admin", "get", "lease", "--namespace", ns)
		})

		It("can delete clients with admin cli", func() {
			ns := Namespace()

			MustKubectl("-n", ns, "get", "secret", "test-client-oidc-client")
			MustKubectl("-n", ns, "get", "clients.jumpstarter.dev/test-client-oidc")
			MustKubectl("-n", ns, "get", "clients.jumpstarter.dev/test-client-sa")
			MustKubectl("-n", ns, "get", "clients.jumpstarter.dev/test-client-legacy")

			MustJmp("admin", "delete", "client", "--namespace", ns, "test-client-oidc", "--delete")
			MustJmp("admin", "delete", "client", "--namespace", ns, "test-client-sa", "--delete")
			MustJmp("admin", "delete", "client", "--namespace", ns, "test-client-legacy", "--delete")

			_, err := Kubectl("-n", ns, "get", "secret", "test-client-oidc-client")
			Expect(err).To(HaveOccurred())
			_, err = Kubectl("-n", ns, "get", "clients.jumpstarter.dev/test-client-oidc")
			Expect(err).To(HaveOccurred())
			_, err = Kubectl("-n", ns, "get", "clients.jumpstarter.dev/test-client-sa")
			Expect(err).To(HaveOccurred())
			_, err = Kubectl("-n", ns, "get", "clients.jumpstarter.dev/test-client-legacy")
			Expect(err).To(HaveOccurred())
		})

		It("can delete exporters with admin cli", func() {
			ns := Namespace()

			MustKubectl("-n", ns, "get", "secret", "test-exporter-oidc-exporter")
			MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/test-exporter-oidc")
			MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/test-exporter-sa")
			MustKubectl("-n", ns, "get", "exporters.jumpstarter.dev/test-exporter-legacy")

			MustJmp("admin", "delete", "exporter", "--namespace", ns, "test-exporter-oidc", "--delete")
			MustJmp("admin", "delete", "exporter", "--namespace", ns, "test-exporter-sa", "--delete")
			MustJmp("admin", "delete", "exporter", "--namespace", ns, "test-exporter-legacy", "--delete")

			_, err := Kubectl("-n", ns, "get", "secret", "test-exporter-oidc-exporter")
			Expect(err).To(HaveOccurred())
			_, err = Kubectl("-n", ns, "get", "exporters.jumpstarter.dev/test-exporter-oidc")
			Expect(err).To(HaveOccurred())
			_, err = Kubectl("-n", ns, "get", "exporters.jumpstarter.dev/test-exporter-sa")
			Expect(err).To(HaveOccurred())
			_, err = Kubectl("-n", ns, "get", "exporters.jumpstarter.dev/test-exporter-legacy")
			Expect(err).To(HaveOccurred())
		})
	})

})
