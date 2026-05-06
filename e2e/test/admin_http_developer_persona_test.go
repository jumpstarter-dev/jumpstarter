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
	"context"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

var _ = Describe("Admin HTTP API: developer self-service persona", Ordered, Label("admin-http-api"), func() {
	var (
		ctx          context.Context
		alice        *AdminClient
		bob          *AdminClient
		devNamespace string
		fixtures     []string
	)

	BeforeAll(func() {
		if Endpoint() == "" {
			Skip("admin-http-api requires the e2e cluster — run via e2e/run-e2e.sh")
		}
		ctx = context.Background()
		devNamespace = Namespace()

		aliceTok, err := DexToken(ctx, "dev-alice", DexTestPassword)
		Expect(err).NotTo(HaveOccurred(), "fetch dev-alice token from Dex")
		bobTok, err := DexToken(ctx, "dev-bob", DexTestPassword)
		Expect(err).NotTo(HaveOccurred(), "fetch dev-bob token from Dex")

		alice = NewAdminClient(Endpoint(), aliceTok)
		bob = NewAdminClient(Endpoint(), bobTok)

		// RBAC fixtures are applied by setup-e2e.sh; re-apply here for
		// local re-run safety.
		_ = MustKubectl("apply", "-f", repoFile("e2e/admin-rbac.yaml"))
	})

	AfterAll(func() {
		if alice == nil {
			return
		}
		// Cleanup as admin would touch namespaces dev-alice/dev-bob can't
		// reach; instead delete via kubectl which uses the SA's full
		// permissions.
		for _, name := range fixtures {
			parts := strings.Split(name, "/")
			if len(parts) != 4 {
				continue
			}
			_, _ = Kubectl("delete", parts[2], parts[3], "-n", parts[1], "--ignore-not-found")
		}
	})

	track := func(name string) string {
		fixtures = append(fixtures, name)
		return name
	}

	Context("self-service create returns the bootstrap token", func() {
		It("dev-alice creates a Client in e2e-dev and gets an inline_token whose iss is the controller's internal issuer", func() {
			id := uniqueID("alice-ci")
			code, body, raw, err := alice.Create(ctx, "clients", devNamespace, id, map[string]any{})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
			track(ResourceName("clients", devNamespace, id))

			tok := ExtractInlineToken(body)
			Expect(tok).NotTo(BeEmpty(), "expected inline_token in CreateClient response")

			claims, err := DecodeJWTClaims(tok)
			Expect(err).NotTo(HaveOccurred(), "decode inline_token claims")
			iss, _ := claims["iss"].(string)
			Expect(iss).To(Equal("https://localhost:8085"),
				"controller's internal OIDC issuer is hard-coded to https://localhost:8085 (controller/cmd/main.go)")
		})
	})

	Context("namespace boundary — full surface denied outside the developer's project", func() {
		// The self-service ClusterRole is bound only in the operator-watched
		// namespace. Every admin verb on every admin-managed resource in
		// any other namespace must return 403.

		DescribeTable("403 from dev-alice for verbs in foreign namespace",
			func(plural, verb string) {
				id := "no-" + verb // never created
				var code int
				var raw []byte
				var err error

				switch verb {
				case "get":
					code, _, raw, err = alice.Get(ctx, plural, foreignNamespace, id)
				case "list":
					code, _, raw, err = alice.List(ctx, plural, foreignNamespace)
				case "create":
					code, _, raw, err = alice.Create(ctx, plural, foreignNamespace, uniqueID("forbidden"), map[string]any{})
				case "update":
					code, _, raw, err = alice.Update(ctx, plural, foreignNamespace, id, map[string]any{
						"labels": map[string]string{"x": "y"},
					})
				case "delete":
					code, _, raw, err = alice.Delete(ctx, plural, foreignNamespace, id)
				}
				Expect(err).NotTo(HaveOccurred())
				Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))
			},

			Entry("GET clients", "clients", "get"),
			Entry("LIST clients", "clients", "list"),
			Entry("CREATE clients", "clients", "create"),
			Entry("UPDATE clients", "clients", "update"),
			Entry("DELETE clients", "clients", "delete"),

			Entry("GET exporters", "exporters", "get"),
			Entry("LIST exporters", "exporters", "list"),
			Entry("CREATE exporters", "exporters", "create"),
			Entry("UPDATE exporters", "exporters", "update"),
			Entry("DELETE exporters", "exporters", "delete"),

			Entry("GET leases", "leases", "get"),
			Entry("LIST leases", "leases", "list"),
			Entry("CREATE leases", "leases", "create"),
			Entry("UPDATE leases", "leases", "update"),
			Entry("DELETE leases", "leases", "delete"),
		)

		It("WATCH in the foreign namespace returns 403 on the initial response", func() {
			stream, err := alice.Watch(ctx, "clients", foreignNamespace)
			Expect(err).NotTo(HaveOccurred())
			defer stream.Stop()
			Expect(stream.InitialStatus).To(Equal(http.StatusForbidden))
		})

		It("dev-alice cannot touch webhooks even in their own namespace — the self-service role omits webhook verbs", func() {
			id := uniqueID("alice-wh")

			code, _, raw, err := alice.Create(ctx, "webhooks", devNamespace, id, map[string]any{
				"url":        "https://example.invalid/h",
				"secret_ref": webhookSecretNm + "/" + webhookSecretKy,
				"events":     []string{"EVENT_CLASS_LEASE_CREATED"},
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))

			code, _, raw, err = alice.List(ctx, "webhooks", devNamespace)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))

			code, _, raw, err = alice.Get(ctx, "webhooks", devNamespace, id)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))
		})
	})

	Context("ownership boundary between developers", func() {
		// JEP-0014 §90–94: same SAR for both developers, owner-hash
		// distinguishes mutation rights. Reads are SAR-only.
		//
		// Per the implementation-gaps note in the plan file:
		// the controller may not yet enforce a cross-caller owner check
		// in admin.v1 Update/Delete. If these specs fail with status
		// 200, that is the gap to close in the controller.

		It("dev-bob cannot update or delete dev-alice's resources, but can read them", func() {
			for _, plural := range []string{"clients", "exporters", "leases"} {
				By("creating a " + plural + " as dev-alice")

				id := uniqueID("alice-owns-" + strings.TrimSuffix(plural, "s"))
				body := map[string]any{}
				if plural == "leases" {
					expID := track(seedExporterAs(ctx, alice, devNamespace, "alice-exp-"+plural))
					_ = expID
					body["exporter_name"] = expID
					body["duration"] = "60s"
					// CreateLease requires alice to have a Client in the
					// namespace (lease_service.go findClientForCaller).
					ensureClient(ctx, alice, devNamespace, "alice-client-"+plural)
					track(ResourceName("clients", devNamespace, "alice-client-"+plural))
				}
				code, _, raw, err := alice.Create(ctx, plural, devNamespace, id, body)
				Expect(err).NotTo(HaveOccurred())
				Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
				track(ResourceName(plural, devNamespace, id))

				By("dev-bob can Get and List the " + plural + " (read is SAR-only)")
				code, _, raw, err = bob.Get(ctx, plural, devNamespace, id)
				Expect(err).NotTo(HaveOccurred())
				Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))

				code, listBody, raw, err := bob.List(ctx, plural, devNamespace)
				Expect(err).NotTo(HaveOccurred())
				Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
				Expect(listIncludes(listBody, plural, ResourceName(plural, devNamespace, id))).To(BeTrue())

				By("dev-bob's Update is rejected with 403 mentioning owner")
				code, _, raw, err = bob.Update(ctx, plural, devNamespace, id, map[string]any{
					"labels": map[string]string{"hijacked": "yes"},
				})
				Expect(err).NotTo(HaveOccurred())
				Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))
				Expect(strings.ToLower(string(raw))).To(ContainSubstring("owner"))

				By("dev-bob's Delete is rejected with 403 mentioning owner")
				code, _, raw, err = bob.Delete(ctx, plural, devNamespace, id)
				Expect(err).NotTo(HaveOccurred())
				Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))
				Expect(strings.ToLower(string(raw))).To(ContainSubstring("owner"))
			}
		})

		It("dev-bob cannot release dev-alice's lease (DELETE soft-releases)", func() {
			// Setup: alice has a Client + Exporter, then a Lease.
			ensureClient(ctx, alice, devNamespace, "alice-rel-client")
			track(ResourceName("clients", devNamespace, "alice-rel-client"))

			expName := track(seedExporterAs(ctx, alice, devNamespace, "alice-rel-exp"))

			leaseID := uniqueID("alice-rel-lease")
			code, _, raw, err := alice.Create(ctx, "leases", devNamespace, leaseID, map[string]any{
				"exporter_name": expName,
				"duration":      "60s",
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
			track(ResourceName("leases", devNamespace, leaseID))

			// DeleteLease is the soft-release path (sets Spec.Release=true
			// per controller/internal/service/admin/v1/lease_service.go).
			code, _, raw, err = bob.Delete(ctx, "leases", devNamespace, leaseID)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))
		})

		It("dev-alice can update and delete their own resources", func() {
			id := uniqueID("alice-self-mut")
			code, _, raw, err := alice.Create(ctx, "clients", devNamespace, id, map[string]any{})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
			track(ResourceName("clients", devNamespace, id))

			code, _, raw, err = alice.Update(ctx, "clients", devNamespace, id, map[string]any{
				"labels": map[string]string{"updated-by": "alice"},
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))

			code, _, raw, err = alice.Delete(ctx, "clients", devNamespace, id)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
		})
	})

	Context("self-protection of the auto-provisioned identity Client", func() {
		// JEP-0014 §130–137: the legacy client.v1 reconciler
		// auto-provisions a Client CRD named after the OIDC subject on
		// first contact; admin.v1 explicitly disallows the user from
		// operating on that Client *as themselves* (provisioning
		// additional Clients is fine). The fixture below mirrors what
		// the legacy reconciler creates: a Client without the
		// admin.v1-stamped owner annotation, labelled to make the
		// intent visible in test logs.
		const identityClient = "dev-alice"

		BeforeAll(func() {
			seedIdentityClient(devNamespace, identityClient, "dex:dev-alice")
			fixtures = append(fixtures, ResourceName("clients", devNamespace, identityClient))
		})

		It("dev-alice can Get her identity Client", func() {
			code, _, raw, err := alice.Get(ctx, "clients", devNamespace, identityClient)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
		})

		It("dev-alice cannot Update her identity Client", func() {
			code, _, raw, err := alice.Update(ctx, "clients", devNamespace, identityClient, map[string]any{
				"labels": map[string]string{"hijacked": "yes"},
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))
		})

		It("dev-alice cannot Delete her identity Client", func() {
			code, _, raw, err := alice.Delete(ctx, "clients", devNamespace, identityClient)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusForbidden), JoinErr(code, raw))
		})
	})

	Context("lease lifecycle", func() {
		It("dev-alice creates → polls → releases a lease end-to-end", func() {
			// Note: the original spec used a REST `:watch` stream as a
			// side-channel; the controller's gRPC-gateway wiring
			// doesn't yet support server-streaming responses (501),
			// so the test polls Get instead. Streaming is exercised
			// over the gRPC transport in controller/internal/admin/e2e.
			ensureClient(ctx, alice, devNamespace, "alice-life-client")
			track(ResourceName("clients", devNamespace, "alice-life-client"))

			expName := track(seedExporterAs(ctx, alice, devNamespace, "alice-life-exp"))

			leaseID := uniqueID("alice-life-lease")
			code, _, raw, err := alice.Create(ctx, "leases", devNamespace, leaseID, map[string]any{
				"exporter_name": expName,
				"duration":      "60s",
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
			track(ResourceName("leases", devNamespace, leaseID))

			// Confirm the lease is observable.
			Eventually(func() int {
				c, _, _, _ := alice.Get(ctx, "leases", devNamespace, leaseID)
				return c
			}, 30*time.Second, time.Second).Should(Equal(http.StatusOK))

			// Release: DeleteLease sets Spec.Release=true.
			code, _, raw, err = alice.Delete(ctx, "leases", devNamespace, leaseID)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
		})
	})
})

// listIncludes returns true when the resource list response includes a
// resource with the given fully-qualified name.
func listIncludes(body map[string]any, plural, want string) bool {
	if body == nil {
		return false
	}
	items, _ := body[plural].([]any)
	for _, item := range items {
		m, ok := item.(map[string]any)
		if !ok {
			continue
		}
		if n, ok := m["name"].(string); ok && n == want {
			return true
		}
	}
	return false
}

// ensureClient idempotently creates a Client owned by c's caller. Used
// to satisfy LeaseService.findClientForCaller, which requires a Client
// CRD in the namespace whose owner-hash matches the lease creator.
func ensureClient(ctx context.Context, c *AdminClient, ns, id string) {
	GinkgoHelper()
	code, _, _, _ := c.Get(ctx, "clients", ns, id)
	if code == http.StatusOK {
		return
	}
	code, _, raw, err := c.Create(ctx, "clients", ns, id, map[string]any{})
	Expect(err).NotTo(HaveOccurred())
	Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
}

// seedExporterAs creates an Exporter through the admin REST surface and
// returns the canonical fully-qualified name (namespaces/.../exporters/...).
// Returns the existing one if a previous spec already created it.
func seedExporterAs(ctx context.Context, c *AdminClient, ns, id string) string {
	GinkgoHelper()
	code, _, _, _ := c.Get(ctx, "exporters", ns, id)
	if code != http.StatusOK {
		code, _, raw, err := c.Create(ctx, "exporters", ns, id, map[string]any{})
		Expect(err).NotTo(HaveOccurred())
		Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
	}
	return ResourceName("exporters", ns, id)
}

// seedIdentityClient kubectl-applies a Client CRD that mirrors what the
// legacy client.v1 reconciler creates on first OIDC contact: no
// admin.v1-stamped owner annotation, but an annotation tying it to the
// OIDC subject so reviewers can spot the intent. Using an annotation
// (not a label) because the OIDC subject may contain ':' which is not
// valid in label values.
func seedIdentityClient(ns, name, oidcSubject string) {
	GinkgoHelper()
	manifest := fmt.Sprintf(`apiVersion: jumpstarter.dev/v1alpha1
kind: Client
metadata:
  name: %s
  namespace: %s
  annotations:
    jumpstarter.dev/identity-of: %q
`, name, ns, oidcSubject)
	tmp, err := os.CreateTemp("", "identity-client-*.yaml")
	Expect(err).NotTo(HaveOccurred())
	defer os.Remove(tmp.Name())
	_, err = tmp.WriteString(manifest)
	Expect(err).NotTo(HaveOccurred())
	Expect(tmp.Close()).To(Succeed())
	MustKubectl("apply", "-f", tmp.Name())
}
