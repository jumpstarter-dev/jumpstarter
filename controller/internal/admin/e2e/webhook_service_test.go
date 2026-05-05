/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package e2e

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	adminv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/webhook"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

var _ = Describe("admin.v1.WebhookService", func() {
	var (
		ns     string
		conn   *grpc.ClientConn
		client adminv1pb.WebhookServiceClient
	)

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
		applyClusterRoleBinding(context.Background(), usernameFor("alice"),
			[]string{"get", "list", "create", "update", "patch", "delete"},
			[]string{"webhooks"})
		// Pre-create the Secret the webhook spec references. The CRD
		// validator does not require it to exist at creation time, but the
		// dispatcher load path does, so the smoke test below stays
		// self-contained.
		Expect(k8sClient.Create(context.Background(), &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: "wh-secret"},
			Data:       map[string][]byte{"key": []byte("super-secret-key")},
		})).To(Succeed())

		conn = dialAdmin(tokenFor("alice"))
		client = adminv1pb.NewWebhookServiceClient(conn)
	})

	AfterEach(func() {
		_ = conn.Close()
	})

	Context("CRUD", func() {
		It("creates a webhook with EventClass values", func() {
			got, err := client.CreateWebhook(context.Background(), &adminv1pb.WebhookCreateRequest{
				Parent:    nsParent(ns),
				WebhookId: "wh-1",
				Webhook: &adminv1pb.Webhook{
					Url:       "https://example.invalid/hook",
					SecretRef: "wh-secret/key",
					Events: []adminv1pb.EventClass{
						adminv1pb.EventClass_EVENT_CLASS_LEASE_CREATED,
						adminv1pb.EventClass_EVENT_CLASS_LEASE_ENDED,
					},
				},
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(got.GetName()).To(Equal(webhookName(ns, "wh-1")))

			// Owner annotations are stamped on creation.
			var w jumpstarterdevv1alpha1.Webhook
			Expect(k8sClient.Get(context.Background(),
				types.NamespacedName{Namespace: ns, Name: "wh-1"}, &w)).To(Succeed())
			Expect(w.Annotations).To(HaveKeyWithValue(identity.OwnerAnnotation,
				ownerIdentity("alice").OwnerHash()))
			Expect(w.Spec.URL).To(Equal("https://example.invalid/hook"))
			Expect(w.Spec.Events).To(ConsistOf("LeaseCreated", "LeaseEnded"))
		})

		It("rejects malformed secret_ref", func() {
			_, err := client.CreateWebhook(context.Background(), &adminv1pb.WebhookCreateRequest{
				Parent:    nsParent(ns),
				WebhookId: "wh-bad",
				Webhook: &adminv1pb.Webhook{
					Url:       "https://example.invalid/hook",
					SecretRef: "missing-slash",
					Events:    []adminv1pb.EventClass{adminv1pb.EventClass_EVENT_CLASS_LEASE_CREATED},
				},
			})
			Expect(grpcCode(err)).To(Equal(codes.InvalidArgument))
		})

		It("rejects unknown event classes", func() {
			_, err := client.CreateWebhook(context.Background(), &adminv1pb.WebhookCreateRequest{
				Parent:    nsParent(ns),
				WebhookId: "wh-bad-evt",
				Webhook: &adminv1pb.Webhook{
					Url:       "https://example.invalid/hook",
					SecretRef: "wh-secret/key",
					Events:    []adminv1pb.EventClass{adminv1pb.EventClass_EVENT_CLASS_UNSPECIFIED},
				},
			})
			Expect(grpcCode(err)).To(Equal(codes.InvalidArgument))
		})

		It("requires webhook_id", func() {
			_, err := client.CreateWebhook(context.Background(), &adminv1pb.WebhookCreateRequest{
				Parent: nsParent(ns),
				Webhook: &adminv1pb.Webhook{
					Url:       "https://example.invalid/hook",
					SecretRef: "wh-secret/key",
					Events:    []adminv1pb.EventClass{adminv1pb.EventClass_EVENT_CLASS_LEASE_CREATED},
				},
			})
			Expect(grpcCode(err)).To(Equal(codes.InvalidArgument))
		})

		It("lists, updates, gets, and deletes webhooks", func() {
			_, err := client.CreateWebhook(context.Background(), &adminv1pb.WebhookCreateRequest{
				Parent:    nsParent(ns),
				WebhookId: "wh-2",
				Webhook: &adminv1pb.Webhook{
					Url:       "https://a.invalid/h",
					SecretRef: "wh-secret/key",
					Events:    []adminv1pb.EventClass{adminv1pb.EventClass_EVENT_CLASS_LEASE_CREATED},
				},
			})
			Expect(err).NotTo(HaveOccurred())

			list, err := client.ListWebhooks(context.Background(),
				&adminv1pb.WebhookListRequest{Parent: nsParent(ns)})
			Expect(err).NotTo(HaveOccurred())
			Expect(list.GetWebhooks()).To(HaveLen(1))

			_, err = client.UpdateWebhook(context.Background(), &adminv1pb.WebhookUpdateRequest{
				Webhook: &adminv1pb.Webhook{
					Name: webhookName(ns, "wh-2"),
					Url:  "https://b.invalid/h",
				},
			})
			Expect(err).NotTo(HaveOccurred())

			got, err := client.GetWebhook(context.Background(),
				&jumpstarterv1pb.GetRequest{Name: webhookName(ns, "wh-2")})
			Expect(err).NotTo(HaveOccurred())
			Expect(got.GetUrl()).To(Equal("https://b.invalid/h"))

			_, err = client.DeleteWebhook(context.Background(),
				&jumpstarterv1pb.DeleteRequest{Name: webhookName(ns, "wh-2")})
			Expect(err).NotTo(HaveOccurred())

			list, err = client.ListWebhooks(context.Background(),
				&adminv1pb.WebhookListRequest{Parent: nsParent(ns)})
			Expect(err).NotTo(HaveOccurred())
			Expect(list.GetWebhooks()).To(BeEmpty())
		})
	})

	Context("delivery (smoke)", func() {
		It("dispatches a signed event to the receiver", func() {
			received := &receivedEvents{}
			receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				body, _ := io.ReadAll(r.Body)
				received.add(r.Header.Get(webhook.SignatureHeader), body)
				w.WriteHeader(http.StatusOK)
			}))
			DeferCleanup(receiver.Close)

			// Wire a dispatcher inline. main.go usually builds this; for the
			// smoke test we instantiate it ourselves and pump one event into
			// it. The dispatcher Start blocks, so we run it in a goroutine.
			disp := webhook.NewDispatcher(k8sClient, ginkgoLogger(), 1, 16)
			ctx, cancel := context.WithCancel(context.Background())
			DeferCleanup(cancel)
			go func() { _ = disp.Start(ctx) }()

			// Subscribe via the admin RPC.
			_, err := client.CreateWebhook(context.Background(), &adminv1pb.WebhookCreateRequest{
				Parent:    nsParent(ns),
				WebhookId: "wh-deliv",
				Webhook: &adminv1pb.Webhook{
					Url:       receiver.URL,
					SecretRef: "wh-secret/key",
					Events:    []adminv1pb.EventClass{adminv1pb.EventClass_EVENT_CLASS_LEASE_CREATED},
				},
			})
			Expect(err).NotTo(HaveOccurred())

			disp.Enqueue(webhook.NewEvent(
				jumpstarterdevv1alpha1.WebhookEventLeaseCreated,
				ns,
				map[string]any{"name": "from-test", "namespace": ns},
			))

			Eventually(received.count, 5*time.Second, 50*time.Millisecond).Should(Equal(int32(1)))

			sig, body := received.first()
			Expect(verifySignature(sig, body, []byte("super-secret-key"))).To(BeTrue())

			// Status reflects the success.
			Eventually(func(g Gomega) {
				var w jumpstarterdevv1alpha1.Webhook
				g.Expect(k8sClient.Get(context.Background(),
					types.NamespacedName{Namespace: ns, Name: "wh-deliv"}, &w)).To(Succeed())
				g.Expect(w.Status.LastSuccess).NotTo(BeNil())
				g.Expect(w.Status.ConsecutiveFailures).To(BeNumerically("==", 0))
			}, 5*time.Second).Should(Succeed())
		})

		It("records ConsecutiveFailures when the receiver returns 5xx", func() {
			receiver := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusInternalServerError)
			}))
			DeferCleanup(receiver.Close)

			disp := webhook.NewDispatcher(k8sClient, ginkgoLogger(), 1, 16)
			ctx, cancel := context.WithCancel(context.Background())
			DeferCleanup(cancel)
			go func() { _ = disp.Start(ctx) }()

			_, err := client.CreateWebhook(context.Background(), &adminv1pb.WebhookCreateRequest{
				Parent:    nsParent(ns),
				WebhookId: "wh-fail",
				Webhook: &adminv1pb.Webhook{
					Url:       receiver.URL,
					SecretRef: "wh-secret/key",
					Events:    []adminv1pb.EventClass{adminv1pb.EventClass_EVENT_CLASS_LEASE_ENDED},
				},
			})
			Expect(err).NotTo(HaveOccurred())

			disp.Enqueue(webhook.NewEvent(
				jumpstarterdevv1alpha1.WebhookEventLeaseEnded,
				ns,
				map[string]any{"name": "from-test", "namespace": ns},
			))

			Eventually(func(g Gomega) {
				var w jumpstarterdevv1alpha1.Webhook
				g.Expect(k8sClient.Get(context.Background(),
					types.NamespacedName{Namespace: ns, Name: "wh-fail"}, &w)).To(Succeed())
				g.Expect(w.Status.LastFailure).NotTo(BeNil())
				g.Expect(w.Status.ConsecutiveFailures).To(BeNumerically(">=", 1))
			}, 5*time.Second).Should(Succeed())
		})
	})
})

// receivedEvents collects the signature header + body of every webhook
// POST so the test can assert on at-least-one and on signature validity.
type receivedEvents struct {
	mu     sync.Mutex
	n      atomic.Int32
	header string
	body   []byte
}

func (r *receivedEvents) add(sig string, body []byte) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.header == "" {
		r.header = sig
		r.body = append([]byte{}, body...)
	}
	r.n.Add(1)
}

func (r *receivedEvents) count() int32 { return r.n.Load() }

func (r *receivedEvents) first() (string, []byte) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.header, r.body
}

// verifySignature recomputes HMAC-SHA256 over "<unix>.<body>" and compares
// against the v1 hex value parsed from the X-Jumpstarter-Signature header.
func verifySignature(header string, body, key []byte) bool {
	parts := strings.Split(header, ",")
	var ts, v1 string
	for _, p := range parts {
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
