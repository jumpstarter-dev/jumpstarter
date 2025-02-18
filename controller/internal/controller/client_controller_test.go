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

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
)

var _ = Describe("Identity Controller", func() {
	Context("When reconciling a resource", func() {
		const resourceName = "test-resource"

		ctx := context.Background()

		typeNamespacedName := types.NamespacedName{
			Name:      resourceName,
			Namespace: "default", // TODO(user):Modify as needed
		}
		client := &jumpstarterdevv1alpha1.Client{}

		BeforeEach(func() {
			By("creating the custom resource for the Kind Client")
			err := k8sClient.Get(ctx, typeNamespacedName, client)
			if err != nil && errors.IsNotFound(err) {
				resource := &jumpstarterdevv1alpha1.Client{
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
			resource := &jumpstarterdevv1alpha1.Client{}
			err := k8sClient.Get(ctx, typeNamespacedName, resource)
			Expect(err).NotTo(HaveOccurred())

			By("Cleanup the specific resource instance Identity")
			Expect(k8sClient.Delete(ctx, resource)).To(Succeed())

			// the cascade delete of secrets does not work on test env
			// https://book.kubebuilder.io/reference/envtest#testing-considerations
			Expect(k8sClient.Delete(ctx, &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{
					Name:      resourceName + "-client",
					Namespace: "default",
				},
			})).To(Succeed())
		})
		It("should successfully reconcile the resource", func() {
			By("Reconciling the created resource")
			signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy", "dummy:")
			Expect(err).NotTo(HaveOccurred())

			controllerReconciler := &ClientReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
				Signer: signer,
			}

			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())
			// TODO(user): Add more specific assertions depending on your controller's reconciliation logic.
			// Example: If you expect a certain status condition after reconciliation, verify it here.
		})

		It("should reconcile a missing token secret", func() {
			By("recreating the secret")
			signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy", "dummy:")
			Expect(err).NotTo(HaveOccurred())

			controllerReconciler := &ClientReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
				Signer: signer,
			}

			// point the client to a non-existing secret
			client := &jumpstarterdevv1alpha1.Client{}
			Expect(k8sClient.Get(ctx, typeNamespacedName, client)).To(Succeed())

			client.Status.Credential = &corev1.LocalObjectReference{Name: "non-existing-secret"}
			Expect(k8sClient.Status().Update(ctx, client)).To(Succeed())

			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())

			By("verifying the secret was created")
			secret := &corev1.Secret{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Namespace: "default",
				Name:      resourceName + "-client",
			}, secret)).To(Succeed())
		})

		It("should reconcile an invalid token secret", func() {
			By("recreating the secret")
			signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy", "dummy:")
			Expect(err).NotTo(HaveOccurred())

			controllerReconciler := &ClientReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
				Signer: signer,
			}

			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())

			// modify the secret to something invalid
			secret := &corev1.Secret{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Namespace: "default",
				Name:      resourceName + "-client",
			}, secret)).To(Succeed())
			secret.Data[TokenKey] = []byte("invalid")
			Expect(k8sClient.Update(ctx, secret)).To(Succeed())

			_, err = controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())

			By("verifying the secret was updated")
			secret = &corev1.Secret{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Namespace: "default",
				Name:      resourceName + "-client",
			}, secret)).To(Succeed())
			Expect(secret.Data[TokenKey]).NotTo(Equal([]byte("invalid")))
		})
	})
})
