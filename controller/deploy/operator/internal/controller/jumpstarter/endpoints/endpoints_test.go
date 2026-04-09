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
	networkingv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("Endpoints Reconciler", func() {
	Context("When reconciling controller endpoints", func() {
		const (
			namespace      = "test-namespace"
			controllerName = "test-controller"
		)

		ctx := context.Background()
		var reconciler *Reconciler
		var owner *corev1.ConfigMap // Use ConfigMap as a simple owner object for testing

		BeforeEach(func() {
			reconciler = NewReconciler(k8sClient, k8sClient.Scheme(), cfg)

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

			// Create an owner object for testing
			owner = &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      controllerName,
					Namespace: namespace,
				},
			}
			err = k8sClient.Create(ctx, owner)
			if err != nil && !errors.IsAlreadyExists(err) {
				Expect(err).NotTo(HaveOccurred())
			}
		})

		Context("with ClusterIP service type", func() {
			It("should create a ClusterIP service successfully", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "controller",
					ClusterIP: &operatorv1alpha1.ClusterIPConfig{
						Enabled: true,
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "controller",
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was created
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller",
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeClusterIP))
				Expect(service.Spec.Selector["app"]).To(Equal("jumpstarter-controller"))
				Expect(service.Labels["app"]).To(Equal("jumpstarter-controller"))
			})
		})

		Context("with LoadBalancer service type", func() {
			It("should create a LoadBalancer service successfully", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "controller",
					LoadBalancer: &operatorv1alpha1.LoadBalancerConfig{
						Enabled:     true,
						Annotations: map[string]string{"service.beta.kubernetes.io/aws-load-balancer-type": "nlb"},
						Labels:      map[string]string{"environment": "production"},
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "controller",
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was created with -lb suffix
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-lb",
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeLoadBalancer))
				Expect(service.Annotations["service.beta.kubernetes.io/aws-load-balancer-type"]).To(Equal("nlb"))
				Expect(service.Labels["environment"]).To(Equal("production"))
				Expect(service.Spec.Selector["app"]).To(Equal("jumpstarter-controller"))
			})
		})

		Context("with NodePort service type", func() {
			It("should create a NodePort service successfully", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "controller",
					NodePort: &operatorv1alpha1.NodePortConfig{
						Enabled:     true,
						Port:        30090,
						Annotations: map[string]string{"nodeport.kubernetes.io/port": "30090"},
						Labels:      map[string]string{"nodeport": "true"},
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "controller",
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was created with -np suffix
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-np",
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeNodePort))
				Expect(service.Annotations["nodeport.kubernetes.io/port"]).To(Equal("30090"))
				Expect(service.Labels["nodeport"]).To(Equal("true"))
				Expect(service.Spec.Selector["app"]).To(Equal("jumpstarter-controller"))
			})
		})

		Context("with multiple service types enabled", func() {
			It("should create all enabled service types", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "controller",
					LoadBalancer: &operatorv1alpha1.LoadBalancerConfig{
						Enabled: true,
					},
					NodePort: &operatorv1alpha1.NodePortConfig{
						Enabled: true,
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "controller",
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify LoadBalancer service was created
				lbService := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-lb",
					Namespace: namespace,
				}, lbService)
				Expect(err).NotTo(HaveOccurred())
				Expect(lbService.Spec.Type).To(Equal(corev1.ServiceTypeLoadBalancer))

				// Verify NodePort service was created
				npService := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-np",
					Namespace: namespace,
				}, npService)
				Expect(err).NotTo(HaveOccurred())
				Expect(npService.Spec.Type).To(Equal(corev1.ServiceTypeNodePort))
			})
		})

		Context("when updating an existing service", func() {
			It("should update the service when configuration changes", func() {
				// Create initial service
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "controller",
					LoadBalancer: &operatorv1alpha1.LoadBalancerConfig{
						Enabled:     true,
						Annotations: map[string]string{"initial": "annotation"},
						Labels:      map[string]string{"initial": "label"},
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "controller",
					Port:       9090,
					TargetPort: intstr.FromInt(9090),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Update the endpoint configuration
				endpoint.LoadBalancer.Annotations["updated"] = "annotation"
				endpoint.LoadBalancer.Labels["updated"] = "label"

				err = reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was updated
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-lb",
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Annotations["updated"]).To(Equal("annotation"))
				Expect(service.Labels["updated"]).To(Equal("label"))
			})
		})

		Context("with Ingress enabled", func() {
			It("should create a ClusterIP service and Ingress with default nginx annotations", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "grpc.example.com:443",
					Ingress: &operatorv1alpha1.IngressConfig{
						Enabled: true,
						Class:   "nginx",
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "controller-grpc",
					Port:       8082,
					TargetPort: intstr.FromInt(8082),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the ClusterIP service was created (used by ingress)
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-grpc",
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeClusterIP))
				Expect(service.Spec.Selector["app"]).To(Equal("jumpstarter-controller"))

				// Verify the Ingress was created
				ingress := &networkingv1.Ingress{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-grpc-ing",
					Namespace: namespace,
				}, ingress)
				Expect(err).NotTo(HaveOccurred())

				// Verify ingress class
				Expect(ingress.Spec.IngressClassName).NotTo(BeNil())
				Expect(*ingress.Spec.IngressClassName).To(Equal("nginx"))

				// Verify default nginx annotations for TLS passthrough
				Expect(ingress.Annotations["nginx.ingress.kubernetes.io/ssl-redirect"]).To(Equal("true"))
				Expect(ingress.Annotations["nginx.ingress.kubernetes.io/backend-protocol"]).To(Equal("GRPC"))
				Expect(ingress.Annotations["nginx.ingress.kubernetes.io/proxy-read-timeout"]).To(Equal("300"))
				Expect(ingress.Annotations["nginx.ingress.kubernetes.io/proxy-send-timeout"]).To(Equal("300"))
				Expect(ingress.Annotations["nginx.ingress.kubernetes.io/ssl-passthrough"]).To(Equal("true"))

				// Verify ingress rules
				Expect(ingress.Spec.Rules).To(HaveLen(1))
				Expect(ingress.Spec.Rules[0].Host).To(Equal("grpc.example.com"))
				Expect(ingress.Spec.Rules[0].HTTP.Paths).To(HaveLen(1))
				Expect(ingress.Spec.Rules[0].HTTP.Paths[0].Backend.Service.Name).To(Equal("controller-grpc"))
				Expect(ingress.Spec.Rules[0].HTTP.Paths[0].Backend.Service.Port.Number).To(Equal(int32(8082)))

				// Verify TLS config
				Expect(ingress.Spec.TLS).To(HaveLen(1))
				Expect(ingress.Spec.TLS[0].Hosts).To(ContainElement("grpc.example.com"))
			})

			It("should merge user annotations with defaults (user takes precedence)", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "grpc.example.com",
					Ingress: &operatorv1alpha1.IngressConfig{
						Enabled: true,
						Class:   "nginx",
						Annotations: map[string]string{
							"nginx.ingress.kubernetes.io/ssl-redirect": "false", // override default
							"custom.annotation/key":                    "custom-value",
						},
						Labels: map[string]string{
							"environment": "production",
						},
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "controller-grpc",
					Port:       8082,
					TargetPort: intstr.FromInt(8082),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the Ingress was created
				ingress := &networkingv1.Ingress{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-grpc-ing",
					Namespace: namespace,
				}, ingress)
				Expect(err).NotTo(HaveOccurred())

				// User annotation should override default
				Expect(ingress.Annotations["nginx.ingress.kubernetes.io/ssl-redirect"]).To(Equal("false"))
				// Custom annotation should be present
				Expect(ingress.Annotations["custom.annotation/key"]).To(Equal("custom-value"))
				// Other defaults should still be present
				Expect(ingress.Annotations["nginx.ingress.kubernetes.io/backend-protocol"]).To(Equal("GRPC"))

				// User labels should be present
				Expect(ingress.Labels["environment"]).To(Equal("production"))
			})

			It("should extract hostname from various address formats", func() {
				testCases := []struct {
					address      string
					expectedHost string
				}{
					{"grpc.example.com", "grpc.example.com"},
					{"grpc.example.com:443", "grpc.example.com"},
					{"grpc.example.com:8080", "grpc.example.com"},
				}

				for _, tc := range testCases {
					endpoint := &operatorv1alpha1.Endpoint{
						Address: tc.address,
						Ingress: &operatorv1alpha1.IngressConfig{
							Enabled: true,
						},
					}

					svcPort := corev1.ServicePort{
						Name:       "test-svc",
						Port:       8082,
						TargetPort: intstr.FromInt(8082),
						Protocol:   corev1.ProtocolTCP,
					}

					err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
					Expect(err).NotTo(HaveOccurred())

					// Verify ingress was created with correct hostname
					ingress := &networkingv1.Ingress{}
					err = k8sClient.Get(ctx, types.NamespacedName{
						Name:      "test-svc-ing",
						Namespace: namespace,
					}, ingress)
					Expect(err).NotTo(HaveOccurred())
					Expect(ingress.Spec.Rules[0].Host).To(Equal(tc.expectedHost))

					// Clean up
					_ = k8sClient.Delete(ctx, ingress)
					svc := &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: "test-svc", Namespace: namespace}}
					_ = k8sClient.Delete(ctx, svc)
				}
			})

			It("should not set ingress class when not specified", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "grpc.example.com",
					Ingress: &operatorv1alpha1.IngressConfig{
						Enabled: true,
						// Class not specified - will use cluster default
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "controller-grpc",
					Port:       8082,
					TargetPort: intstr.FromInt(8082),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileControllerEndpoint(ctx, owner, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify ingress class is nil (will use cluster default IngressClass)
				ingress := &networkingv1.Ingress{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-grpc-ing",
					Namespace: namespace,
				}, ingress)
				Expect(err).NotTo(HaveOccurred())
				Expect(ingress.Spec.IngressClassName).To(BeNil())
			})
		})

		AfterEach(func() {
			// Clean up created services
			services := []string{"controller", "controller-lb", "controller-np", "controller-grpc", "test-svc"}
			for _, svcName := range services {
				service := &corev1.Service{
					ObjectMeta: metav1.ObjectMeta{
						Name:      svcName,
						Namespace: namespace,
					},
				}
				_ = k8sClient.Delete(ctx, service)
			}

			// Clean up ingresses
			ingresses := []string{"controller-grpc-ing", "test-svc-ing"}
			for _, ingName := range ingresses {
				ingress := &networkingv1.Ingress{
					ObjectMeta: metav1.ObjectMeta{
						Name:      ingName,
						Namespace: namespace,
					},
				}
				_ = k8sClient.Delete(ctx, ingress)
			}

			// Clean up owner
			_ = k8sClient.Delete(ctx, owner)
		})
	})

	Context("When reconciling router replica endpoints", func() {
		const (
			namespace   = "test-namespace"
			routerName  = "test-router"
			replicaIdx  = int32(0)
			endpointIdx = 0
		)

		ctx := context.Background()
		var reconciler *Reconciler
		var owner *corev1.ConfigMap

		BeforeEach(func() {
			reconciler = NewReconciler(k8sClient, k8sClient.Scheme(), cfg)

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

			// Create an owner object for testing
			owner = &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      routerName,
					Namespace: namespace,
				},
			}
			err = k8sClient.Create(ctx, owner)
			if err != nil && !errors.IsAlreadyExists(err) {
				Expect(err).NotTo(HaveOccurred())
			}
		})

		Context("with proper pod selector", func() {
			It("should create a service with correct pod selector matching deployment labels", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "router",
					NodePort: &operatorv1alpha1.NodePortConfig{
						Enabled: true,
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "router",
					Port:       8083,
					TargetPort: intstr.FromInt(8083),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileRouterReplicaEndpoint(ctx, owner, replicaIdx, endpointIdx, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the service was created with correct pod selector
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "router-np",
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				// The selector should be "app: test-router-router-0" (owner.Name + "-router-" + replicaIndex)
				Expect(service.Spec.Selector["app"]).To(Equal("test-router-router-0"))
				Expect(service.Labels["router"]).To(Equal(routerName))
				Expect(service.Labels["router-index"]).To(Equal("0"))
			})
		})

		Context("with Ingress enabled for router", func() {
			It("should create a ClusterIP service and Ingress for router replica", func() {
				endpoint := &operatorv1alpha1.Endpoint{
					Address: "router-0.example.com",
					Ingress: &operatorv1alpha1.IngressConfig{
						Enabled: true,
						Class:   "nginx",
						Annotations: map[string]string{
							"router.annotation": "value",
						},
					},
				}

				svcPort := corev1.ServicePort{
					Name:       "router-grpc",
					Port:       8083,
					TargetPort: intstr.FromInt(8083),
					Protocol:   corev1.ProtocolTCP,
				}

				err := reconciler.ReconcileRouterReplicaEndpoint(ctx, owner, replicaIdx, endpointIdx, endpoint, svcPort)
				Expect(err).NotTo(HaveOccurred())

				// Verify the ClusterIP service was created
				service := &corev1.Service{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "router-grpc",
					Namespace: namespace,
				}, service)
				Expect(err).NotTo(HaveOccurred())
				Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeClusterIP))
				Expect(service.Spec.Selector["app"]).To(Equal("test-router-router-0"))

				// Verify the Ingress was created
				ingress := &networkingv1.Ingress{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      "router-grpc-ing",
					Namespace: namespace,
				}, ingress)
				Expect(err).NotTo(HaveOccurred())

				// Verify ingress configuration
				Expect(*ingress.Spec.IngressClassName).To(Equal("nginx"))
				Expect(ingress.Spec.Rules[0].Host).To(Equal("router-0.example.com"))
				Expect(ingress.Spec.Rules[0].HTTP.Paths[0].Backend.Service.Name).To(Equal("router-grpc"))

				// Verify user and default annotations
				Expect(ingress.Annotations["router.annotation"]).To(Equal("value"))
				Expect(ingress.Annotations["nginx.ingress.kubernetes.io/ssl-passthrough"]).To(Equal("true"))
			})
		})

		AfterEach(func() {
			// Clean up created services
			services := []string{"router", "router-lb", "router-np", "router-ing", "router-route", "router-grpc"}
			for _, svcName := range services {
				service := &corev1.Service{
					ObjectMeta: metav1.ObjectMeta{
						Name:      svcName,
						Namespace: namespace,
					},
				}
				_ = k8sClient.Delete(ctx, service)
			}

			// Clean up ingresses
			ingresses := []string{"router-grpc-ing"}
			for _, ingName := range ingresses {
				ingress := &networkingv1.Ingress{
					ObjectMeta: metav1.ObjectMeta{
						Name:      ingName,
						Namespace: namespace,
					},
				}
				_ = k8sClient.Delete(ctx, ingress)
			}

			// Clean up owner
			_ = k8sClient.Delete(ctx, owner)
		})
	})

})
