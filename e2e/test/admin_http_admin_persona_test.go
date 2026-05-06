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
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2" //nolint:revive
	. "github.com/onsi/gomega"    //nolint:revive
)

const (
	// foreignNamespace is the "outside the developer's project" target
	// for cross-namespace deny tests. e2e/admin-rbac.yaml creates it but
	// binds no developer RBAC there, so every admin-API verb returns
	// 403 — independently of whether the resource happens to exist.
	foreignNamespace = "e2e-foreign"

	// echoPodName / echoSvcName host an in-cluster receiver the webhook
	// dispatcher posts to. The HMAC signing key is shared with the
	// controller via webhookSecretKey below.
	echoPodName     = "webhook-echo"
	echoSvcName     = "webhook-echo"
	webhookSecretNm = "wh-secret"
	webhookSecretKy = "key"
	webhookSecretVl = "e2e-admin-test-key"
)

var _ = Describe("Admin HTTP API: full admin persona", Ordered, Label("admin-http-api"), func() {
	var (
		ctx      context.Context
		admin    *AdminClient
		alice    *AdminClient
		bob      *AdminClient
		// devNamespace is the operator-watched namespace (typically
		// "jumpstarter-lab"). The Client/Exporter reconcilers run only
		// here, so this is the only namespace where Create-with-bootstrap
		// returns. The self-service developers are RoleBound here.
		devNamespace string
		fixtures     []string // resource names created in BeforeAll for cleanup
	)

	BeforeAll(func() {
		if Endpoint() == "" {
			Skip("admin-http-api requires the e2e cluster — run via e2e/run-e2e.sh")
		}
		ctx = context.Background()
		devNamespace = Namespace()

		// Real cluster + Dex required. Acquire tokens for the three
		// personas the spec relies on.
		adminTok, err := DexToken(ctx, "jumpstarter-admin", DexTestPassword)
		Expect(err).NotTo(HaveOccurred(), "fetch admin token from Dex")
		aliceTok, err := DexToken(ctx, "dev-alice", DexTestPassword)
		Expect(err).NotTo(HaveOccurred(), "fetch dev-alice token from Dex")
		bobTok, err := DexToken(ctx, "dev-bob", DexTestPassword)
		Expect(err).NotTo(HaveOccurred(), "fetch dev-bob token from Dex")

		admin = NewAdminClient(Endpoint(), adminTok)
		alice = NewAdminClient(Endpoint(), aliceTok)
		bob = NewAdminClient(Endpoint(), bobTok)

		// Idempotently re-apply the RBAC manifests — supports re-running
		// the suite locally after manifest edits without rerunning
		// setup-e2e.sh end-to-end.
		_ = MustKubectl("apply", "-f", repoFile("e2e/admin-rbac.yaml"))

		// HMAC signing secret in the operator-watched namespace for webhook tests.
		ensureSecret(devNamespace, webhookSecretNm, webhookSecretKy, webhookSecretVl)

		// Echo receiver pod + service for webhook delivery checks.
		ensureWebhookEcho(devNamespace)
	})

	AfterAll(func() {
		if admin == nil {
			return
		}
		// Best-effort cleanup of named fixtures.
		for _, name := range fixtures {
			parts := strings.Split(name, "/")
			if len(parts) != 4 {
				continue
			}
			ns, plural, id := parts[1], parts[2], parts[3]
			_, _, _, _ = admin.Delete(context.Background(), plural, ns, id)
		}
	})

	track := func(name string) string {
		fixtures = append(fixtures, name)
		return name
	}

	Context("cluster-wide reads", func() {
		It("admin can list clients in the operator-watched namespace and a foreign namespace", func() {
			// Seed a Client in the watched namespace via REST (reconciler
			// will provision its bootstrap token). The foreign namespace
			// has no reconciler, so seed any read-only fixture there
			// directly with kubectl — admin reads via REST should still
			// see it because admin's ClusterRoleBinding spans every namespace.
			devName := uniqueID("admin-list")
			code, _, raw, err := admin.Create(ctx, "clients", devNamespace, devName, map[string]any{})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
			track(ResourceName("clients", devNamespace, devName))

			foreignName := "admin-list-foreign"
			seedRawClient(foreignNamespace, foreignName)
			DeferCleanup(func() {
				_, _ = Kubectl("delete", "client", foreignName, "-n", foreignNamespace, "--ignore-not-found")
			})

			devList, devNames := listClientNames(ctx, admin, devNamespace)
			Expect(devList).To(Equal(http.StatusOK))
			Expect(devNames).To(ContainElement(ResourceName("clients", devNamespace, devName)))

			foreignList, foreignNames := listClientNames(ctx, admin, foreignNamespace)
			Expect(foreignList).To(Equal(http.StatusOK))
			Expect(foreignNames).To(ContainElement(ResourceName("clients", foreignNamespace, foreignName)))
		})
	})

	Context("operating on other users' resources", func() {
		// JEP-0014 §90–94: cluster-admin RBAC is not constrained by
		// per-resource owner hashes. Developer creates → admin updates +
		// deletes succeeds.

		It("admin can update and delete a Client owned by dev-alice", func() {
			id := uniqueID("alice-owned-client")

			// dev-alice creates the resource — owner annotation is alice's hash.
			code, _, raw, err := alice.Create(ctx, "clients", devNamespace, id, map[string]any{})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))

			code, _, raw, err = admin.Update(ctx, "clients", devNamespace, id, map[string]any{
				"labels": map[string]string{"env": "admin-touched"},
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))

			code, _, raw, err = admin.Delete(ctx, "clients", devNamespace, id)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))

			code, _, _, err = admin.Get(ctx, "clients", devNamespace, id)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusNotFound))
		})

		It("admin can update and delete an Exporter owned by dev-alice", func() {
			id := uniqueID("alice-owned-exp")

			code, _, raw, err := alice.Create(ctx, "exporters", devNamespace, id, map[string]any{})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))

			code, _, raw, err = admin.Update(ctx, "exporters", devNamespace, id, map[string]any{
				"labels": map[string]string{"env": "admin-touched"},
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))

			code, _, raw, err = admin.Delete(ctx, "exporters", devNamespace, id)
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
		})
	})

	Context("webhook lifecycle", func() {
		It("delivers a LeaseCreated event to an in-cluster receiver and updates lastSuccess", func() {
			whID := uniqueID("admin-wh")
			echoURL := fmt.Sprintf("http://%s.%s.svc.cluster.local:8080/", echoSvcName, devNamespace)

			code, _, raw, err := admin.Create(ctx, "webhooks", devNamespace, whID, map[string]any{
				"url":        echoURL,
				"secret_ref": webhookSecretNm + "/" + webhookSecretKy,
				"events":     []string{"EVENT_CLASS_LEASE_CREATED"},
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
			track(ResourceName("webhooks", devNamespace, whID))

			// Trigger: admin creates a Client (so they have an owner-hashed
			// Client CRD for lease lookup), an Exporter, and then a Lease
			// referencing it. The LeaseCreated event fires from the
			// reconciler; the dispatcher delivers to echoURL.
			adminClient := uniqueID("admin-wh-self")
			Eventually(func() int {
				c, _, _, _ := admin.Create(ctx, "clients", devNamespace, adminClient, map[string]any{})
				return c
			}, 30*time.Second, time.Second).Should(Equal(http.StatusOK))
			track(ResourceName("clients", devNamespace, adminClient))

			expID := uniqueID("admin-wh-exp")
			code, _, raw, err = admin.Create(ctx, "exporters", devNamespace, expID, map[string]any{})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
			track(ResourceName("exporters", devNamespace, expID))

			leaseID := uniqueID("admin-wh-lease")
			code, _, raw, err = admin.Create(ctx, "leases", devNamespace, leaseID, map[string]any{
				"exporter_name": ResourceName("exporters", devNamespace, expID),
				"duration":      "60s",
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
			track(ResourceName("leases", devNamespace, leaseID))

			// Webhook delivery: the dispatcher calls echoURL, which echoes
			// the request as a JSON-encoded log line. Poll the Webhook CRD
			// status until lastSuccess flips, then validate HMAC against
			// the echo pod's stdout.
			Eventually(func(g Gomega) {
				out, err := Kubectl("get", "webhook", whID, "-n", devNamespace,
					"-o", "jsonpath={.status.lastSuccess}")
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(out).NotTo(BeEmpty(), "expected status.lastSuccess to be set")
			}, 60*time.Second, time.Second).Should(Succeed())

			Eventually(func(g Gomega) {
				out, err := Kubectl("logs", "-n", devNamespace, "pod/"+echoPodName, "--tail=200")
				g.Expect(err).NotTo(HaveOccurred())
				sig, body, found := lastEchoRequest(out)
				g.Expect(found).To(BeTrue(), "no echo request logged yet")
				g.Expect(verifyHMACSignature(sig, body, []byte(webhookSecretVl))).To(BeTrue(),
					"HMAC signature mismatch: header=%q body=%q", sig, string(body))
			}, 60*time.Second, time.Second).Should(Succeed())
		})
	})

	Context("watch streams", func() {
		It("admin sees a dev-alice mutation on a cluster-wide watch", func() {
			stream, err := admin.Watch(ctx, "clients", devNamespace)
			Expect(err).NotTo(HaveOccurred())
			Expect(stream.InitialStatus).To(Equal(http.StatusOK))
			defer stream.Stop()

			id := uniqueID("watch-alice")
			Eventually(func() int {
				c, _, _, _ := alice.Create(ctx, "clients", devNamespace, id, map[string]any{})
				return c
			}, 30*time.Second, time.Second).Should(Equal(http.StatusOK))
			track(ResourceName("clients", devNamespace, id))

			Eventually(stream.Events, 30*time.Second).Should(Receive(matchClientEvent(devNamespace, id)))
		})
	})

	// bob is referenced by the developer suite via the shared package;
	// keep the alias so unused-locals linters don't trip in this file.
	_ = bob
})

// --- helpers shared with the developer suite ---

// uniqueID returns a short suffixed ID safe for kube object names.
func uniqueID(prefix string) string {
	now := time.Now().UTC().Format("150405.000")
	now = strings.ReplaceAll(now, ".", "")
	return strings.ToLower(prefix + "-" + now + "-" + strconv.Itoa(int(time.Now().UnixNano())%1000))
}

// repoFile returns an absolute path under the repo root.
func repoFile(rel string) string {
	root := RepoRoot()
	return root + "/" + rel
}

// seedRawClient kubectl-applies a Client CRD with no owner annotation —
// suitable for "foreign namespace, admin still sees it" read tests
// where the controller's reconciler doesn't run (so a REST Create
// would time out waiting on credential provisioning).
func seedRawClient(ns, name string) {
	GinkgoHelper()
	manifest := fmt.Sprintf(`apiVersion: jumpstarter.dev/v1alpha1
kind: Client
metadata:
  name: %s
  namespace: %s
`, name, ns)
	tmp, err := os.CreateTemp("", "raw-client-*.yaml")
	Expect(err).NotTo(HaveOccurred())
	defer os.Remove(tmp.Name())
	_, err = tmp.WriteString(manifest)
	Expect(err).NotTo(HaveOccurred())
	Expect(tmp.Close()).To(Succeed())
	MustKubectl("apply", "-f", tmp.Name())
}

// ensureSecret creates a kube Secret with a single key/value entry,
// idempotent across runs.
func ensureSecret(ns, name, key, value string) {
	manifest := fmt.Sprintf(`apiVersion: v1
kind: Secret
metadata:
  name: %s
  namespace: %s
type: Opaque
stringData:
  %s: %s
`, name, ns, key, value)
	tmp, err := os.CreateTemp("", "wh-secret-*.yaml")
	Expect(err).NotTo(HaveOccurred())
	defer os.Remove(tmp.Name())
	_, err = tmp.WriteString(manifest)
	Expect(err).NotTo(HaveOccurred())
	Expect(tmp.Close()).To(Succeed())
	MustKubectl("apply", "-f", tmp.Name())
}

// ensureWebhookEcho deploys mendhak/http-https-echo as a Pod + Service
// in ns. The image emits one JSON log line per received request,
// capturing the headers (including X-Jumpstarter-Signature) and body —
// which the test parses to verify the HMAC.
func ensureWebhookEcho(ns string) {
	manifest := fmt.Sprintf(`apiVersion: v1
kind: Pod
metadata:
  name: %[1]s
  namespace: %[2]s
  labels:
    app: %[1]s
spec:
  containers:
  - name: echo
    image: mendhak/http-https-echo:31
    env:
    - name: HTTP_PORT
      value: "8080"
    - name: LOG_WITHOUT_NEWLINE
      value: "true"
    ports:
    - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: %[1]s
  namespace: %[2]s
spec:
  selector:
    app: %[1]s
  ports:
  - port: 8080
    targetPort: 8080
`, echoPodName, ns)
	tmp, err := os.CreateTemp("", "wh-echo-*.yaml")
	Expect(err).NotTo(HaveOccurred())
	defer os.Remove(tmp.Name())
	_, err = tmp.WriteString(manifest)
	Expect(err).NotTo(HaveOccurred())
	Expect(tmp.Close()).To(Succeed())
	MustKubectl("apply", "-f", tmp.Name())

	Eventually(func() string {
		out, _ := Kubectl("get", "pod", echoPodName, "-n", ns,
			"-o", "jsonpath={.status.phase}")
		return out
	}, 90*time.Second, 2*time.Second).Should(Equal("Running"))
}

// listClientNames returns the status code and the slice of client names
// from a List response.
func listClientNames(ctx context.Context, c *AdminClient, ns string) (int, []string) {
	GinkgoHelper()
	code, body, raw, err := c.List(ctx, "clients", ns)
	Expect(err).NotTo(HaveOccurred())
	Expect(code).To(Equal(http.StatusOK), JoinErr(code, raw))
	clients, _ := body["clients"].([]any)
	out := make([]string, 0, len(clients))
	for _, c := range clients {
		if m, ok := c.(map[string]any); ok {
			if n, ok := m["name"].(string); ok {
				out = append(out, n)
			}
		}
	}
	return code, out
}

// lastEchoRequest scans the echo pod's stdout (newest-last) for the
// most recent JSON-encoded request log line and returns the
// X-Jumpstarter-Signature header value and the request body. The
// mendhak/http-https-echo image emits a single JSON object per line.
func lastEchoRequest(podLogs string) (string, []byte, bool) {
	lines := strings.Split(podLogs, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var rec struct {
			Headers map[string]any  `json:"headers"`
			Body    json.RawMessage `json:"body"`
		}
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			continue
		}
		sig := ""
		for k, v := range rec.Headers {
			if strings.EqualFold(k, "x-jumpstarter-signature") {
				if s, ok := v.(string); ok {
					sig = s
				}
			}
		}
		if sig == "" {
			continue
		}
		// Body in mendhak's echo is the raw text. JSON-encoded it'll
		// come back as a quoted string; strip surrounding quotes for
		// the HMAC recomputation, which signs the bytes the dispatcher
		// posted (not a re-encoded form).
		body := []byte(rec.Body)
		if len(body) >= 2 && body[0] == '"' && body[len(body)-1] == '"' {
			unquoted := ""
			if err := json.Unmarshal(body, &unquoted); err == nil {
				body = []byte(unquoted)
			}
		}
		return sig, body, true
	}
	return "", nil, false
}

// verifyHMACSignature reproduces the X-Jumpstarter-Signature check from
// controller/internal/webhook/signer.go. Returns true when "v1=" matches
// HMAC-SHA256("<t>.<body>", key).
func verifyHMACSignature(header string, body, key []byte) bool {
	var ts, v1 string
	for _, p := range strings.Split(header, ",") {
		switch {
		case strings.HasPrefix(p, "t="):
			ts = strings.TrimPrefix(p, "t=")
		case strings.HasPrefix(p, "v1="):
			v1 = strings.TrimPrefix(p, "v1=")
		}
	}
	if ts == "" || v1 == "" {
		return false
	}
	if _, err := strconv.ParseInt(ts, 10, 64); err != nil {
		return false
	}
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(ts))
	mac.Write([]byte("."))
	mac.Write(body)
	want := hex.EncodeToString(mac.Sum(nil))
	return hmac.Equal([]byte(want), []byte(v1))
}

// matchClientEvent / matchLeaseEvent return Gomega matchers that succeed
// for any WatchEvent whose embedded resource carries the given fully-
// qualified name (any EventType — ADDED is most common, MODIFIED also
// acceptable). Used by both persona suites against the REST :watch
// streams the controller exposes through the loopback gRPC dial.
func matchClientEvent(ns, id string) gomegaMatcher {
	return &resourceEventMatcher{singular: "client", want: ResourceName("clients", ns, id)}
}

func matchLeaseEvent(ns, id string) gomegaMatcher {
	return &resourceEventMatcher{singular: "lease", want: ResourceName("leases", ns, id)}
}

// gomegaMatcher is a structural alias so we don't pull in gomega/types
// just for the interface.
type gomegaMatcher interface {
	Match(actual any) (bool, error)
	FailureMessage(actual any) string
	NegatedFailureMessage(actual any) string
}

type resourceEventMatcher struct {
	singular string
	want     string
}

func (m *resourceEventMatcher) Match(actual any) (bool, error) {
	ev, ok := actual.(WatchEvent)
	if !ok {
		return false, fmt.Errorf("expected WatchEvent, got %T", actual)
	}
	if ev.Result == nil {
		return false, nil
	}
	got := ResourceNameInEvent(ev, m.singular)
	return got == m.want, nil
}

func (m *resourceEventMatcher) FailureMessage(actual any) string {
	return fmt.Sprintf("expected watch event to carry %s name %q, got %v", m.singular, m.want, actual)
}

func (m *resourceEventMatcher) NegatedFailureMessage(actual any) string {
	return fmt.Sprintf("expected watch event NOT to carry %s name %q, got %v", m.singular, m.want, actual)
}

