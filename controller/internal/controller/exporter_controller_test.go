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

package controller

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"time"

	"github.com/golang-jwt/jwt/v5"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/tools/record"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
)

var _ = Describe("Exporter Controller", func() {
	Context("When reconciling a resource", func() {
		const resourceName = "test-resource"

		ctx := context.Background()

		typeNamespacedName := types.NamespacedName{
			Name:      resourceName,
			Namespace: "default", // TODO(user):Modify as needed
		}
		exporter := &jumpstarterdevv1alpha1.Exporter{}

		BeforeEach(func() {
			By("creating the custom resource for the Kind Exporter")
			err := k8sClient.Get(ctx, typeNamespacedName, exporter)
			if err != nil && errors.IsNotFound(err) {
				resource := &jumpstarterdevv1alpha1.Exporter{
					ObjectMeta: metav1.ObjectMeta{
						Name:      resourceName,
						Namespace: "default",
					},
					// TODO(user): Specify other spec details if needed.
				}
				Expect(k8sClient.Create(ctx, resource)).To(Succeed())
			}
		})

		AfterEach(func() {
			// TODO(user): Cleanup logic after each test, like removing the resource instance.
			resource := &jumpstarterdevv1alpha1.Exporter{}
			err := k8sClient.Get(ctx, typeNamespacedName, resource)
			Expect(err).NotTo(HaveOccurred())

			By("Cleanup the specific resource instance Exporter")
			Expect(k8sClient.Delete(ctx, resource)).To(Succeed())

			// the cascade delete of secrets does not work on test env
			// https://book.kubebuilder.io/reference/envtest#testing-considerations
			Expect(k8sClient.Delete(ctx, &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{
					Name:      resourceName + "-exporter",
					Namespace: "default",
				},
			})).To(Succeed())
		})
		It("should successfully reconcile the resource", func() {
			By("Reconciling the created resource")
			signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy")
			Expect(err).NotTo(HaveOccurred())

			controllerReconciler := &ExporterReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
				Signer: signer,
			}

			res, err := controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(res.RequeueAfter).To(Equal(tokenExpiryRequeueInterval))

			exporter := &jumpstarterdevv1alpha1.Exporter{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, exporter)).To(Succeed())
			Expect(exporter.Status.TokenExpiresAt).NotTo(BeNil())

			cond := meta.FindStatusCondition(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
			Expect(cond).NotTo(BeNil())
			Expect(cond.Status).To(Equal(metav1.ConditionFalse))
			Expect(cond.Reason).To(Equal("Valid"))
		})
		It("should reconcile a missing token secret", func() {
			By("recreating the secret")
			signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy")
			Expect(err).NotTo(HaveOccurred())

			controllerReconciler := &ExporterReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
				Signer: signer,
			}

			// point the client to a non-existing secret
			exporter := &jumpstarterdevv1alpha1.Exporter{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, exporter)).To(Succeed())

			exporter.Status.Credential = &corev1.LocalObjectReference{Name: "non-existing-secret"}
			Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())

			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())

			By("verifying the secret was created")
			secret := &corev1.Secret{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Namespace: "default",
				Name:      resourceName + "-exporter",
			}, secret)).To(Succeed())
		})
		DescribeTable("warns only after the automatic renewal window", func(lifetime, remaining time.Duration, expiring bool) {
			key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
			Expect(err).NotTo(HaveOccurred())
			signer := oidc.NewSigner(key, "https://example.com", "dummy")
			recorder := record.NewFakeRecorder(10)
			controllerReconciler := &ExporterReconciler{
				Client:   k8sClient,
				Scheme:   k8sClient.Scheme(),
				Signer:   signer,
				Recorder: recorder,
			}
			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(err).NotTo(HaveOccurred())

			exporter := &jumpstarterdevv1alpha1.Exporter{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, exporter)).To(Succeed())
			Expect(recorder.Events).To(Receive(ContainSubstring("CredentialCreated")))
			now := time.Now()
			token, err := jwt.NewWithClaims(jwt.SigningMethodES256, jwt.RegisteredClaims{
				Issuer:    signer.Issuer(),
				Audience:  []string{signer.Audience()},
				Subject:   exporter.InternalSubject(),
				IssuedAt:  jwt.NewNumericDate(now.Add(remaining - lifetime)),
				ExpiresAt: jwt.NewNumericDate(now.Add(remaining)),
			}).SignedString(key)
			Expect(err).NotTo(HaveOccurred())
			secret := &corev1.Secret{}
			secretKey := types.NamespacedName{Namespace: "default", Name: resourceName + "-exporter"}
			Expect(k8sClient.Get(ctx, secretKey, secret)).To(Succeed())
			secret.Data[TokenKey] = []byte(token)
			Expect(k8sClient.Update(ctx, secret)).To(Succeed())

			result, err := controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(err).NotTo(HaveOccurred())
			Expect(result.RequeueAfter).To(BeNumerically(">", 0))
			Expect(result.RequeueAfter).To(BeNumerically("<=", min(tokenExpiryRequeueInterval, lifetime/10)))
			Expect(k8sClient.Get(ctx, secretKey, secret)).To(Succeed())
			Expect(string(secret.Data[TokenKey])).To(Equal(token))
			Expect(k8sClient.Get(ctx, typeNamespacedName, exporter)).To(Succeed())
			Expect(exporter.Status.TokenExpiresAt).NotTo(BeNil())
			condition := meta.FindStatusCondition(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
			Expect(condition).NotTo(BeNil())
			if expiring {
				Expect(condition.Status).To(Equal(metav1.ConditionTrue))
				Expect(condition.Reason).To(Equal("ExpiringSoon"))
				Expect(recorder.Events).To(Receive(ContainSubstring("Warning TokenExpiringSoon")))
			} else {
				Expect(condition.Status).To(Equal(metav1.ConditionFalse))
			}
			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(err).NotTo(HaveOccurred())
			Expect(recorder.Events).NotTo(Receive())
		},
			Entry("year-long token before renewal", 365*24*time.Hour, 32*24*time.Hour, false),
			Entry("year-long token with missed renewal", 365*24*time.Hour, 29*24*time.Hour, true),
			Entry("30-day token at renewal", 30*24*time.Hour, 6*24*time.Hour, false),
			Entry("30-day token with missed renewal", 30*24*time.Hour, 2*24*time.Hour, true),
			Entry("hour-long token at renewal", time.Hour, 12*time.Minute, false),
			Entry("hour-long token with missed renewal", time.Hour, 5*time.Minute, true),
		)

		It("keeps regenerated credentials visible through heartbeats until current-token authentication", func() {
			key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
			Expect(err).NotTo(HaveOccurred())
			signer := oidc.NewSigner(key, "https://example.com", "dummy")
			recorder := record.NewFakeRecorder(10)
			reconciler := &ExporterReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(), Signer: signer, Recorder: recorder}
			request := reconcile.Request{NamespacedName: typeNamespacedName}
			_, err = reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
			Expect(recorder.Events).To(Receive(ContainSubstring("CredentialCreated")))

			resource := &jumpstarterdevv1alpha1.Exporter{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			resource.Status.LastSeen = metav1.NewTime(time.Now().Add(-2 * time.Hour))
			Expect(k8sClient.Status().Update(ctx, resource)).To(Succeed())
			expiredAt := time.Now().Add(-time.Hour).Truncate(time.Second)
			token, err := jwt.NewWithClaims(jwt.SigningMethodES256, jwt.RegisteredClaims{
				Issuer: signer.Issuer(), Audience: []string{signer.Audience()}, Subject: resource.InternalSubject(),
				IssuedAt: jwt.NewNumericDate(expiredAt.Add(-365 * 24 * time.Hour)), ExpiresAt: jwt.NewNumericDate(expiredAt),
			}).SignedString(key)
			Expect(err).NotTo(HaveOccurred())
			secretKey := types.NamespacedName{Namespace: "default", Name: resourceName + "-exporter"}
			secret := &corev1.Secret{}
			Expect(k8sClient.Get(ctx, secretKey, secret)).To(Succeed())
			secret.Data[TokenKey] = []byte(token)
			secret.Annotations = map[string]string{PreviousTokenHashAnnotation: "obsolete-hash"}
			Expect(k8sClient.Update(ctx, secret)).To(Succeed())

			for range 2 {
				_, err = reconciler.Reconcile(ctx, request)
				Expect(err).NotTo(HaveOccurred())
				Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
				condition := meta.FindStatusCondition(resource.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
				Expect(condition).NotTo(BeNil())
				Expect(condition.Status).To(Equal(metav1.ConditionTrue))
				Expect(condition.Reason).To(Equal("Regenerated"))
				Expect(resource.Status.TokenExpiresAt.Time.Equal(expiredAt)).To(BeTrue())
				Expect(k8sClient.Get(ctx, secretKey, secret)).To(Succeed())
				Expect(string(secret.Data[TokenKey])).NotTo(Equal(token))
				Expect(signer.Validate(string(secret.Data[TokenKey]))).To(Succeed())
				Expect(secret.Annotations).NotTo(HaveKey(PreviousTokenHashAnnotation))
			}
			Expect(recorder.Events).To(Receive(ContainSubstring("Warning CredentialRegenerated")))
			Expect(recorder.Events).NotTo(Receive())

			By("receiving a heartbeat from a stream authenticated with the expired token")
			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			resource.Annotations = map[string]string{AuthenticatedTokenHashAnnotation: TokenHash(token)}
			Expect(k8sClient.Update(ctx, resource)).To(Succeed())
			resource.Status.LastSeen = metav1.Now()
			resource.Status.ExporterStatusValue = jumpstarterdevv1alpha1.ExporterStatusAvailable
			Expect(k8sClient.Status().Update(ctx, resource)).To(Succeed())
			_, err = reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			condition := meta.FindStatusCondition(resource.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
			Expect(condition.Reason).To(Equal("Regenerated"))
			Expect(resource.Status.TokenExpiresAt.Time.Equal(expiredAt)).To(BeTrue())

			By("authenticating a new connection with the replacement token")
			resource.Annotations[AuthenticatedTokenHashAnnotation] = TokenHash(string(secret.Data[TokenKey]))
			Expect(k8sClient.Update(ctx, resource)).To(Succeed())
			_, err = reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			condition = meta.FindStatusCondition(resource.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
			Expect(condition.Status).To(Equal(metav1.ConditionFalse))
			Expect(condition.Reason).To(Equal("Valid"))
			Expect(resource.Status.TokenExpiresAt.Time.After(time.Now())).To(BeTrue())
			Expect(k8sClient.Get(ctx, secretKey, secret)).To(Succeed())
			Expect(secret.Annotations).NotTo(HaveKey(expiredTokenAnnotation))
		})
		DescribeTable("suppresses unused internal credential warnings for external identities", func(remaining time.Duration) {
			key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
			Expect(err).NotTo(HaveOccurred())
			signer := oidc.NewSigner(key, "https://example.com", "dummy")
			recorder := record.NewFakeRecorder(10)
			reconciler := &ExporterReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(), Signer: signer, Recorder: recorder}
			request := reconcile.Request{NamespacedName: typeNamespacedName}
			_, err = reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
			Expect(recorder.Events).To(Receive(ContainSubstring("CredentialCreated")))
			resource := &jumpstarterdevv1alpha1.Exporter{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			now := time.Now()
			token, err := jwt.NewWithClaims(jwt.SigningMethodES256, jwt.RegisteredClaims{
				Issuer: signer.Issuer(), Audience: []string{signer.Audience()}, Subject: resource.InternalSubject(),
				IssuedAt: jwt.NewNumericDate(now.Add(-365 * 24 * time.Hour)), ExpiresAt: jwt.NewNumericDate(now.Add(remaining)),
			}).SignedString(key)
			Expect(err).NotTo(HaveOccurred())
			secret := &corev1.Secret{}
			secretKey := types.NamespacedName{Namespace: "default", Name: resourceName + "-exporter"}
			Expect(k8sClient.Get(ctx, secretKey, secret)).To(Succeed())
			secret.Data[TokenKey] = []byte(token)
			Expect(k8sClient.Update(ctx, secret)).To(Succeed())
			_, err = reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
			Expect(recorder.Events).To(Receive(Or(ContainSubstring("TokenExpiringSoon"), ContainSubstring("CredentialRegenerated"))))

			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			resource.Spec.Username = new("oidc:exporter@example.com")
			Expect(k8sClient.Update(ctx, resource)).To(Succeed())
			for range 2 {
				_, err = reconciler.Reconcile(ctx, request)
				Expect(err).NotTo(HaveOccurred())
				Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
				Expect(resource.Status.TokenExpiresAt).To(BeNil())
				Expect(meta.FindStatusCondition(resource.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))).To(BeNil())
				Expect(recorder.Events).NotTo(Receive())
			}
		},
			Entry("expiring credential", 24*time.Hour),
			Entry("regenerated credential", -time.Hour),
		)

		DescribeTable("clears malformed credential recovery annotations", func(annotations map[string]string) {
			signer, err := oidc.NewSignerFromSeed([]byte("annotations"), "https://example.com", "dummy")
			Expect(err).NotTo(HaveOccurred())
			reconciler := &ExporterReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(), Signer: signer}
			request := reconcile.Request{NamespacedName: typeNamespacedName}
			_, err = reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
			secret := &corev1.Secret{}
			secretKey := types.NamespacedName{Namespace: "default", Name: resourceName + "-exporter"}
			Expect(k8sClient.Get(ctx, secretKey, secret)).To(Succeed())
			originalToken := string(secret.Data[TokenKey])
			secret.Annotations = annotations
			secret.Annotations["unrelated"] = "preserved"
			secret.Annotations[PreviousTokenHashAnnotation] = "retained-hash"
			Expect(k8sClient.Update(ctx, secret)).To(Succeed())
			_, err = reconciler.Reconcile(ctx, request)
			Expect(err).NotTo(HaveOccurred())
			Expect(k8sClient.Get(ctx, secretKey, secret)).To(Succeed())
			Expect(secret.Annotations).NotTo(HaveKey(expiredTokenAnnotation))
			Expect(secret.Annotations).NotTo(HaveKey(regeneratedTokenAnnotation))
			Expect(secret.Annotations).To(HaveKeyWithValue("unrelated", "preserved"))
			Expect(secret.Annotations).To(HaveKeyWithValue(PreviousTokenHashAnnotation, "retained-hash"))
			Expect(string(secret.Data[TokenKey])).To(Equal(originalToken))
			resource := &jumpstarterdevv1alpha1.Exporter{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, resource)).To(Succeed())
			condition := meta.FindStatusCondition(resource.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("Valid"))
		},
			Entry("missing regeneration timestamp", map[string]string{expiredTokenAnnotation: "2026-01-01T00:00:00Z"}),
			Entry("missing expiry timestamp", map[string]string{regeneratedTokenAnnotation: "2026-01-01T00:00:00Z"}),
			Entry("malformed expiry", map[string]string{expiredTokenAnnotation: "bad", regeneratedTokenAnnotation: "2026-01-01T00:00:00Z"}),
			Entry("malformed regeneration", map[string]string{expiredTokenAnnotation: "2026-01-01T00:00:00Z", regeneratedTokenAnnotation: "bad"}),
			Entry("empty values", map[string]string{expiredTokenAnnotation: "", regeneratedTokenAnnotation: ""}),
		)
		It("should reconcile an invalid token secret", func() {
			By("recreating an invalid secret")
			signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy")
			Expect(err).NotTo(HaveOccurred())

			controllerReconciler := &ExporterReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
				Signer: signer,
			}

			// First reconcile to create the secret
			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())

			// Corrupt the secret
			secret := &corev1.Secret{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Namespace: "default",
				Name:      resourceName + "-exporter",
			}, secret)).To(Succeed())

			secret.Data[TokenKey] = []byte("invalid-token")
			Expect(k8sClient.Update(ctx, secret)).To(Succeed())

			// Reconcile
			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())

			// Verify secret was recreated with a valid token
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Namespace: "default",
				Name:      resourceName + "-exporter",
			}, secret)).To(Succeed())
			Expect(string(secret.Data[TokenKey])).NotTo(Equal("invalid-token"))

			// Verify condition is Valid
			exporter := &jumpstarterdevv1alpha1.Exporter{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, exporter)).To(Succeed())
			cond := meta.FindStatusCondition(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring))
			Expect(cond).NotTo(BeNil())
			Expect(cond.Status).To(Equal(metav1.ConditionFalse))
			Expect(cond.Reason).To(Equal("Valid"))
		})
	})
})
