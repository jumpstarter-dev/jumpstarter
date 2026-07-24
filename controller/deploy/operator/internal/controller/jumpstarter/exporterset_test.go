/*
Copyright 2026 by the Jumpstarter Authors

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
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/utils/ptr"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("defaultExporterSetControllerResources", func() {
	It("should return defaults when spec is empty", func() {
		result := defaultExporterSetControllerResources(corev1.ResourceRequirements{})

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("100m")))
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("256Mi")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("500m")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("512Mi")))
	})

	It("should return user-specified resources when requests are set", func() {
		custom := corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("250m"),
			},
		}

		result := defaultExporterSetControllerResources(custom)

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("250m")))
		Expect(result.Limits).To(BeNil())
	})

	It("should return user-specified resources when limits are set", func() {
		custom := corev1.ResourceRequirements{
			Limits: corev1.ResourceList{
				corev1.ResourceMemory: resource.MustParse("1Gi"),
			},
		}

		result := defaultExporterSetControllerResources(custom)

		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("1Gi")))
		Expect(result.Requests).To(BeNil())
	})

	It("should return user-specified resources when both requests and limits are set", func() {
		custom := corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("200m"),
				corev1.ResourceMemory: resource.MustParse("512Mi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("2"),
				corev1.ResourceMemory: resource.MustParse("2Gi"),
			},
		}

		result := defaultExporterSetControllerResources(custom)

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("200m")))
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("512Mi")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("2")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("2Gi")))
	})

	It("should preserve claims-only input without applying defaults", func() {
		custom := corev1.ResourceRequirements{
			Claims: []corev1.ResourceClaim{
				{Name: "gpu"},
			},
		}

		result := defaultExporterSetControllerResources(custom)

		Expect(result.Claims).To(HaveLen(1))
		Expect(result.Claims[0].Name).To(Equal("gpu"))
		Expect(result.Requests).To(BeNil())
		Expect(result.Limits).To(BeNil())
	})
})

var _ = Describe("exporterSetPolicyRules", func() {
	rules := exporterSetPolicyRules()

	apiGroupResources := func() map[string][]string {
		m := make(map[string][]string)
		for _, rule := range rules {
			for _, g := range rule.APIGroups {
				m[g] = append(m[g], rule.Resources...)
			}
		}
		return m
	}

	It("should contain rules for all required API groups", func() {
		groups := apiGroupResources()
		Expect(groups).To(HaveKey("virtualtarget.jumpstarter.dev"))
		Expect(groups).To(HaveKey("jumpstarter.dev"))
		Expect(groups).To(HaveKey(""))
		Expect(groups).To(HaveKey("coordination.k8s.io"))
	})

	It("should grant read-only access on exportersets (no create/update/delete)", func() {
		for _, rule := range rules {
			if containsString(rule.APIGroups, "virtualtarget.jumpstarter.dev") &&
				containsString(rule.Resources, "exportersets") &&
				!containsString(rule.Resources, "exportersets/status") &&
				!containsString(rule.Resources, "exportersets/scale") &&
				!containsString(rule.Resources, "exportersets/finalizers") {
				Expect(rule.Verbs).To(ContainElements("get", "list", "watch"))
				Expect(rule.Verbs).NotTo(ContainElement("create"))
				Expect(rule.Verbs).NotTo(ContainElement("update"))
				Expect(rule.Verbs).NotTo(ContainElement("patch"))
				Expect(rule.Verbs).NotTo(ContainElement("delete"))
				return
			}
		}
		Fail("no rule found granting read-only access on exportersets")
	})

	It("should grant status/scale/finalizer access on exportersets", func() {
		foundStatusScale := false
		foundFinalizers := false
		for _, rule := range rules {
			if containsString(rule.APIGroups, "virtualtarget.jumpstarter.dev") {
				if containsString(rule.Resources, "exportersets/status") {
					Expect(rule.Resources).To(ContainElement("exportersets/scale"))
					Expect(rule.Verbs).To(ContainElements("get", "update", "patch"))
					foundStatusScale = true
				}
				if containsString(rule.Resources, "exportersets/finalizers") {
					Expect(rule.Verbs).To(ContainElement("update"))
					foundFinalizers = true
				}
			}
		}
		Expect(foundStatusScale).To(BeTrue(), "missing exportersets/status and exportersets/scale rule")
		Expect(foundFinalizers).To(BeTrue(), "missing exportersets/finalizers rule")
	})

	It("should grant read-only access on virtualtargetclasses", func() {
		for _, rule := range rules {
			if containsString(rule.APIGroups, "virtualtarget.jumpstarter.dev") &&
				containsString(rule.Resources, "virtualtargetclasses") {
				Expect(rule.Verbs).To(ContainElements("get", "list", "watch"))
				Expect(rule.Verbs).NotTo(ContainElement("delete"))
				return
			}
		}
		Fail("no rule found for virtualtargetclasses")
	})

	It("should grant full CRUD on pods", func() {
		for _, rule := range rules {
			if containsString(rule.APIGroups, "") &&
				containsString(rule.Resources, "pods") {
				Expect(rule.Verbs).To(ContainElements("get", "list", "watch", "create", "update", "patch", "delete"))
				return
			}
		}
		Fail("no rule found granting full CRUD on pods")
	})

	It("should grant full CRUD on exporters", func() {
		for _, rule := range rules {
			if containsString(rule.APIGroups, "jumpstarter.dev") &&
				containsString(rule.Resources, "exporters") {
				Expect(rule.Verbs).To(ContainElements("get", "list", "watch", "create", "update", "patch", "delete"))
				return
			}
		}
		Fail("no rule found granting full CRUD on exporters")
	})

	It("should grant read-only access on jumpstarter leases", func() {
		for _, rule := range rules {
			if containsString(rule.APIGroups, "jumpstarter.dev") &&
				containsString(rule.Resources, "leases") {
				Expect(rule.Verbs).To(ContainElements("get", "list", "watch"))
				Expect(rule.Verbs).NotTo(ContainElement("delete"))
				return
			}
		}
		Fail("no rule found for jumpstarter.dev leases")
	})

	It("should grant full CRUD on coordination leases for leader election", func() {
		for _, rule := range rules {
			if containsString(rule.APIGroups, "coordination.k8s.io") &&
				containsString(rule.Resources, "leases") {
				Expect(rule.Verbs).To(ContainElements("get", "list", "watch", "create", "update", "patch", "delete"))
				return
			}
		}
		Fail("no rule found for coordination.k8s.io leases")
	})

	It("should grant event create/patch access", func() {
		for _, rule := range rules {
			if containsString(rule.APIGroups, "") &&
				containsString(rule.Resources, "events") {
				Expect(rule.Verbs).To(ContainElements("create", "patch"))
				return
			}
		}
		Fail("no rule found for events")
	})

	It("should grant read-only access on secrets and configmaps", func() {
		for _, rule := range rules {
			if containsString(rule.APIGroups, "") &&
				containsString(rule.Resources, "secrets") {
				Expect(rule.Verbs).To(ContainElements("get", "list", "watch"))
				Expect(rule.Verbs).NotTo(ContainElement("delete"))
				return
			}
		}
		Fail("no rule found for secrets/configmaps")
	})
})

var _ = Describe("hasEnabledProvisioners", func() {
	It("should return false for empty list", func() {
		Expect(hasEnabledProvisioners(nil)).To(BeFalse())
		Expect(hasEnabledProvisioners([]operatorv1alpha1.ProvisionerConfig{})).To(BeFalse())
	})

	It("should return true when Enabled is nil (default enabled)", func() {
		provs := []operatorv1alpha1.ProvisionerConfig{
			{Name: "qemu.jumpstarter.dev"},
		}
		Expect(hasEnabledProvisioners(provs)).To(BeTrue())
	})

	It("should return true when Enabled is explicitly true", func() {
		provs := []operatorv1alpha1.ProvisionerConfig{
			{Name: "qemu.jumpstarter.dev", Enabled: ptr.To(true)},
		}
		Expect(hasEnabledProvisioners(provs)).To(BeTrue())
	})

	It("should return false when all provisioners are disabled", func() {
		provs := []operatorv1alpha1.ProvisionerConfig{
			{Name: "qemu.jumpstarter.dev", Enabled: ptr.To(false)},
			{Name: "corellium.jumpstarter.dev", Enabled: ptr.To(false)},
		}
		Expect(hasEnabledProvisioners(provs)).To(BeFalse())
	})

	It("should return true when at least one provisioner is enabled among disabled ones", func() {
		provs := []operatorv1alpha1.ProvisionerConfig{
			{Name: "qemu.jumpstarter.dev", Enabled: ptr.To(false)},
			{Name: "corellium.jumpstarter.dev", Enabled: ptr.To(true)},
		}
		Expect(hasEnabledProvisioners(provs)).To(BeTrue())
	})
})

var _ = Describe("createExporterSetServiceAccount", func() {
	var r *JumpstarterReconciler
	var js *operatorv1alpha1.Jumpstarter

	BeforeEach(func() {
		r = &JumpstarterReconciler{Scheme: k8sClient.Scheme()}
		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: "my-js", Namespace: "ns1"},
		}
	})

	It("should use correct naming convention", func() {
		sa := r.createExporterSetServiceAccount(js, "qemu-jumpstarter-dev")

		Expect(sa.Name).To(Equal("my-js-exporterset-qemu-jumpstarter-dev"))
		Expect(sa.Namespace).To(Equal("ns1"))
	})

	It("should set all required labels", func() {
		sa := r.createExporterSetServiceAccount(js, "qemu-jumpstarter-dev")

		Expect(sa.Labels).To(HaveKeyWithValue("app", "exporterset-controller"))
		Expect(sa.Labels).To(HaveKeyWithValue("component", "exporterset-controller"))
		Expect(sa.Labels).To(HaveKeyWithValue("provisioner", "qemu-jumpstarter-dev"))
		Expect(sa.Labels).To(HaveKeyWithValue("controller", "my-js"))
		Expect(sa.Labels).To(HaveKeyWithValue("app.kubernetes.io/managed-by", "jumpstarter-operator"))
	})
})

var _ = Describe("createExporterSetRole", func() {
	var r *JumpstarterReconciler
	var js *operatorv1alpha1.Jumpstarter

	BeforeEach(func() {
		r = &JumpstarterReconciler{Scheme: k8sClient.Scheme()}
		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: "my-js", Namespace: "ns1"},
		}
	})

	It("should use correct naming convention", func() {
		role := r.createExporterSetRole(js, "qemu-jumpstarter-dev")

		Expect(role.Name).To(Equal("my-js-exporterset-qemu-jumpstarter-dev-role"))
		Expect(role.Namespace).To(Equal("ns1"))
	})

	It("should set all required labels", func() {
		role := r.createExporterSetRole(js, "qemu-jumpstarter-dev")

		Expect(role.Labels).To(HaveKeyWithValue("app", "exporterset-controller"))
		Expect(role.Labels).To(HaveKeyWithValue("component", "exporterset-controller"))
		Expect(role.Labels).To(HaveKeyWithValue("provisioner", "qemu-jumpstarter-dev"))
		Expect(role.Labels).To(HaveKeyWithValue("controller", "my-js"))
		Expect(role.Labels).To(HaveKeyWithValue("app.kubernetes.io/managed-by", "jumpstarter-operator"))
	})

	It("should have non-empty rules matching exporterSetPolicyRules()", func() {
		role := r.createExporterSetRole(js, "qemu-jumpstarter-dev")

		Expect(role.Rules).To(HaveLen(len(exporterSetPolicyRules())))
		Expect(role.Rules).To(Equal(exporterSetPolicyRules()))
	})
})

var _ = Describe("createExporterSetRoleBinding", func() {
	var r *JumpstarterReconciler
	var js *operatorv1alpha1.Jumpstarter

	BeforeEach(func() {
		r = &JumpstarterReconciler{Scheme: k8sClient.Scheme()}
		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: "my-js", Namespace: "ns1"},
		}
	})

	It("should use correct naming convention", func() {
		rb := r.createExporterSetRoleBinding(js, "qemu-jumpstarter-dev")

		Expect(rb.Name).To(Equal("my-js-exporterset-qemu-jumpstarter-dev-rolebinding"))
		Expect(rb.Namespace).To(Equal("ns1"))
	})

	It("should reference the correct Role", func() {
		rb := r.createExporterSetRoleBinding(js, "qemu-jumpstarter-dev")

		Expect(rb.RoleRef.APIGroup).To(Equal("rbac.authorization.k8s.io"))
		Expect(rb.RoleRef.Kind).To(Equal("Role"))
		Expect(rb.RoleRef.Name).To(Equal("my-js-exporterset-qemu-jumpstarter-dev-role"))
	})

	It("should bind to the correct ServiceAccount", func() {
		rb := r.createExporterSetRoleBinding(js, "qemu-jumpstarter-dev")

		Expect(rb.Subjects).To(HaveLen(1))
		Expect(rb.Subjects[0].Kind).To(Equal("ServiceAccount"))
		Expect(rb.Subjects[0].Name).To(Equal("my-js-exporterset-qemu-jumpstarter-dev"))
		Expect(rb.Subjects[0].Namespace).To(Equal("ns1"))
	})

	It("should set all required labels", func() {
		rb := r.createExporterSetRoleBinding(js, "qemu-jumpstarter-dev")

		Expect(rb.Labels).To(HaveKeyWithValue("app", "exporterset-controller"))
		Expect(rb.Labels).To(HaveKeyWithValue("component", "exporterset-controller"))
		Expect(rb.Labels).To(HaveKeyWithValue("provisioner", "qemu-jumpstarter-dev"))
		Expect(rb.Labels).To(HaveKeyWithValue("controller", "my-js"))
		Expect(rb.Labels).To(HaveKeyWithValue("app.kubernetes.io/managed-by", "jumpstarter-operator"))
	})
})

var _ = Describe("createExporterSetDeployment", func() {
	var r *JumpstarterReconciler
	var js *operatorv1alpha1.Jumpstarter

	BeforeEach(func() {
		r = &JumpstarterReconciler{Scheme: k8sClient.Scheme()}
		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: "my-js", Namespace: "ns1"},
			Spec: operatorv1alpha1.JumpstarterSpec{
				ExporterSets: &operatorv1alpha1.ExporterSetsConfig{
					Image:           "quay.io/jumpstarter-dev/exporterset:latest",
					ImagePullPolicy: corev1.PullAlways,
				},
			},
		}
	})

	It("should use correct naming convention", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		Expect(dep.Name).To(Equal("my-js-exporterset-qemu-jumpstarter-dev"))
		Expect(dep.Namespace).To(Equal("ns1"))
	})

	It("should set the --provisioner flag with the original (unsanitized) name", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		container := dep.Spec.Template.Spec.Containers[0]
		Expect(container.Args).To(ContainElement("--provisioner=qemu.jumpstarter.dev"))
	})

	It("should include leader-elect and health/metrics flags", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		container := dep.Spec.Template.Spec.Containers[0]
		Expect(container.Args).To(ContainElements(
			"--leader-elect",
			"--health-probe-bind-address=:8081",
			"--metrics-bind-address=0",
		))
	})

	It("should use the global image when no per-provisioner override is set", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		Expect(dep.Spec.Template.Spec.Containers[0].Image).To(Equal("quay.io/jumpstarter-dev/exporterset:latest"))
	})

	It("should use per-provisioner image override when set", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name:  "qemu.jumpstarter.dev",
			Image: "quay.io/custom/exporterset:v2.0",
		})

		Expect(dep.Spec.Template.Spec.Containers[0].Image).To(Equal("quay.io/custom/exporterset:v2.0"))
	})

	It("should use the global ImagePullPolicy", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		Expect(dep.Spec.Template.Spec.Containers[0].ImagePullPolicy).To(Equal(corev1.PullAlways))
	})

	It("should default ImagePullPolicy to IfNotPresent when not set", func() {
		js.Spec.ExporterSets.ImagePullPolicy = ""

		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		Expect(dep.Spec.Template.Spec.Containers[0].ImagePullPolicy).To(Equal(corev1.PullIfNotPresent))
	})

	It("should default to 1 replica when not specified", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		Expect(*dep.Spec.Replicas).To(Equal(int32(1)))
	})

	It("should use per-provisioner replicas override", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name:     "qemu.jumpstarter.dev",
			Replicas: ptr.To(int32(3)),
		})

		Expect(*dep.Spec.Replicas).To(Equal(int32(3)))
	})

	It("should reference the correct ServiceAccount", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		Expect(dep.Spec.Template.Spec.ServiceAccountName).To(Equal("my-js-exporterset-qemu-jumpstarter-dev"))
	})

	It("should use the /exporter-set-controller command", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		Expect(dep.Spec.Template.Spec.Containers[0].Command).To(Equal([]string{"/exporter-set-controller"}))
	})

	It("should have matching selector and template labels", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		selectorLabels := dep.Spec.Selector.MatchLabels
		templateLabels := dep.Spec.Template.Labels
		Expect(selectorLabels).To(Equal(templateLabels))
		Expect(selectorLabels).To(HaveKeyWithValue("component", "exporterset-controller"))
		Expect(selectorLabels).To(HaveKeyWithValue("provisioner", "qemu-jumpstarter-dev"))
		Expect(selectorLabels).To(HaveKeyWithValue("controller", "my-js"))
	})

	It("should configure liveness and readiness probes on port 8081", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		container := dep.Spec.Template.Spec.Containers[0]
		Expect(container.LivenessProbe).NotTo(BeNil())
		Expect(container.LivenessProbe.HTTPGet.Path).To(Equal("/healthz"))
		Expect(container.LivenessProbe.HTTPGet.Port.IntValue()).To(Equal(8081))

		Expect(container.ReadinessProbe).NotTo(BeNil())
		Expect(container.ReadinessProbe.HTTPGet.Path).To(Equal("/readyz"))
		Expect(container.ReadinessProbe.HTTPGet.Port.IntValue()).To(Equal(8081))
	})

	It("should enforce security best practices", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		podSec := dep.Spec.Template.Spec.SecurityContext
		Expect(podSec).NotTo(BeNil())
		Expect(*podSec.RunAsNonRoot).To(BeTrue())
		Expect(podSec.SeccompProfile.Type).To(Equal(corev1.SeccompProfileTypeRuntimeDefault))

		containerSec := dep.Spec.Template.Spec.Containers[0].SecurityContext
		Expect(containerSec).NotTo(BeNil())
		Expect(*containerSec.AllowPrivilegeEscalation).To(BeFalse())
		Expect(containerSec.Capabilities.Drop).To(ContainElement(corev1.Capability("ALL")))
	})

	It("should apply default resources when none are specified", func() {
		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		res := dep.Spec.Template.Spec.Containers[0].Resources
		Expect(res.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("100m")))
		Expect(res.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("256Mi")))
		Expect(res.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("500m")))
		Expect(res.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("512Mi")))
	})

	It("should use custom global resources when provided", func() {
		js.Spec.ExporterSets.Resources = corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("1"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("2"),
			},
		}

		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name: "qemu.jumpstarter.dev",
		})

		res := dep.Spec.Template.Spec.Containers[0].Resources
		Expect(res.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("1")))
		Expect(res.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("2")))
	})

	It("should use per-provisioner resources override over global", func() {
		js.Spec.ExporterSets.Resources = corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("1"),
			},
		}

		provResources := &corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("4"),
				corev1.ResourceMemory: resource.MustParse("4Gi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("8"),
				corev1.ResourceMemory: resource.MustParse("8Gi"),
			},
		}

		dep := r.createExporterSetDeployment(js, operatorv1alpha1.ProvisionerConfig{
			Name:      "qemu.jumpstarter.dev",
			Resources: provResources,
		})

		res := dep.Spec.Template.Spec.Containers[0].Resources
		Expect(res.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("4")))
		Expect(res.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("4Gi")))
		Expect(res.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("8")))
		Expect(res.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("8Gi")))
	})
})

func containsString(slice []string, s string) bool {
	for _, v := range slice {
		if v == s {
			return true
		}
	}
	return false
}
