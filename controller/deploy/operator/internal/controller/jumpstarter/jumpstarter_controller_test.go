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
	"fmt"
	"strings"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
	"k8s.io/utils/ptr"
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
						Replicas:        ptr.To(int32(1)),
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
						Replicas:        ptr.To(int32(1)),
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
				Replicas: ptr.To(int32(1)),
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "controller"}},
				},
			},
			Routers: operatorv1alpha1.RoutersConfig{
				Image:    "quay.io/jumpstarter/jumpstarter:latest",
				Replicas: ptr.To(int32(1)),
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

var _ = Describe("ExporterSet Controller Lifecycle", func() {
	const crName = "test-exporterset"
	var crNamespace string

	ctx := context.Background()

	makeJumpstarterSpec := func() operatorv1alpha1.JumpstarterSpec {
		return operatorv1alpha1.JumpstarterSpec{
			BaseDomain: "example.com",
			CertManager: operatorv1alpha1.CertManagerConfig{
				Enabled: false,
			},
			Controller: operatorv1alpha1.ControllerConfig{
				Image:    "quay.io/jumpstarter/jumpstarter:latest",
				Replicas: ptr.To(int32(1)),
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "controller"}},
				},
			},
			Routers: operatorv1alpha1.RoutersConfig{
				Image:    "quay.io/jumpstarter/jumpstarter:latest",
				Replicas: ptr.To(int32(1)),
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "router"}},
				},
			},
		}
	}

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

	BeforeEach(func() {
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "exporterset-test-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		crNamespace = ns.Name
	})

	AfterEach(func() {
		_ = k8sClient.Delete(ctx, &corev1.Namespace{
			ObjectMeta: metav1.ObjectMeta{Name: crNamespace},
		})
	})

	It("creates Deployment, ServiceAccount, Role, and RoleBinding for an enabled provisioner", func() {
		By("creating a Jumpstarter CR with an enabled provisioner")
		spec := makeJumpstarterSpec()
		spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
			Image:           "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
			ImagePullPolicy: corev1.PullIfNotPresent,
			Provisioners: []operatorv1alpha1.ProvisionerConfig{
				{
					Name: "qemu.jumpstarter.dev",
				},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("reconciling")
		doReconcile()

		By("verifying the Deployment exists with correct args")
		deployment := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, deployment)).To(Succeed())
		Expect(deployment.Spec.Template.Spec.Containers).To(HaveLen(1))
		container := deployment.Spec.Template.Spec.Containers[0]
		Expect(container.Args).To(ContainElement("--provisioner=qemu.jumpstarter.dev"))
		Expect(container.Image).To(Equal("quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest"))
		Expect(container.ImagePullPolicy).To(Equal(corev1.PullIfNotPresent))
		Expect(deployment.Spec.Template.Spec.ServiceAccountName).To(Equal(crName + "-exporterset-qemu-jumpstarter-dev"))

		By("verifying health and readiness probes on :8081")
		Expect(container.LivenessProbe).NotTo(BeNil())
		Expect(container.LivenessProbe.HTTPGet).NotTo(BeNil())
		Expect(container.LivenessProbe.HTTPGet.Path).To(Equal("/healthz"))
		Expect(container.LivenessProbe.HTTPGet.Port.IntValue()).To(Equal(8081))
		Expect(container.ReadinessProbe).NotTo(BeNil())
		Expect(container.ReadinessProbe.HTTPGet).NotTo(BeNil())
		Expect(container.ReadinessProbe.HTTPGet.Path).To(Equal("/readyz"))
		Expect(container.ReadinessProbe.HTTPGet.Port.IntValue()).To(Equal(8081))

		By("verifying the ServiceAccount exists")
		sa := &corev1.ServiceAccount{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, sa)).To(Succeed())

		By("verifying the Role exists with correct rules")
		role := &rbacv1.Role{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev-role",
			Namespace: crNamespace,
		}, role)).To(Succeed())
		Expect(len(role.Rules)).To(BeNumerically(">=", 8))
		apiGroups := make(map[string]bool)
		for _, rule := range role.Rules {
			for _, g := range rule.APIGroups {
				apiGroups[g] = true
			}
		}
		Expect(apiGroups).To(HaveKey("virtualtarget.jumpstarter.dev"))
		Expect(apiGroups).To(HaveKey("jumpstarter.dev"))
		Expect(apiGroups).To(HaveKey("coordination.k8s.io"))

		By("verifying the RoleBinding exists")
		rb := &rbacv1.RoleBinding{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev-rolebinding",
			Namespace: crNamespace,
		}, rb)).To(Succeed())
		Expect(rb.RoleRef.Name).To(Equal(crName + "-exporterset-qemu-jumpstarter-dev-role"))
		Expect(rb.Subjects).To(HaveLen(1))
		Expect(rb.Subjects[0].Name).To(Equal(crName + "-exporterset-qemu-jumpstarter-dev"))
	})

	It("uses per-provisioner image override when specified", func() {
		By("creating a Jumpstarter CR with a per-provisioner image override")
		spec := makeJumpstarterSpec()
		spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
			Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
			Provisioners: []operatorv1alpha1.ProvisionerConfig{
				{
					Name:  "qemu.jumpstarter.dev",
					Image: "quay.io/custom/exporterset:v1.0",
				},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("reconciling")
		doReconcile()

		By("verifying the Deployment uses the per-provisioner image")
		deployment := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, deployment)).To(Succeed())
		Expect(deployment.Spec.Template.Spec.Containers[0].Image).To(Equal("quay.io/custom/exporterset:v1.0"))
	})

	It("deletes Deployment and RBAC when provisioner is disabled, but preserves SA", func() {
		By("creating a Jumpstarter CR with an enabled provisioner")
		spec := makeJumpstarterSpec()
		enabled := true
		spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
			Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
			Provisioners: []operatorv1alpha1.ProvisionerConfig{
				{Name: "qemu.jumpstarter.dev", Enabled: &enabled},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("first reconcile — resources should be created")
		doReconcile()

		deployment := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, deployment)).To(Succeed())

		By("disabling the provisioner")
		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		disabled := false
		js.Spec.ExporterSets.Provisioners[0].Enabled = &disabled
		Expect(k8sClient.Update(ctx, js)).To(Succeed())

		By("second reconcile — deployment and owned RBAC should be deleted")
		doReconcile()

		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, deployment)
		Expect(errors.IsNotFound(err)).To(BeTrue(), "expected deployment to be deleted")

		role := &rbacv1.Role{}
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev-role",
			Namespace: crNamespace,
		}, role)
		Expect(errors.IsNotFound(err)).To(BeTrue(), "expected Role to be deleted")

		rb := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev-rolebinding",
			Namespace: crNamespace,
		}, rb)
		Expect(errors.IsNotFound(err)).To(BeTrue(), "expected RoleBinding to be deleted")

		By("verifying ServiceAccount is preserved (orphan pattern)")
		sa := &corev1.ServiceAccount{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, sa)).To(Succeed())
	})

	It("creates separate Deployments and RBAC for multiple provisioners", func() {
		By("creating a Jumpstarter CR with two provisioners")
		spec := makeJumpstarterSpec()
		spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
			Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
			Provisioners: []operatorv1alpha1.ProvisionerConfig{
				{Name: "qemu.jumpstarter.dev"},
				{Name: "corellium.jumpstarter.dev"},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("reconciling")
		doReconcile()

		By("verifying both Deployments exist with distinct provisioner flags")
		for _, tc := range []struct {
			provisioner string
			suffix      string
		}{
			{"qemu.jumpstarter.dev", "qemu-jumpstarter-dev"},
			{"corellium.jumpstarter.dev", "corellium-jumpstarter-dev"},
		} {
			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      crName + "-exporterset-" + tc.suffix,
				Namespace: crNamespace,
			}, dep)).To(Succeed(), "deployment for %s should exist", tc.provisioner)
			Expect(dep.Spec.Template.Spec.Containers[0].Args).To(
				ContainElement("--provisioner="+tc.provisioner),
				"deployment for %s should have correct --provisioner flag", tc.provisioner,
			)

			sa := &corev1.ServiceAccount{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      crName + "-exporterset-" + tc.suffix,
				Namespace: crNamespace,
			}, sa)).To(Succeed(), "SA for %s should exist", tc.provisioner)

			role := &rbacv1.Role{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      crName + "-exporterset-" + tc.suffix + "-role",
				Namespace: crNamespace,
			}, role)).To(Succeed(), "Role for %s should exist", tc.provisioner)

			rb := &rbacv1.RoleBinding{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      crName + "-exporterset-" + tc.suffix + "-rolebinding",
				Namespace: crNamespace,
			}, rb)).To(Succeed(), "RoleBinding for %s should exist", tc.provisioner)
		}
	})

	It("only creates resources for enabled provisioners in a mixed list", func() {
		By("creating a Jumpstarter CR with one enabled and one disabled provisioner")
		spec := makeJumpstarterSpec()
		enabled := true
		disabled := false
		spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
			Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
			Provisioners: []operatorv1alpha1.ProvisionerConfig{
				{Name: "qemu.jumpstarter.dev", Enabled: &enabled},
				{Name: "corellium.jumpstarter.dev", Enabled: &disabled},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("reconciling")
		doReconcile()

		By("verifying the enabled provisioner's Deployment exists")
		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, dep)).To(Succeed())

		By("verifying the disabled provisioner's Deployment does not exist")
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-corellium-jumpstarter-dev",
			Namespace: crNamespace,
		}, dep)
		Expect(errors.IsNotFound(err)).To(BeTrue(), "expected disabled provisioner deployment to not exist")
	})

	It("cleans up all ExporterSet resources when exporterSets is removed from spec", func() {
		By("creating a Jumpstarter CR with provisioners")
		spec := makeJumpstarterSpec()
		spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
			Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
			Provisioners: []operatorv1alpha1.ProvisionerConfig{
				{Name: "qemu.jumpstarter.dev"},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("first reconcile — resources created")
		doReconcile()

		By("removing exporterSets from spec")
		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		js.Spec.ExporterSets = nil
		Expect(k8sClient.Update(ctx, js)).To(Succeed())

		By("second reconcile — deployment should be cleaned up")
		doReconcile()

		deployment := &appsv1.Deployment{}
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, deployment)
		Expect(errors.IsNotFound(err)).To(BeTrue(), "expected deployment to be deleted")
	})

	It("sets ExporterSetControllersReady based on Deployment Available condition", func() {
		By("creating a Jumpstarter CR with an enabled provisioner")
		spec := makeJumpstarterSpec()
		spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
			Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
			Provisioners: []operatorv1alpha1.ProvisionerConfig{
				{Name: "qemu.jumpstarter.dev"},
			},
		}
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		By("first reconcile — deployment exists but is not Available yet")
		doReconcile()

		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeExporterSetControllersReady)
		Expect(cond).NotTo(BeNil(), "ExporterSetControllersReady condition should be set")
		Expect(cond.Status).To(Equal(metav1.ConditionFalse),
			"should be False while provisioner Deployment is not Available")

		By("marking the provisioner Deployment as Available")
		deployment := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      crName + "-exporterset-qemu-jumpstarter-dev",
			Namespace: crNamespace,
		}, deployment)).To(Succeed())
		deployment.Status.Conditions = []appsv1.DeploymentCondition{
			{
				Type:   appsv1.DeploymentAvailable,
				Status: corev1.ConditionTrue,
				Reason: "MinimumReplicasAvailable",
			},
		}
		Expect(k8sClient.Status().Update(ctx, deployment)).To(Succeed())

		By("second reconcile — condition should become True")
		doReconcile()

		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		cond = meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeExporterSetControllersReady)
		Expect(cond).NotTo(BeNil())
		Expect(cond.Status).To(Equal(metav1.ConditionTrue),
			"should be True once provisioner Deployment is Available")
		Expect(cond.Reason).To(Equal("AllControllersAvailable"))
	})
})

var _ = Describe("Scale-to-zero / Suspend", func() {
	const crName = "test-suspend"
	var crNamespace string
	ctx := context.Background()

	baseSpec := func() operatorv1alpha1.JumpstarterSpec {
		return operatorv1alpha1.JumpstarterSpec{
			BaseDomain: "example.com",
			CertManager: operatorv1alpha1.CertManagerConfig{
				Enabled: false,
			},
			Controller: operatorv1alpha1.ControllerConfig{
				Image:    "quay.io/jumpstarter/jumpstarter:latest",
				Replicas: ptr.To(int32(1)),
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "controller"}},
				},
			},
			Routers: operatorv1alpha1.RoutersConfig{
				Image:    "quay.io/jumpstarter/jumpstarter:latest",
				Replicas: ptr.To(int32(1)),
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "router"}},
				},
			},
		}
	}

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

	BeforeEach(func() {
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "suspend-test-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		crNamespace = ns.Name
	})

	AfterEach(func() {
		_ = k8sClient.Delete(ctx, &corev1.Namespace{
			ObjectMeta: metav1.ObjectMeta{Name: crNamespace},
		})
	})

	Context("Controller", func() {
		It("keeps the Deployment at 0 replicas when controller.replicas=0 (not deleted)", func() {
			By("creating a CR with controller replicas=0")
			spec := baseSpec()
			spec.Controller.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())

			doReconcile()

			By("verifying the controller Deployment exists with 0 replicas")
			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      crName + "-controller",
				Namespace: crNamespace,
			}, dep)).To(Succeed(), "controller Deployment should exist, not be deleted")
			Expect(*dep.Spec.Replicas).To(Equal(int32(0)))
		})

		It("reports ControllerDeploymentReady=True with 'suspended' message when replicas=0", func() {
			spec := baseSpec()
			spec.Controller.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())

			doReconcile()

			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeControllerDeploymentReady)
			Expect(cond).NotTo(BeNil())
			Expect(cond.Status).To(Equal(metav1.ConditionTrue))
			Expect(cond.Message).To(ContainSubstring("suspended"))
		})

		It("scales the controller Deployment back up when replicas is restored", func() {
			By("creating with replicas=0")
			spec := baseSpec()
			spec.Controller.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())
			doReconcile()

			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-controller", Namespace: crNamespace,
			}, dep)).To(Succeed())
			Expect(*dep.Spec.Replicas).To(Equal(int32(0)))

			By("restoring replicas=1")
			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			js.Spec.Controller.Replicas = ptr.To(int32(1))
			Expect(k8sClient.Update(ctx, js)).To(Succeed())
			doReconcile()

			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-controller", Namespace: crNamespace,
			}, dep)).To(Succeed())
			Expect(*dep.Spec.Replicas).To(Equal(int32(1)))
		})
	})

	Context("Routers", func() {
		It("scales existing router Deployments to 0 (not deletes) when routers.replicas=0", func() {
			By("creating with routers.replicas=1 and reconciling to create the Deployment")
			spec := baseSpec()
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())
			doReconcile()

			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-router-0", Namespace: crNamespace,
			}, dep)).To(Succeed())
			Expect(*dep.Spec.Replicas).To(Equal(int32(1)))

			By("scaling routers to 0")
			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			js.Spec.Routers.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Update(ctx, js)).To(Succeed())
			doReconcile()

			By("verifying router Deployment still exists but with 0 replicas")
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-router-0", Namespace: crNamespace,
			}, dep)).To(Succeed(), "router Deployment should not be deleted")
			Expect(*dep.Spec.Replicas).To(Equal(int32(0)))
		})

		It("preserves router Services when routers.replicas=0", func() {
			By("creating with routers.replicas=1")
			spec := baseSpec()
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())
			doReconcile()

			By("scaling routers to 0")
			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			js.Spec.Routers.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Update(ctx, js)).To(Succeed())
			doReconcile()

			By("verifying router Service is still present")
			svc := &corev1.Service{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-router-0", Namespace: crNamespace,
			}, svc)).To(Succeed(), "router Service should be preserved when suspended")
		})

		It("reports RouterDeploymentsReady=True with 'suspended' message when replicas=0", func() {
			spec := baseSpec()
			spec.Routers.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())

			doReconcile()

			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeRouterDeploymentsReady)
			Expect(cond).NotTo(BeNil())
			Expect(cond.Status).To(Equal(metav1.ConditionTrue))
			Expect(cond.Message).To(ContainSubstring("suspended"))
		})

		It("resumes routers after suspension by recreating Deployments when replicas > 0", func() {
			By("creating with replicas=1 and suspending")
			spec := baseSpec()
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())
			doReconcile()

			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			js.Spec.Routers.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Update(ctx, js)).To(Succeed())
			doReconcile()

			By("restoring replicas=1")
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			js.Spec.Routers.Replicas = ptr.To(int32(1))
			Expect(k8sClient.Update(ctx, js)).To(Succeed())
			doReconcile()

			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-router-0", Namespace: crNamespace,
			}, dep)).To(Succeed())
			Expect(*dep.Spec.Replicas).To(Equal(int32(1)))
		})

		It("resumes to 1 replica after 3->0 suspension: excess Deployments are cleaned up", func() {
			By("creating with replicas=3")
			spec := baseSpec()
			spec.Routers.Replicas = ptr.To(int32(3))
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())
			doReconcile()

			By("verifying all 3 Deployments exist")
			dep := &appsv1.Deployment{}
			for i := 0; i < 3; i++ {
				Expect(k8sClient.Get(ctx, types.NamespacedName{
					Name: fmt.Sprintf("%s-router-%d", crName, i), Namespace: crNamespace,
				}, dep)).To(Succeed())
			}

			By("suspending all routers (replicas=0) — Deployments scaled to 0 but not deleted")
			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			js.Spec.Routers.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Update(ctx, js)).To(Succeed())
			doReconcile()

			for i := 0; i < 3; i++ {
				Expect(k8sClient.Get(ctx, types.NamespacedName{
					Name: fmt.Sprintf("%s-router-%d", crName, i), Namespace: crNamespace,
				}, dep)).To(Succeed(), "Deployment router-%d should exist (suspended, not deleted)", i)
				Expect(*dep.Spec.Replicas).To(Equal(int32(0)))
			}

			By("resuming at replicas=1 — excess suspended Deployments (router-1, router-2) are cleaned up")
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			js.Spec.Routers.Replicas = ptr.To(int32(1))
			Expect(k8sClient.Update(ctx, js)).To(Succeed())
			doReconcile()

			By("verifying router-0 is active (replicas=1)")
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-router-0", Namespace: crNamespace,
			}, dep)).To(Succeed())
			Expect(*dep.Spec.Replicas).To(Equal(int32(1)))

			By("verifying router-1 and router-2 are deleted (cleanup of excess suspended Deployments)")
			for i := 1; i < 3; i++ {
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name: fmt.Sprintf("%s-router-%d", crName, i), Namespace: crNamespace,
				}, dep)
				Expect(errors.IsNotFound(err)).To(BeTrue(),
					"excess suspended Deployment router-%d should be deleted on resume", i)
			}
		})
	})

	Context("ExporterSet provisioner", func() {
		It("keeps the provisioner Deployment at 0 replicas when replicas=0 (not deleted)", func() {
			By("creating a provisioner with replicas=0")
			spec := baseSpec()
			zero := int32(0)
			spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
				Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
				Provisioners: []operatorv1alpha1.ProvisionerConfig{
					{Name: "qemu.jumpstarter.dev", Replicas: &zero},
				},
			}
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())

			doReconcile()

			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      crName + "-exporterset-qemu-jumpstarter-dev",
				Namespace: crNamespace,
			}, dep)).To(Succeed(), "provisioner Deployment should exist at 0 replicas, not be deleted")
			Expect(*dep.Spec.Replicas).To(Equal(int32(0)))
		})

		It("does not block readiness check for suspended provisioners (replicas=0)", func() {
			By("creating two provisioners: one active, one suspended")
			spec := baseSpec()
			zero := int32(0)
			spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
				Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
				Provisioners: []operatorv1alpha1.ProvisionerConfig{
					{Name: "qemu.jumpstarter.dev"},                       // active (replicas defaults to 1)
					{Name: "corellium.jumpstarter.dev", Replicas: &zero}, // suspended
				},
			}
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())

			doReconcile()

			By("marking the active provisioner as Available (the suspended one is ignored)")
			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name:      crName + "-exporterset-qemu-jumpstarter-dev",
				Namespace: crNamespace,
			}, dep)).To(Succeed())
			dep.Status.Conditions = []appsv1.DeploymentCondition{
				{Type: appsv1.DeploymentAvailable, Status: corev1.ConditionTrue, Reason: "MinimumReplicasAvailable"},
			}
			Expect(k8sClient.Status().Update(ctx, dep)).To(Succeed())

			doReconcile()

			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeExporterSetControllersReady)
			Expect(cond).NotTo(BeNil())
			Expect(cond.Status).To(Equal(metav1.ConditionTrue),
				"suspended provisioner should not block overall ExporterSetControllersReady")
		})

		It("clears a stale ExporterSetControllersReady=False when all provisioners are suspended", func() {
			By("creating an active provisioner that is not yet Available (condition will be False)")
			spec := baseSpec()
			spec.ExporterSets = &operatorv1alpha1.ExporterSetsConfig{
				Image: "quay.io/jumpstarter-dev/jumpstarter-exporterset-controller:latest",
				Provisioners: []operatorv1alpha1.ProvisionerConfig{
					{Name: "qemu.jumpstarter.dev"},
				},
			}
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())

			doReconcile()

			By("verifying ExporterSetControllersReady=False (deployment not Available)")
			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeExporterSetControllersReady)
			Expect(cond).NotTo(BeNil())
			Expect(cond.Status).To(Equal(metav1.ConditionFalse))

			By("suspending all provisioners (replicas=0)")
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			zero := int32(0)
			js.Spec.ExporterSets.Provisioners[0].Replicas = &zero
			Expect(k8sClient.Update(ctx, js)).To(Succeed())

			doReconcile()

			By("verifying ExporterSetControllersReady is now True/Suspended (stale False cleared)")
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			cond = meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeExporterSetControllersReady)
			Expect(cond).NotTo(BeNil())
			Expect(cond.Status).To(Equal(metav1.ConditionTrue),
				"stale False condition should be cleared when all provisioners are suspended")
			Expect(cond.Reason).To(Equal("Suspended"))
		})
	})

	Context("Suspended reason", func() {
		It("uses Suspended reason (not DeploymentAvailable) when controller.replicas=0", func() {
			spec := baseSpec()
			spec.Controller.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())

			doReconcile()

			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeControllerDeploymentReady)
			Expect(cond).NotTo(BeNil())
			Expect(cond.Reason).To(Equal("Suspended"),
				"reason should be Suspended, not DeploymentAvailable")
		})

		It("uses Suspended reason (not AllDeploymentsAvailable) when routers.replicas=0", func() {
			spec := baseSpec()
			spec.Routers.Replicas = ptr.To(int32(0))
			Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
				ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
				Spec:       spec,
			})).To(Succeed())

			doReconcile()

			js := &operatorv1alpha1.Jumpstarter{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
			cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeRouterDeploymentsReady)
			Expect(cond).NotTo(BeNil())
			Expect(cond.Reason).To(Equal("Suspended"),
				"reason should be Suspended, not AllDeploymentsAvailable")
		})
	})
})

var _ = Describe("sanitizeProvisionerName", func() {
	It("replaces dots with dashes and lowercases", func() {
		Expect(sanitizeProvisionerName("qemu.jumpstarter.dev")).To(Equal("qemu-jumpstarter-dev"))
		Expect(sanitizeProvisionerName("corellium.jumpstarter.dev")).To(Equal("corellium-jumpstarter-dev"))
		Expect(sanitizeProvisionerName("UPPER.Case")).To(Equal("upper-case"))
	})

	It("strips characters invalid in DNS-1123 labels", func() {
		Expect(sanitizeProvisionerName("foo_bar")).To(Equal("foobar"))
		Expect(sanitizeProvisionerName("a/b")).To(Equal("ab"))
		Expect(sanitizeProvisionerName("with spaces")).To(Equal("withspaces"))
	})

	It("trims leading and trailing dashes", func() {
		Expect(sanitizeProvisionerName(".leading")).To(Equal("leading"))
		Expect(sanitizeProvisionerName("trailing.")).To(Equal("trailing"))
		Expect(sanitizeProvisionerName("..both..")).To(Equal("both"))
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
