/*
Copyright 2025. The Jumpstarter Authors.

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
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("isExternalIssuer", func() {
	It("should return false when Server is nil", func() {
		js := &operatorv1alpha1.Jumpstarter{
			Spec: operatorv1alpha1.JumpstarterSpec{
				CertManager: operatorv1alpha1.CertManagerConfig{
					Enabled: true,
				},
			},
		}
		Expect(isExternalIssuer(js)).To(BeFalse())
	})

	It("should return false when IssuerRef is nil (self-signed)", func() {
		js := &operatorv1alpha1.Jumpstarter{
			Spec: operatorv1alpha1.JumpstarterSpec{
				CertManager: operatorv1alpha1.CertManagerConfig{
					Enabled: true,
					Server: &operatorv1alpha1.ServerCertConfig{
						SelfSigned: &operatorv1alpha1.SelfSignedConfig{
							Enabled: true,
						},
					},
				},
			},
		}
		Expect(isExternalIssuer(js)).To(BeFalse())
	})

	It("should return true when IssuerRef is set", func() {
		js := &operatorv1alpha1.Jumpstarter{
			Spec: operatorv1alpha1.JumpstarterSpec{
				CertManager: operatorv1alpha1.CertManagerConfig{
					Enabled: true,
					Server: &operatorv1alpha1.ServerCertConfig{
						IssuerRef: &operatorv1alpha1.IssuerReference{
							Name: "letsencrypt-prod",
							Kind: "ClusterIssuer",
						},
					},
				},
			},
		}
		Expect(isExternalIssuer(js)).To(BeTrue())
	})
})

var _ = Describe("collectControllerDNSNames", func() {
	var r *JumpstarterReconciler

	BeforeEach(func() {
		r = &JumpstarterReconciler{}
	})

	Context("with includeInternalNames=true (self-signed mode)", func() {
		It("should include internal K8s service DNS names", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
				},
			}

			names := r.collectControllerDNSNames(js, true)
			Expect(names).To(ContainElements(
				"jumpstarter-controller",
				"jumpstarter-controller.test-ns",
				"jumpstarter-controller.test-ns.svc",
				"jumpstarter-controller.test-ns.svc.cluster.local",
				"grpc.example.com",
			))
		})

		It("should include both internal names and endpoint names", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
					Controller: operatorv1alpha1.ControllerConfig{
						GRPC: operatorv1alpha1.GRPCConfig{
							Endpoints: []operatorv1alpha1.Endpoint{
								{Address: "grpc.custom.example.com:443"},
							},
						},
					},
				},
			}

			names := r.collectControllerDNSNames(js, true)
			Expect(names).To(ContainElements(
				"jumpstarter-controller",
				"jumpstarter-controller.test-ns",
				"jumpstarter-controller.test-ns.svc",
				"jumpstarter-controller.test-ns.svc.cluster.local",
				"grpc.custom.example.com",
				"grpc.example.com",
			))
		})
	})

	Context("with includeInternalNames=false (external issuer mode)", func() {
		It("should NOT include internal K8s service DNS names", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
				},
			}

			names := r.collectControllerDNSNames(js, false)
			Expect(names).NotTo(ContainElement("jumpstarter-controller"))
			Expect(names).NotTo(ContainElement("jumpstarter-controller.test-ns"))
			Expect(names).NotTo(ContainElement("jumpstarter-controller.test-ns.svc"))
			Expect(names).NotTo(ContainElement("jumpstarter-controller.test-ns.svc.cluster.local"))
			Expect(names).To(ContainElement("grpc.example.com"))
		})

		It("should include only endpoint and baseDomain names", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
					Controller: operatorv1alpha1.ControllerConfig{
						GRPC: operatorv1alpha1.GRPCConfig{
							Endpoints: []operatorv1alpha1.Endpoint{
								{Address: "grpc.custom.example.com:443"},
							},
						},
					},
				},
			}

			names := r.collectControllerDNSNames(js, false)
			Expect(names).To(ConsistOf(
				"grpc.custom.example.com",
				"grpc.example.com",
			))
		})

		It("should return only baseDomain name when no endpoints configured", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
				},
			}

			names := r.collectControllerDNSNames(js, false)
			Expect(names).To(ConsistOf("grpc.example.com"))
		})
	})
})

var _ = Describe("collectRouterDNSNames", func() {
	var r *JumpstarterReconciler

	BeforeEach(func() {
		r = &JumpstarterReconciler{}
	})

	Context("with includeInternalNames=true (self-signed mode)", func() {
		It("should include internal K8s service DNS names", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
				},
			}

			names := r.collectRouterDNSNames(js, 0, true)
			Expect(names).To(ContainElements(
				"jumpstarter-router-0",
				"jumpstarter-router-0.test-ns",
				"jumpstarter-router-0.test-ns.svc",
				"jumpstarter-router-0.test-ns.svc.cluster.local",
				"router-0.example.com",
			))
		})

		It("should include both internal names and endpoint names with replica substitution", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
					Routers: operatorv1alpha1.RoutersConfig{
						GRPC: operatorv1alpha1.GRPCConfig{
							Endpoints: []operatorv1alpha1.Endpoint{
								{Address: "router-$(replica).custom.example.com:443"},
							},
						},
					},
				},
			}

			names := r.collectRouterDNSNames(js, 2, true)
			Expect(names).To(ContainElements(
				"jumpstarter-router-2",
				"jumpstarter-router-2.test-ns",
				"jumpstarter-router-2.test-ns.svc",
				"jumpstarter-router-2.test-ns.svc.cluster.local",
				"router-2.custom.example.com",
				"router-2.example.com",
			))
		})
	})

	Context("with includeInternalNames=false (external issuer mode)", func() {
		It("should NOT include internal K8s service DNS names", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
				},
			}

			names := r.collectRouterDNSNames(js, 0, false)
			Expect(names).NotTo(ContainElement("jumpstarter-router-0"))
			Expect(names).NotTo(ContainElement("jumpstarter-router-0.test-ns"))
			Expect(names).NotTo(ContainElement("jumpstarter-router-0.test-ns.svc"))
			Expect(names).NotTo(ContainElement("jumpstarter-router-0.test-ns.svc.cluster.local"))
			Expect(names).To(ContainElement("router-0.example.com"))
		})

		It("should include only endpoint and baseDomain names", func() {
			js := &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter",
					Namespace: "test-ns",
				},
				Spec: operatorv1alpha1.JumpstarterSpec{
					BaseDomain: "example.com",
					Routers: operatorv1alpha1.RoutersConfig{
						GRPC: operatorv1alpha1.GRPCConfig{
							Endpoints: []operatorv1alpha1.Endpoint{
								{Address: "router-$(replica).custom.example.com:443"},
							},
						},
					},
				},
			}

			names := r.collectRouterDNSNames(js, 1, false)
			Expect(names).To(ConsistOf(
				"router-1.custom.example.com",
				"router-1.example.com",
			))
		})
	})
})
