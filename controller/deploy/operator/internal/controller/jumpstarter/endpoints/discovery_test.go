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
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

// createTestJumpstarterSpec creates a JumpstarterSpec with the given baseDomain for testing
func createTestJumpstarterSpec(baseDomain string) *operatorv1alpha1.JumpstarterSpec {
	return &operatorv1alpha1.JumpstarterSpec{
		BaseDomain: baseDomain,
	}
}

// createOpenShiftIngressConfig creates an OpenShift Ingress cluster config for testing
func createOpenShiftIngressConfig(domain string) *unstructured.Unstructured {
	ingress := &unstructured.Unstructured{}
	ingress.SetGroupVersionKind(schema.GroupVersionKind{
		Group:   "config.openshift.io",
		Version: "v1",
		Kind:    "Ingress",
	})
	ingress.SetName("cluster")
	ingress.Object["spec"] = map[string]interface{}{
		"domain": domain,
	}
	return ingress
}

var _ = Describe("detectOpenShiftBaseDomain", func() {
	// Note: These tests require OpenShift CRDs to be available in the test environment.
	// They will be skipped if the CRDs are not present, which is expected in non-OpenShift environments.

	Context("when OpenShift is available", func() {
		BeforeEach(func() {
			// Check if OpenShift CRDs are available
			ingress := createOpenShiftIngressConfig("test-check.apps.example.com")
			err := k8sClient.Create(ctx, ingress)
			if err != nil {
				Skip("Skipping OpenShift baseDomain auto-detection tests: OpenShift CRDs not available in test environment")
			}
			Expect(k8sClient.Delete(ctx, ingress)).To(Succeed())
		})

		Context("when OpenShift Ingress cluster config exists", func() {
			It("should successfully auto-detect baseDomain", func() {
				ingress := createOpenShiftIngressConfig("apps.example.com")
				Expect(k8sClient.Create(ctx, ingress)).To(Succeed())
				DeferCleanup(func() { _ = k8sClient.Delete(ctx, ingress) })

				Expect(detectOpenShiftBaseDomain(cfg)).To(Equal("apps.example.com"))
			})
		})

		Context("when OpenShift Ingress cluster config has empty domain", func() {
			It("should return empty string", func() {
				ingress := createOpenShiftIngressConfig("")
				Expect(k8sClient.Create(ctx, ingress)).To(Succeed())
				DeferCleanup(func() { _ = k8sClient.Delete(ctx, ingress) })

				Expect(detectOpenShiftBaseDomain(cfg)).To(Equal(""))
			})
		})

		Context("when OpenShift Ingress cluster config has no spec.domain", func() {
			It("should return empty string", func() {
				// Create a mock OpenShift Ingress cluster config without domain field
				ingress := &unstructured.Unstructured{}
				ingress.SetGroupVersionKind(schema.GroupVersionKind{
					Group:   "config.openshift.io",
					Version: "v1",
					Kind:    "Ingress",
				})
				ingress.SetName("cluster")
				ingress.Object["spec"] = map[string]interface{}{}

				Expect(k8sClient.Create(ctx, ingress)).To(Succeed())
				DeferCleanup(func() { _ = k8sClient.Delete(ctx, ingress) })

				Expect(detectOpenShiftBaseDomain(cfg)).To(Equal(""))
			})
		})
	})

	Context("when OpenShift Ingress cluster config does not exist", func() {
		It("should return empty string", func() {
			// Try to auto-detect when no Ingress config exists
			// This test will work even without OpenShift CRDs because it just checks the fallback behavior
			detectedDomain := detectOpenShiftBaseDomain(cfg)
			Expect(detectedDomain).To(Equal(""))
		})
	})
})

var _ = Describe("DefaultBaseDomain in Reconciler", func() {
	Context("when baseDomain is auto-detected", func() {
		It("should apply default baseDomain in ApplyDefaults when spec.BaseDomain is empty", func() {
			reconciler := NewReconciler(k8sClient, k8sClient.Scheme(), cfg)

			// Manually set a default baseDomain for testing
			reconciler.DefaultBaseDomain = "apps.example.com"

			// Create a spec with empty baseDomain
			spec := createTestJumpstarterSpec("")

			// Apply defaults with a namespace
			reconciler.ApplyDefaults(spec, "test-namespace")

			// Should use the default baseDomain with namespace prefix
			Expect(spec.BaseDomain).To(Equal("jumpstarter.test-namespace.apps.example.com"))
		})

		It("should not override user-provided baseDomain", func() {
			reconciler := NewReconciler(k8sClient, k8sClient.Scheme(), cfg)

			// Set a default baseDomain
			reconciler.DefaultBaseDomain = "apps.example.com"

			// Create a spec with user-provided baseDomain
			spec := createTestJumpstarterSpec("user.custom.domain")

			// Apply defaults
			reconciler.ApplyDefaults(spec, "test-namespace")

			// Should keep the user-provided baseDomain
			Expect(spec.BaseDomain).To(Equal("user.custom.domain"))
		})

		It("should not set baseDomain when DefaultBaseDomain is empty", func() {
			reconciler := NewReconciler(k8sClient, k8sClient.Scheme(), cfg)

			// No default baseDomain set (simulating non-OpenShift cluster)
			reconciler.DefaultBaseDomain = ""

			// Create a spec with empty baseDomain
			spec := createTestJumpstarterSpec("")

			// Apply defaults
			reconciler.ApplyDefaults(spec, "test-namespace")

			// baseDomain should remain empty
			Expect(spec.BaseDomain).To(Equal(""))
		})
	})
})
