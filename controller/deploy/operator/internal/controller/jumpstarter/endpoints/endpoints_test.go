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

package endpoints

import (
	"context"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("Endpoints Reconciler", func() {
	Context("When reconciling an endpoint", func() {
		const (
			namespace    = "test-namespace"
			endpointName = "test-endpoint"
		)

		ctx := context.Background()
		var reconciler *Reconciler

		BeforeEach(func() {
			reconciler = NewReconciler(k8sClient)

			// Create the test namespace
			ns := &corev1.Namespace{
				ObjectMeta: metav1.ObjectMeta{
					Name: namespace,
				},
			}
			err := k8sClient.Create(ctx, ns)
			if err != nil && !errors.IsAlreadyExists(err) {
				Expect(err).NotTo(HaveOccurred())
			}
		})

		Context("with ClusterIP service type", func() {
			It("should create a ClusterIP service successfully", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: endpointName,
				}

				svcPort := corev1.ServicePort{
					Name:       endpointName,
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileEndpoint(ctx, namespace, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was created
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      endpointName,
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeClusterIP))
				Expect(service.Labels["app"]).To(Equal(endpointName))
			})
		})

		Context("with LoadBalancer service type", func() {
			It("should create a LoadBalancer service successfully", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: endpointName,
					LoadBalancer: &operatorv1alpha1.LoadBalancerConfig{
						Enabled:     true,
						Annotations: map[string]string{"service.beta.kubernetes.io/aws-load-balancer-type": "nlb"},
						Labels:      map[string]string{"environment": "production"},
					},
				}

				svcPort := corev1.ServicePort{
					Name:       endpointName,
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileEndpoint(ctx, namespace, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was created
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      endpointName,
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeLoadBalancer))
				Expect(service.Annotations["service.beta.kubernetes.io/aws-load-balancer-type"]).To(Equal("nlb"))
				Expect(service.Labels["environment"]).To(Equal("production"))
			})
		})

		Context("with NodePort service type", func() {
			It("should create a NodePort service successfully", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: endpointName,
					NodePort: &operatorv1alpha1.NodePortConfig{
						Enabled:     true,
						Port:        30090,
						Annotations: map[string]string{"nodeport.kubernetes.io/port": "30090"},
						Labels:      map[string]string{"nodeport": "true"},
					},
				}

				svcPort := corev1.ServicePort{
					Name:       endpointName,
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileEndpoint(ctx, namespace, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was created
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      endpointName,
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeNodePort))
				Expect(service.Annotations["nodeport.kubernetes.io/port"]).To(Equal("30090"))
				Expect(service.Labels["nodeport"]).To(Equal("true"))
			})
		})

		Context("with invalid configuration", func() {
			It("should return an error when both LoadBalancer and NodePort are enabled", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: endpointName,
					LoadBalancer: &operatorv1alpha1.LoadBalancerConfig{
						Enabled: true,
					},
					NodePort: &operatorv1alpha1.NodePortConfig{
						Enabled: true,
					},
				}

				svcPort := corev1.ServicePort{
					Name:       endpointName,
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileEndpoint(ctx, namespace, endpoint, svcPort)
				Expect(err).To(HaveOccurred())
				Expect(err.Error()).To(ContainSubstring("both LoadBalancer and NodePort are enabled"))
			})
		})

		Context("when updating an existing service", func() {
			It("should update the service when configuration changes", func() {
				// Create initial service
				endpoint := &operatorv1alpha1.Endpoint{
					Address: endpointName,
					LoadBalancer: &operatorv1alpha1.LoadBalancerConfig{
						Enabled:     true,
						Annotations: map[string]string{"initial": "annotation"},
						Labels:      map[string]string{"initial": "label"},
					},
				}

				svcPort := corev1.ServicePort{
					Name:       endpointName,
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileEndpoint(ctx, namespace, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Update the endpoint configuration
				endpoint.LoadBalancer.Annotations["updated"] = "annotation"
				endpoint.LoadBalancer.Labels["updated"] = "label"

				err = reconciler.ReconcileEndpoint(ctx, namespace, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was updated
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      endpointName,
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Annotations["updated"]).To(Equal("annotation"))
				Expect(service.Labels["updated"]).To(Equal("label"))
			})
		})

		AfterEach(func() {
			// Clean up created services
			service := &corev1.Service{
				ObjectMeta: metav1.ObjectMeta{
					Name:      endpointName,
					Namespace: namespace,
				},
			}
			_ = k8sClient.Delete(ctx, service)
		})
	})

})
