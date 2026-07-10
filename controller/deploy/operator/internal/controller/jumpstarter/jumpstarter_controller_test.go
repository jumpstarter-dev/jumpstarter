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
	"strings"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/internal/controller/jumpstarter/endpoints"
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
						BaseDomain: "example.com",
						CertManager: operatorv1alpha1.CertManagerConfig{
							Enabled: false, // Disable for unit tests - cert-manager CRDs not available in envtest
						},
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
				EndpointReconciler: endpoints.NewReconciler(k8sClient, k8sClient.Scheme(), cfg),
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

var _ = Describe("Jumpstarter Controller — JWT CA resolution", func() {
	const caSecretName = "test-ca-secret"
	const caCMName = "test-ca-cm"
	const caKey = "tls.crt"
	const caKeyPEM = "ca.crt"
	const crName = "test-jwt-ca"

	// Each test gets its own namespace so fixed-name resources like
	// jumpstarter-service-ca-cert don't collide with other CRs in "default".
	var crNamespace string

	ctx := context.Background()

	makeJumpstarterSpec := func() operatorv1alpha1.JumpstarterSpec {
		return operatorv1alpha1.JumpstarterSpec{
			BaseDomain: "example.com",
			CertManager: operatorv1alpha1.CertManagerConfig{
				Enabled: false, // cert-manager CRDs are not available in envtest
			},
			Controller: operatorv1alpha1.ControllerConfig{
				Image:    "quay.io/jumpstarter/jumpstarter:latest",
				Replicas: 1,
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "controller"}},
				},
			},
			Routers: operatorv1alpha1.RoutersConfig{
				Image:    "quay.io/jumpstarter/jumpstarter:latest",
				Replicas: 1,
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "router"}},
				},
			},
		}
	}

	BeforeEach(func() {
		// Create an isolated namespace for each test so fixed-name resources
		// (jumpstarter-service-ca-cert, jumpstarter-controller, etc.) don't
		// collide with those owned by the test-resource CR in "default".
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "jwt-ca-test-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		crNamespace = ns.Name
	})

	doReconcile := func() {
		r := &JumpstarterReconciler{
			Client:             k8sClient,
			Scheme:             k8sClient.Scheme(),
			EndpointReconciler: endpoints.NewReconciler(k8sClient, k8sClient.Scheme(), cfg),
		}
		_, err := r.Reconcile(ctx, reconcile.Request{
			NamespacedName: types.NamespacedName{Name: crName, Namespace: crNamespace},
		})
		Expect(err).NotTo(HaveOccurred())
	}

	getConfigData := func() string {
		cm := &corev1.ConfigMap{}
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      "jumpstarter-controller",
			Namespace: crNamespace,
		}, cm)
		Expect(err).NotTo(HaveOccurred())
		return cm.Data["config"]
	}

	AfterEach(func() {
		// Delete the namespace — this cascades to all owned resources.
		_ = k8sClient.Delete(ctx, &corev1.Namespace{
			ObjectMeta: metav1.ObjectMeta{Name: crNamespace},
		})
	})

	It("inlines the CA PEM from a Secret reference into the controller ConfigMap", func() {
		By("creating a CA Secret")
		Expect(k8sClient.Create(ctx, &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{Name: caSecretName, Namespace: crNamespace},
			Data:       map[string][]byte{caKey: []byte(testPEM)},
		})).To(Succeed())

		By("creating a Jumpstarter CR with a certificateAuthoritySecret reference")
		spec := makeJumpstarterSpec()
		spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"jumpstarter-cli"},
					},
					ClaimMappings: apiserverv1beta1.ClaimMappings{
						Username: apiserverv1beta1.PrefixedClaimOrExpression{
							Claim:  "preferred_username",
							Prefix: strPtr("oidc:"),
						},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: caSecretName,
					Key:  caKey,
				},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("reconciling")
		doReconcile()

		By("verifying the CA PEM is inlined in the controller ConfigMap")
		Expect(getConfigData()).To(ContainSubstring("BEGIN CERTIFICATE"))
	})

	It("inlines the CA PEM from a ConfigMap reference into the controller ConfigMap", func() {
		By("creating a CA ConfigMap")
		Expect(k8sClient.Create(ctx, &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{Name: caCMName, Namespace: crNamespace},
			Data:       map[string]string{caKeyPEM: testPEM},
		})).To(Succeed())

		By("creating a Jumpstarter CR with a certificateAuthorityConfigMap reference")
		spec := makeJumpstarterSpec()
		spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"jumpstarter-cli"},
					},
					ClaimMappings: apiserverv1beta1.ClaimMappings{
						Username: apiserverv1beta1.PrefixedClaimOrExpression{
							Claim:  "preferred_username",
							Prefix: strPtr("oidc:"),
						},
					},
				},
				CertificateAuthorityConfigMap: &operatorv1alpha1.ConfigMapKeySelector{
					Name: caCMName,
					Key:  caKeyPEM,
				},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("reconciling")
		doReconcile()

		By("verifying the CA PEM is inlined in the controller ConfigMap")
		Expect(getConfigData()).To(ContainSubstring("BEGIN CERTIFICATE"))
	})

	It("returns an error when the referenced Secret does not exist", func() {
		By("creating a Jumpstarter CR referencing a non-existent Secret")
		spec := makeJumpstarterSpec()
		spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"jumpstarter-cli"},
					},
					ClaimMappings: apiserverv1beta1.ClaimMappings{
						Username: apiserverv1beta1.PrefixedClaimOrExpression{
							Claim:  "preferred_username",
							Prefix: strPtr("oidc:"),
						},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "no-such-secret",
					Key:  "tls.crt",
				},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("reconciling — expect error due to missing Secret")
		r := &JumpstarterReconciler{
			Client:             k8sClient,
			Scheme:             k8sClient.Scheme(),
			EndpointReconciler: endpoints.NewReconciler(k8sClient, k8sClient.Scheme(), cfg),
		}
		_, err := r.Reconcile(ctx, reconcile.Request{
			NamespacedName: types.NamespacedName{Name: crName, Namespace: crNamespace},
		})
		Expect(err).To(HaveOccurred())
		Expect(strings.ToLower(err.Error())).To(ContainSubstring("no-such-secret"))
	})

	It("updates the ConfigMap when the CA Secret is rotated", func() {
		By("creating the initial CA Secret")
		secret := &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{Name: caSecretName, Namespace: crNamespace},
			Data:       map[string][]byte{caKey: []byte(testPEM)},
		}
		Expect(k8sClient.Create(ctx, secret)).To(Succeed())

		By("creating the Jumpstarter CR")
		spec := makeJumpstarterSpec()
		spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"jumpstarter-cli"},
					},
					ClaimMappings: apiserverv1beta1.ClaimMappings{
						Username: apiserverv1beta1.PrefixedClaimOrExpression{
							Claim:  "preferred_username",
							Prefix: strPtr("oidc:"),
						},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: caSecretName,
					Key:  caKey,
				},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("first reconcile — original cert")
		doReconcile()
		firstConfig := getConfigData()
		Expect(firstConfig).To(ContainSubstring("BEGIN CERTIFICATE"))

		By("rotating the CA Secret")
		secret.Data[caKey] = []byte(testPEM2)
		Expect(k8sClient.Update(ctx, secret)).To(Succeed())

		By("second reconcile — should pick up the rotated cert")
		doReconcile()
		secondConfig := getConfigData()
		Expect(secondConfig).To(ContainSubstring("BEGIN CERTIFICATE"))
		Expect(secondConfig).NotTo(Equal(firstConfig))
	})
})

// strPtr is a helper to create a pointer to a string literal.
func strPtr(s string) *string {
	return &s
}

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
