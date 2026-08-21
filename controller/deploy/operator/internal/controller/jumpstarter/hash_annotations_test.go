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
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("secretDataHash", func() {
	It("should produce a deterministic hash", func() {
		secret := &corev1.Secret{
			Data: map[string][]byte{
				"tls.crt": []byte("cert-data"),
				"tls.key": []byte("key-data"),
			},
		}
		hash1 := secretDataHash(secret)
		hash2 := secretDataHash(secret)
		Expect(hash1).To(Equal(hash2))
		Expect(hash1).To(HaveLen(64))
	})

	It("should produce different hashes for different data", func() {
		s1 := &corev1.Secret{
			Data: map[string][]byte{"tls.crt": []byte("cert-a")},
		}
		s2 := &corev1.Secret{
			Data: map[string][]byte{"tls.crt": []byte("cert-b")},
		}
		Expect(secretDataHash(s1)).NotTo(Equal(secretDataHash(s2)))
	})

	It("should not collide when key/value boundaries differ", func() {
		// Without length prefixes these concatenate to the same byte stream: "axbby".
		s1 := &corev1.Secret{
			Data: map[string][]byte{
				"a": []byte("xb"),
				"b": []byte("y"),
			},
		}
		s2 := &corev1.Secret{
			Data: map[string][]byte{
				"a": []byte("x"),
				"b": []byte("by"),
			},
		}
		Expect(secretDataHash(s1)).NotTo(Equal(secretDataHash(s2)))
	})

	It("should be order-independent", func() {
		s1 := &corev1.Secret{
			Data: map[string][]byte{
				"aaa": []byte("1"),
				"zzz": []byte("2"),
			},
		}
		s2 := &corev1.Secret{
			Data: map[string][]byte{
				"zzz": []byte("2"),
				"aaa": []byte("1"),
			},
		}
		Expect(secretDataHash(s1)).To(Equal(secretDataHash(s2)))
	})

	It("should handle empty data", func() {
		secret := &corev1.Secret{Data: map[string][]byte{}}
		hash := secretDataHash(secret)
		Expect(hash).To(HaveLen(64))
	})
})

var _ = Describe("configMapDataHash", func() {
	It("should produce a deterministic hash", func() {
		cm := &corev1.ConfigMap{
			Data: map[string]string{
				"config.yaml": "key: value",
			},
		}
		hash1 := configMapDataHash(cm)
		hash2 := configMapDataHash(cm)
		Expect(hash1).To(Equal(hash2))
		Expect(hash1).To(HaveLen(64))
	})

	It("should produce different hashes for different data", func() {
		cm1 := &corev1.ConfigMap{Data: map[string]string{"k": "v1"}}
		cm2 := &corev1.ConfigMap{Data: map[string]string{"k": "v2"}}
		Expect(configMapDataHash(cm1)).NotTo(Equal(configMapDataHash(cm2)))
	})

	It("should not collide when key/value boundaries differ", func() {
		// Without length prefixes these concatenate to the same byte stream: "axbby".
		cm1 := &corev1.ConfigMap{Data: map[string]string{"a": "xb", "b": "y"}}
		cm2 := &corev1.ConfigMap{Data: map[string]string{"a": "x", "b": "by"}}
		Expect(configMapDataHash(cm1)).NotTo(Equal(configMapDataHash(cm2)))
	})
})

var _ = Describe("buildControllerPodAnnotations", func() {
	var r *JumpstarterReconciler

	BeforeEach(func() {
		r = &JumpstarterReconciler{}
	})

	It("should always include configmap hash", func() {
		js := &operatorv1alpha1.Jumpstarter{}
		annotations := r.buildControllerPodAnnotations(js, "cm-hash", "")
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/configmap-sha256", "cm-hash"))
		Expect(annotations).NotTo(HaveKey("jumpstarter.dev/tls-secret-sha256"))
	})

	It("should include TLS hash when non-empty", func() {
		js := &operatorv1alpha1.Jumpstarter{}
		annotations := r.buildControllerPodAnnotations(js, "cm-hash", "tls-hash")
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/configmap-sha256", "cm-hash"))
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/tls-secret-sha256", "tls-hash"))
	})

	It("should include user-provided pod annotations", func() {
		js := &operatorv1alpha1.Jumpstarter{
			Spec: operatorv1alpha1.JumpstarterSpec{
				Controller: operatorv1alpha1.ControllerConfig{
					PodAnnotations: map[string]string{
						"custom.io/key": "custom-value",
					},
				},
			},
		}
		annotations := r.buildControllerPodAnnotations(js, "cm-hash", "tls-hash")
		Expect(annotations).To(HaveKeyWithValue("custom.io/key", "custom-value"))
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/configmap-sha256", "cm-hash"))
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/tls-secret-sha256", "tls-hash"))
	})

	It("should not allow user annotations to override operator annotations", func() {
		js := &operatorv1alpha1.Jumpstarter{
			Spec: operatorv1alpha1.JumpstarterSpec{
				Controller: operatorv1alpha1.ControllerConfig{
					PodAnnotations: map[string]string{
						"jumpstarter.dev/configmap-sha256":  "user-override",
						"jumpstarter.dev/tls-secret-sha256": "user-tls-override",
					},
				},
			},
		}
		annotations := r.buildControllerPodAnnotations(js, "real-hash", "real-tls-hash")
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/configmap-sha256", "real-hash"))
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/tls-secret-sha256", "real-tls-hash"))
	})
})

var _ = Describe("buildRouterPodAnnotations", func() {
	var r *JumpstarterReconciler

	BeforeEach(func() {
		r = &JumpstarterReconciler{}
	})

	It("should return nil when no TLS hash and no user annotations", func() {
		js := &operatorv1alpha1.Jumpstarter{}
		annotations := r.buildRouterPodAnnotations(js, "")
		Expect(annotations).To(BeNil())
	})

	It("should include TLS hash when non-empty", func() {
		js := &operatorv1alpha1.Jumpstarter{}
		annotations := r.buildRouterPodAnnotations(js, "tls-hash")
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/tls-secret-sha256", "tls-hash"))
	})

	It("should include user-provided pod annotations", func() {
		js := &operatorv1alpha1.Jumpstarter{
			Spec: operatorv1alpha1.JumpstarterSpec{
				Routers: operatorv1alpha1.RoutersConfig{
					PodAnnotations: map[string]string{
						"prometheus.io/scrape": "true",
					},
				},
			},
		}
		annotations := r.buildRouterPodAnnotations(js, "tls-hash")
		Expect(annotations).To(HaveKeyWithValue("prometheus.io/scrape", "true"))
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/tls-secret-sha256", "tls-hash"))
	})

	It("should return non-nil when only user annotations are set", func() {
		js := &operatorv1alpha1.Jumpstarter{
			Spec: operatorv1alpha1.JumpstarterSpec{
				Routers: operatorv1alpha1.RoutersConfig{
					PodAnnotations: map[string]string{
						"custom/key": "value",
					},
				},
			},
		}
		annotations := r.buildRouterPodAnnotations(js, "")
		Expect(annotations).To(HaveKeyWithValue("custom/key", "value"))
	})

	It("should not allow user annotations to override operator TLS hash", func() {
		js := &operatorv1alpha1.Jumpstarter{
			Spec: operatorv1alpha1.JumpstarterSpec{
				Routers: operatorv1alpha1.RoutersConfig{
					PodAnnotations: map[string]string{
						"jumpstarter.dev/tls-secret-sha256": "user-tls-override",
					},
				},
			},
		}
		annotations := r.buildRouterPodAnnotations(js, "real-tls-hash")
		Expect(annotations).To(HaveKeyWithValue("jumpstarter.dev/tls-secret-sha256", "real-tls-hash"))
	})
})

var _ = Describe("getControllerTLSSecretHash", func() {
	var r *JumpstarterReconciler

	BeforeEach(func() {
		r = &JumpstarterReconciler{Client: k8sClient}
	})

	It("should return empty hash when no TLS is configured", func() {
		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test",
				Namespace: "default",
			},
		}
		hash, err := r.getControllerTLSSecretHash(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(hash).To(BeEmpty())
	})

	It("should return empty hash when secret does not exist yet", func() {
		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test",
				Namespace: "default",
			},
			Spec: operatorv1alpha1.JumpstarterSpec{
				Controller: operatorv1alpha1.ControllerConfig{
					GRPC: operatorv1alpha1.GRPCConfig{
						TLS: operatorv1alpha1.TLSConfig{
							CertSecret: "nonexistent-secret",
						},
					},
				},
			},
		}
		hash, err := r.getControllerTLSSecretHash(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(hash).To(BeEmpty())
	})

	It("should return hash when secret exists", func() {
		secret := &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "controller-tls-test",
				Namespace: "default",
			},
			Data: map[string][]byte{
				"tls.crt": []byte("test-cert"),
				"tls.key": []byte("test-key"),
			},
		}
		Expect(k8sClient.Create(ctx, secret)).To(Succeed())
		defer func() {
			Expect(k8sClient.Delete(ctx, secret)).To(Succeed())
		}()

		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test",
				Namespace: "default",
			},
			Spec: operatorv1alpha1.JumpstarterSpec{
				Controller: operatorv1alpha1.ControllerConfig{
					GRPC: operatorv1alpha1.GRPCConfig{
						TLS: operatorv1alpha1.TLSConfig{
							CertSecret: "controller-tls-test",
						},
					},
				},
			},
		}
		hash, err := r.getControllerTLSSecretHash(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(hash).To(HaveLen(64))
	})
})

var _ = Describe("getRouterTLSSecretHash", func() {
	var r *JumpstarterReconciler

	BeforeEach(func() {
		r = &JumpstarterReconciler{Client: k8sClient}
	})

	It("should return empty hash when no TLS is configured", func() {
		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test",
				Namespace: "default",
			},
		}
		hash, err := r.getRouterTLSSecretHash(ctx, js, 0)
		Expect(err).NotTo(HaveOccurred())
		Expect(hash).To(BeEmpty())
	})

	It("should return empty hash when secret does not exist yet", func() {
		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test",
				Namespace: "default",
			},
			Spec: operatorv1alpha1.JumpstarterSpec{
				Routers: operatorv1alpha1.RoutersConfig{
					GRPC: operatorv1alpha1.GRPCConfig{
						TLS: operatorv1alpha1.TLSConfig{
							CertSecret: "nonexistent-router-secret",
						},
					},
				},
			},
		}
		hash, err := r.getRouterTLSSecretHash(ctx, js, 0)
		Expect(err).NotTo(HaveOccurred())
		Expect(hash).To(BeEmpty())
	})

	It("should return hash when shared CertSecret exists", func() {
		secret := &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "router-tls-shared",
				Namespace: "default",
			},
			Data: map[string][]byte{
				"tls.crt": []byte("router-cert"),
				"tls.key": []byte("router-key"),
			},
		}
		Expect(k8sClient.Create(ctx, secret)).To(Succeed())
		defer func() {
			Expect(k8sClient.Delete(ctx, secret)).To(Succeed())
		}()

		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test",
				Namespace: "default",
			},
			Spec: operatorv1alpha1.JumpstarterSpec{
				Routers: operatorv1alpha1.RoutersConfig{
					GRPC: operatorv1alpha1.GRPCConfig{
						TLS: operatorv1alpha1.TLSConfig{
							CertSecret: "router-tls-shared",
						},
					},
				},
			},
		}
		hash0, err := r.getRouterTLSSecretHash(ctx, js, 0)
		Expect(err).NotTo(HaveOccurred())
		Expect(hash0).To(HaveLen(64))

		// Shared secret path ignores replica index
		hash1, err := r.getRouterTLSSecretHash(ctx, js, 1)
		Expect(err).NotTo(HaveOccurred())
		Expect(hash1).To(Equal(hash0))
	})

	It("should return hash for cert-manager per-replica secret", func() {
		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "cm-router",
				Namespace: "default",
			},
			Spec: operatorv1alpha1.JumpstarterSpec{
				CertManager: operatorv1alpha1.CertManagerConfig{
					Enabled: true,
				},
			},
		}

		secret := &corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{
				Name:      GetRouterCertSecretName(js, 0),
				Namespace: "default",
			},
			Data: map[string][]byte{
				"tls.crt": []byte("cm-router-cert"),
				"tls.key": []byte("cm-router-key"),
			},
		}
		Expect(k8sClient.Create(ctx, secret)).To(Succeed())
		defer func() {
			Expect(k8sClient.Delete(ctx, secret)).To(Succeed())
		}()

		hash, err := r.getRouterTLSSecretHash(ctx, js, 0)
		Expect(err).NotTo(HaveOccurred())
		Expect(hash).To(HaveLen(64))

		// Different replica looks up a different secret name
		hashMissing, err := r.getRouterTLSSecretHash(ctx, js, 1)
		Expect(err).NotTo(HaveOccurred())
		Expect(hashMissing).To(BeEmpty())
	})
})
