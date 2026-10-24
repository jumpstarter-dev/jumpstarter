package service

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/controller"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	. "github.com/onsi/gomega"
	"google.golang.org/grpc/metadata"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

type credentialStatusStream struct{ fakeStatusStream }

func (*credentialStatusStream) Send(*pb.StatusResponse) error { return nil }

func startCredentialStatus(t *testing.T, svc *ControllerService, credential string) {
	t.Helper()
	ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("authorization", "Bearer "+credential))
	ctx, cancel := context.WithCancel(ctx)
	done := make(chan error, 1)
	go func() { done <- svc.Status(&pb.StatusRequest{}, &credentialStatusStream{fakeStatusStream{ctx: ctx}}) }()
	t.Cleanup(func() {
		cancel()
		select {
		case err := <-done:
			NewWithT(t).Expect(err).NotTo(HaveOccurred())
		case <-time.After(time.Second):
			t.Error("Status did not stop")
		}
	})
}

func TestExporterCredentialRecoveryRequiresFreshAuthentication(t *testing.T) {
	g := NewWithT(t)
	svc, ctx := authSuccessServiceCtx(t, nil)
	key := types.NamespacedName{Namespace: "default", Name: "test-exporter"}
	exporter := &jumpstarterdevv1alpha1.Exporter{}
	g.Expect(svc.Client.Get(ctx, key, exporter)).To(Succeed())
	exporter.UID = "test-uid"
	g.Expect(corev1.AddToScheme(svc.Client.Scheme())).To(Succeed())
	svc.Client = fake.NewClientBuilder().WithScheme(svc.Client.Scheme()).WithStatusSubresource(exporter).WithObjects(exporter).Build()
	privateKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	g.Expect(err).NotTo(HaveOccurred())
	signer := oidc.NewSigner(privateKey, "https://example.com", "dummy")
	reconciler := &controller.ExporterReconciler{Client: svc.Client, Scheme: svc.Client.Scheme(), Signer: signer}
	request := reconcile.Request{NamespacedName: key}
	_, err = reconciler.Reconcile(ctx, request)
	g.Expect(err).NotTo(HaveOccurred())
	expiry := time.Now().Add(time.Second).Truncate(time.Second)
	credential, err := jwt.NewWithClaims(jwt.SigningMethodES256, jwt.RegisteredClaims{
		Issuer: signer.Issuer(), Audience: []string{signer.Audience()}, Subject: exporter.InternalSubject(),
		IssuedAt: jwt.NewNumericDate(expiry.Add(-time.Hour)), ExpiresAt: jwt.NewNumericDate(expiry),
	}).SignedString(privateKey)
	g.Expect(err).NotTo(HaveOccurred())
	secretKey := types.NamespacedName{Namespace: key.Namespace, Name: key.Name + "-exporter"}
	secret := &corev1.Secret{}
	g.Expect(svc.Client.Get(ctx, secretKey, secret)).To(Succeed())
	secret.Data[controller.TokenKey] = []byte(credential)
	g.Expect(svc.Client.Update(ctx, secret)).To(Succeed())

	t.Run("existing stream heartbeats", func(t *testing.T) {
		g := NewWithT(t)
		startCredentialStatus(t, svc, credential)
		g.Eventually(func(g Gomega) {
			g.Expect(svc.Client.Get(ctx, key, exporter)).To(Succeed())
			g.Expect(exporter.Annotations).To(HaveKeyWithValue(controller.AuthenticatedTokenHashAnnotation, controller.TokenHash(credential)))
			g.Expect(exporter.Status.LastSeen.IsZero()).To(BeFalse())
		}, time.Second, 10*time.Millisecond).Should(Succeed())
		g.Eventually(func() bool { return time.Now().After(expiry) }, 2*time.Second, 10*time.Millisecond).Should(BeTrue())
		_, err := reconciler.Reconcile(ctx, request)
		g.Expect(err).NotTo(HaveOccurred())
		regeneratedAt := time.Now()
		g.Eventually(func(g Gomega) {
			g.Expect(svc.Client.Get(ctx, key, exporter)).To(Succeed())
			g.Expect(exporter.Status.LastSeen.After(regeneratedAt)).To(BeTrue())
		}, 12*time.Second, 20*time.Millisecond).Should(Succeed())
		_, err = reconciler.Reconcile(ctx, request)
		g.Expect(err).NotTo(HaveOccurred())
		g.Expect(svc.Client.Get(ctx, key, exporter)).To(Succeed())
		condition := meta.FindStatusCondition(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
		g.Expect(condition).NotTo(BeNil())
		g.Expect(condition.Reason).To(Equal("Regenerated"))
		g.Expect(exporter.Status.TokenExpiresAt.Time.Equal(expiry)).To(BeTrue())
	})
	g.Expect(svc.Client.Get(ctx, secretKey, secret)).To(Succeed())
	replacement := string(secret.Data[controller.TokenKey])
	for _, rpc := range []string{"Status", "Register"} {
		t.Run("fresh "+rpc, func(t *testing.T) {
			g := NewWithT(t)
			g.Expect(svc.Client.Get(ctx, key, exporter)).To(Succeed())
			exporter.Annotations[controller.AuthenticatedTokenHashAnnotation] = controller.TokenHash(credential)
			g.Expect(svc.Client.Update(ctx, exporter)).To(Succeed())
			if rpc == "Status" {
				startCredentialStatus(t, svc, replacement)
			} else {
				ctx := metadata.NewIncomingContext(ctx, metadata.Pairs("authorization", "Bearer "+replacement))
				_, err := svc.Register(ctx, &pb.RegisterRequest{})
				g.Expect(err).NotTo(HaveOccurred())
			}
			g.Eventually(func(g Gomega) {
				g.Expect(svc.Client.Get(ctx, key, exporter)).To(Succeed())
				g.Expect(exporter.Annotations[controller.AuthenticatedTokenHashAnnotation]).To(Equal(controller.TokenHash(replacement)))
			}, time.Second, 10*time.Millisecond).Should(Succeed())
			_, err := reconciler.Reconcile(ctx, request)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(svc.Client.Get(ctx, key, exporter)).To(Succeed())
			condition := meta.FindStatusCondition(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
			g.Expect(condition).NotTo(BeNil())
			g.Expect(condition.Reason).To(Equal("Valid"))
		})
	}
}
