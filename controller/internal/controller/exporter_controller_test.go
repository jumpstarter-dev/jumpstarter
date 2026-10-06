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
			controllerReconciler := &ExporterReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
				Signer: signer,
			}
			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{NamespacedName: typeNamespacedName})
			Expect(err).NotTo(HaveOccurred())

			exporter := &jumpstarterdevv1alpha1.Exporter{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, exporter)).To(Succeed())
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
			} else {
				Expect(condition.Status).To(Equal(metav1.ConditionFalse))
			}
		},
			Entry("year-long token before renewal", 365*24*time.Hour, 32*24*time.Hour, false),
			Entry("year-long token with missed renewal", 365*24*time.Hour, 29*24*time.Hour, true),
			Entry("30-day token at renewal", 30*24*time.Hour, 6*24*time.Hour, false),
			Entry("30-day token with missed renewal", 30*24*time.Hour, 2*24*time.Hour, true),
			Entry("hour-long token at renewal", time.Hour, 12*time.Minute, false),
			Entry("hour-long token with missed renewal", time.Hour, 5*time.Minute, true),
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
