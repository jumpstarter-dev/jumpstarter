/*
Copyright 2025.

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

package jumpstarter

import (
	"context"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/controller/jumpstarter/endpoints"
)

var _ = Describe("Jumpstarter Controller", func() {
	Context("When reconciling a resource", func() {
		const resourceName = "test-resource"

		ctx := context.Background()

		typeNamespacedName := types.NamespacedName{
			Name:      resourceName,
			Namespace: "default", // TODO(user):Modify as needed
		}
		jumpstarter := &operatorv1alpha1.Jumpstarter{}

		BeforeEach(func() {
			By("creating the custom resource for the Kind Jumpstarter")
			err := k8sClient.Get(ctx, typeNamespacedName, jumpstarter)
			if err != nil && errors.IsNotFound(err) {
				resource := &operatorv1alpha1.Jumpstarter{
					ObjectMeta: metav1.ObjectMeta{
						Name:      resourceName,
						Namespace: "default",
					},
					Spec: operatorv1alpha1.JumpstarterSpec{
						BaseDomain:     "example.com",
						UseCertManager: true,
						Controller: operatorv1alpha1.ControllerConfig{
							Image:           "quay.io/jumpstarter/jumpstarter:latest",
							ImagePullPolicy: "IfNotPresent",
							Replicas:        1,
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("100m"),
									corev1.ResourceMemory: resource.MustParse("100Mi"),
								},
							},
						GRPC: operatorv1alpha1.GRPCConfig{
							Endpoints: []operatorv1alpha1.Endpoint{
								{
									Address: "controller",
								},
							},
						},
						},
						Routers: operatorv1alpha1.RoutersConfig{
							Image:           "quay.io/jumpstarter/jumpstarter:latest",
							ImagePullPolicy: "IfNotPresent",
							Replicas:        1,
							Resources: corev1.ResourceRequirements{
								Requests: corev1.ResourceList{
									corev1.ResourceCPU:    resource.MustParse("100m"),
									corev1.ResourceMemory: resource.MustParse("100Mi"),
								},
							},
						GRPC: operatorv1alpha1.GRPCConfig{
							Endpoints: []operatorv1alpha1.Endpoint{
								{
									Address: "router",
								},
							},
						},
						},
					},
				}
				Expect(k8sClient.Create(ctx, resource)).To(Succeed())
			}
		})

		AfterEach(func() {
			// TODO(user): Cleanup logic after each test, like removing the resource instance.
			resource := &operatorv1alpha1.Jumpstarter{}
			err := k8sClient.Get(ctx, typeNamespacedName, resource)
			Expect(err).NotTo(HaveOccurred())

			By("Cleanup the specific resource instance Jumpstarter")
			Expect(k8sClient.Delete(ctx, resource)).To(Succeed())
		})
		It("should successfully reconcile the resource", func() {
			By("Reconciling the created resource")
			controllerReconciler := &JumpstarterReconciler{
				Client:             k8sClient,
				Scheme:             k8sClient.Scheme(),
				EndpointReconciler: endpoints.NewReconciler(k8sClient),
			}

			_, err := controllerReconciler.Reconcile(ctx, reconcile.Request{
				NamespacedName: typeNamespacedName,
			})
			Expect(err).NotTo(HaveOccurred())
			// TODO(user): Add more specific assertions depending on your controller's reconciliation logic.
			// Example: If you expect a certain status condition after reconciliation, verify it here.
		})
	})
})

var _ = Describe("ensurePort", func() {
	DescribeTable("should handle addresses correctly",
		func(address, defaultPort, expected string) {
			result := ensurePort(address, defaultPort)
			Expect(result).To(Equal(expected))
		},
		Entry("hostname without port", "example.com", "443", "example.com:443"),
		Entry("hostname with port", "example.com:8083", "443", "example.com:8083"),
		Entry("IPv6 without port", "2001:db8::1", "443", "[2001:db8::1]:443"),
		Entry("IPv6 with port", "[2001:db8::1]:8083", "443", "[2001:db8::1]:8083"),
		Entry("malformed - too many colons", "host:port:extra", "443", "[host:port:extra]:443"),
		Entry("malformed - empty string", "", "443", ":443"),
		Entry("malformed - just colon", ":", "443", ":"),
	)
})
