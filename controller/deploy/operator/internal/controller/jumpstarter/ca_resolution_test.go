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
	"context"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("resolveJWTAuthenticators", func() {
	var (
		ctx           context.Context
		testNamespace string
		reconciler    *JumpstarterReconciler
		js            *operatorv1alpha1.Jumpstarter
	)

	BeforeEach(func() {
		ctx = context.Background()

		// Create a unique namespace for each test to ensure isolation.
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "ca-resolution-test-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		testNamespace = ns.Name

		// Seed a Secret and ConfigMap in the test namespace.
		Expect(k8sClient.Create(ctx, &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{Name: "local-ca-secret", Namespace: testNamespace},
			Data:       map[string][]byte{"tls.crt": []byte(testPEM)},
		})).To(Succeed())
		Expect(k8sClient.Create(ctx, &corev1.ConfigMap{
			ObjectMeta: metav1.ObjectMeta{Name: "local-ca-cm", Namespace: testNamespace},
			Data:       map[string]string{"ca.crt": testPEM},
		})).To(Succeed())

		reconciler = &JumpstarterReconciler{Client: k8sClient}

		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test-js",
				Namespace: testNamespace,
			},
		}
	})

	AfterEach(func() {
		_ = k8sClient.Delete(ctx, &corev1.Namespace{
			ObjectMeta: metav1.ObjectMeta{Name: testNamespace},
		})
	})

	It("passes through a JWT entry with no CA reference unchanged", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:                  "https://issuer.example.com",
						Audiences:            []string{"aud"},
						CertificateAuthority: "inline-pem",
					},
				},
			},
		}

		result, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(result).To(HaveLen(1))
		Expect(result[0].Issuer.CertificateAuthority).To(Equal("inline-pem"))
	})

	It("resolves a Secret reference in the same namespace", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "local-ca-secret",
					Key:  "tls.crt",
				},
			},
		}

		result, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(result).To(HaveLen(1))
		Expect(result[0].Issuer.CertificateAuthority).To(Equal(testPEM))
	})

	It("uses the default key tls.crt when Key is omitted for a Secret reference", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "local-ca-secret",
				},
			},
		}

		result, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(result).To(HaveLen(1))
		Expect(result[0].Issuer.CertificateAuthority).To(Equal(testPEM))
	})

	It("resolves a ConfigMap reference in the same namespace", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthorityConfigMap: &operatorv1alpha1.ConfigMapKeySelector{
					Name: "local-ca-cm",
					Key:  "ca.crt",
				},
			},
		}

		result, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(result).To(HaveLen(1))
		Expect(result[0].Issuer.CertificateAuthority).To(Equal(testPEM))
	})

	It("uses the default key ca.crt when Key is omitted for a ConfigMap reference", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthorityConfigMap: &operatorv1alpha1.ConfigMapKeySelector{
					Name: "local-ca-cm",
				},
			},
		}

		result, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(result).To(HaveLen(1))
		Expect(result[0].Issuer.CertificateAuthority).To(Equal(testPEM))
	})

	It("Secret reference takes precedence over ConfigMap reference when both are set", func() {
		// Update the ConfigMap with different content so the assertion is meaningful.
		cm := &corev1.ConfigMap{}
		Expect(k8sClient.Get(ctx, client.ObjectKey{Name: "local-ca-cm", Namespace: testNamespace}, cm)).To(Succeed())
		cm.Data["ca.crt"] = testPEM2
		Expect(k8sClient.Update(ctx, cm)).To(Succeed())

		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "local-ca-secret",
					Key:  "tls.crt",
				},
				CertificateAuthorityConfigMap: &operatorv1alpha1.ConfigMapKeySelector{
					Name: "local-ca-cm",
					Key:  "ca.crt",
				},
			},
		}

		result, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		// Secret (testPEM) must win over ConfigMap (testPEM2).
		Expect(result[0].Issuer.CertificateAuthority).To(Equal(testPEM))
	})

	It("returns an error when the referenced Secret does not exist", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "does-not-exist",
					Key:  "tls.crt",
				},
			},
		}

		_, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).To(HaveOccurred())
		Expect(err.Error()).To(ContainSubstring("jwt[0]"))
		Expect(err.Error()).To(ContainSubstring("certificateAuthoritySecret"))
		Expect(err.Error()).To(ContainSubstring("does-not-exist"))
	})

	It("returns an error when the referenced key is missing from the Secret", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "local-ca-secret",
					Key:  "wrong-key",
				},
			},
		}

		_, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).To(HaveOccurred())
		Expect(err.Error()).To(ContainSubstring("jwt[0]"))
		Expect(err.Error()).To(ContainSubstring(`key "wrong-key" not found`))
	})

	It("returns an error when the referenced ConfigMap does not exist", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthorityConfigMap: &operatorv1alpha1.ConfigMapKeySelector{
					Name: "missing-cm",
					Key:  "ca.crt",
				},
			},
		}

		_, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).To(HaveOccurred())
		Expect(err.Error()).To(ContainSubstring("jwt[0]"))
		Expect(err.Error()).To(ContainSubstring("certificateAuthorityConfigMap"))
		Expect(err.Error()).To(ContainSubstring("missing-cm"))
	})

	It("returns an error when the referenced key is missing from the ConfigMap", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer.example.com",
						Audiences: []string{"aud"},
					},
				},
				CertificateAuthorityConfigMap: &operatorv1alpha1.ConfigMapKeySelector{
					Name: "local-ca-cm",
					Key:  "wrong-key",
				},
			},
		}

		_, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).To(HaveOccurred())
		Expect(err.Error()).To(ContainSubstring(`key "wrong-key" not found`))
	})

	It("resolves multiple JWT entries independently", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer1.example.com",
						Audiences: []string{"aud1"},
					},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "local-ca-secret",
					Key:  "tls.crt",
				},
			},
			{
				// Second entry has no CA reference — inline value should pass through unchanged.
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer2.example.com",
						Audiences: []string{"aud2"},
					},
				},
			},
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://issuer3.example.com",
						Audiences: []string{"aud3"},
					},
				},
				CertificateAuthorityConfigMap: &operatorv1alpha1.ConfigMapKeySelector{
					Name: "local-ca-cm",
					Key:  "ca.crt",
				},
			},
		}

		result, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(result).To(HaveLen(3))
		Expect(result[0].Issuer.CertificateAuthority).To(Equal(testPEM))
		Expect(result[1].Issuer.CertificateAuthority).To(BeEmpty())
		Expect(result[2].Issuer.CertificateAuthority).To(Equal(testPEM))
	})

	It("returns an error on the second entry and includes the correct index", func() {
		js.Spec.Authentication.JWT = []operatorv1alpha1.JWTAuthenticatorConfig{
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{URL: "https://ok.example.com", Audiences: []string{"aud"}},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "local-ca-secret",
					Key:  "tls.crt",
				},
			},
			{
				JWTAuthenticator: apiserverv1beta1.JWTAuthenticator{
					Issuer: apiserverv1beta1.Issuer{URL: "https://bad.example.com", Audiences: []string{"aud"}},
				},
				CertificateAuthoritySecret: &operatorv1alpha1.SecretKeySelector{
					Name: "no-such-secret",
					Key:  "tls.crt",
				},
			},
		}

		_, err := reconciler.resolveJWTAuthenticators(ctx, js)
		Expect(err).To(HaveOccurred())
		Expect(err.Error()).To(ContainSubstring("jwt[1]"))
	})
})
