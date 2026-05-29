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
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("RBAC factory functions", func() {
	var (
		r  *JumpstarterReconciler
		js *operatorv1alpha1.Jumpstarter
	)

	BeforeEach(func() {
		r = &JumpstarterReconciler{}
		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "test",
				Namespace: "default",
			},
		}
	})

	Describe("createRouterServiceAccount", func() {
		It("should use the correct name format", func() {
			sa := r.createRouterServiceAccount(js)
			Expect(sa.Name).To(Equal("test-router-sa"))
			Expect(sa.Namespace).To(Equal("default"))
		})

		It("should set router labels", func() {
			sa := r.createRouterServiceAccount(js)
			Expect(sa.Labels).To(HaveKeyWithValue("app", "jumpstarter-router"))
			Expect(sa.Labels).To(HaveKeyWithValue("app.kubernetes.io/name", "jumpstarter-router"))
			Expect(sa.Labels).To(HaveKeyWithValue("app.kubernetes.io/managed-by", "jumpstarter-operator"))
		})
	})

	Describe("createRouterRole", func() {
		It("should use the correct name format", func() {
			role := r.createRouterRole(js)
			Expect(role.Name).To(Equal("test-router-role"))
			Expect(role.Namespace).To(Equal("default"))
		})

		It("should set router labels", func() {
			role := r.createRouterRole(js)
			Expect(role.Labels).To(HaveKeyWithValue("app", "jumpstarter-router"))
			Expect(role.Labels).To(HaveKeyWithValue("app.kubernetes.io/name", "jumpstarter-router"))
			Expect(role.Labels).To(HaveKeyWithValue("app.kubernetes.io/managed-by", "jumpstarter-operator"))
		})

		It("should grant read-only access to configmaps only", func() {
			role := r.createRouterRole(js)
			Expect(role.Rules).To(HaveLen(1))
			Expect(role.Rules[0].APIGroups).To(Equal([]string{""}))
			Expect(role.Rules[0].Resources).To(Equal([]string{"configmaps"}))
			Expect(role.Rules[0].Verbs).To(ConsistOf("get", "list", "watch"))
		})

		It("should not grant access to secrets", func() {
			role := r.createRouterRole(js)
			for _, rule := range role.Rules {
				Expect(rule.Resources).NotTo(ContainElement("secrets"))
			}
		})
	})

	Describe("createRouterRoleBinding", func() {
		It("should use the correct name format", func() {
			rb := r.createRouterRoleBinding(js)
			Expect(rb.Name).To(Equal("test-router-rolebinding"))
			Expect(rb.Namespace).To(Equal("default"))
		})

		It("should set router labels", func() {
			rb := r.createRouterRoleBinding(js)
			Expect(rb.Labels).To(HaveKeyWithValue("app", "jumpstarter-router"))
			Expect(rb.Labels).To(HaveKeyWithValue("app.kubernetes.io/name", "jumpstarter-router"))
			Expect(rb.Labels).To(HaveKeyWithValue("app.kubernetes.io/managed-by", "jumpstarter-operator"))
		})

		It("should reference the router role", func() {
			rb := r.createRouterRoleBinding(js)
			Expect(rb.RoleRef.APIGroup).To(Equal("rbac.authorization.k8s.io"))
			Expect(rb.RoleRef.Kind).To(Equal("Role"))
			Expect(rb.RoleRef.Name).To(Equal("test-router-role"))
		})

		It("should bind to the router service account", func() {
			rb := r.createRouterRoleBinding(js)
			Expect(rb.Subjects).To(HaveLen(1))
			Expect(rb.Subjects[0].Kind).To(Equal("ServiceAccount"))
			Expect(rb.Subjects[0].Name).To(Equal("test-router-sa"))
			Expect(rb.Subjects[0].Namespace).To(Equal("default"))
		})
	})
})

var _ = Describe("reconcileRBAC integration", func() {
	var (
		r   *JumpstarterReconciler
		js  *operatorv1alpha1.Jumpstarter
		ctx context.Context
	)

	BeforeEach(func() {
		r = &JumpstarterReconciler{
			Client: k8sClient,
			Scheme: k8sClient.Scheme(),
		}
		ctx = context.Background()

		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "rbac-integ",
				Namespace: "default",
			},
			Spec: operatorv1alpha1.JumpstarterSpec{
				BaseDomain: "example.com",
				Controller: operatorv1alpha1.ControllerConfig{
					Image:    "quay.io/jumpstarter/jumpstarter:latest",
					Replicas: 1,
				},
				Routers: operatorv1alpha1.RoutersConfig{
					Image:    "quay.io/jumpstarter/jumpstarter:latest",
					Replicas: 1,
				},
			},
		}
		err := k8sClient.Get(ctx, types.NamespacedName{Name: js.Name, Namespace: js.Namespace}, &operatorv1alpha1.Jumpstarter{})
		if errors.IsNotFound(err) {
			Expect(k8sClient.Create(ctx, js)).To(Succeed())
		}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: js.Name, Namespace: js.Namespace}, js)).To(Succeed())
	})

	AfterEach(func() {
		// Clean up all RBAC resources
		for _, name := range []string{"rbac-integ-controller-rolebinding", "rbac-integ-router-rolebinding"} {
			rb := &rbacv1.RoleBinding{}
			if err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: "default"}, rb); err == nil {
				_ = k8sClient.Delete(ctx, rb)
			}
		}
		for _, name := range []string{"rbac-integ-controller-role", "rbac-integ-router-role"} {
			role := &rbacv1.Role{}
			if err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: "default"}, role); err == nil {
				_ = k8sClient.Delete(ctx, role)
			}
		}
		for _, name := range []string{"rbac-integ-controller-manager", "rbac-integ-router-sa"} {
			sa := &corev1.ServiceAccount{}
			if err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: "default"}, sa); err == nil {
				_ = k8sClient.Delete(ctx, sa)
			}
		}
		resource := &operatorv1alpha1.Jumpstarter{}
		if err := k8sClient.Get(ctx, types.NamespacedName{Name: js.Name, Namespace: js.Namespace}, resource); err == nil {
			_ = k8sClient.Delete(ctx, resource)
		}
	})

	It("should create all six RBAC resources with correct names, permissions, and bindings", func() {
		err := r.reconcileRBAC(ctx, js)
		Expect(err).NotTo(HaveOccurred())

		// 1. Controller ServiceAccount
		controllerSA := &corev1.ServiceAccount{}
		err = k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-manager", Namespace: "default"}, controllerSA)
		Expect(err).NotTo(HaveOccurred())
		Expect(controllerSA.Labels).To(HaveKeyWithValue("app", "jumpstarter-controller"))

		// 2. Router ServiceAccount
		routerSA := &corev1.ServiceAccount{}
		err = k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-sa", Namespace: "default"}, routerSA)
		Expect(err).NotTo(HaveOccurred())
		Expect(routerSA.Labels).To(HaveKeyWithValue("app", "jumpstarter-router"))

		// 3. Controller Role
		controllerRole := &rbacv1.Role{}
		err = k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-role", Namespace: "default"}, controllerRole)
		Expect(err).NotTo(HaveOccurred())
		Expect(controllerRole.Labels).To(HaveKeyWithValue("app", "jumpstarter-controller"))
		// Controller role should have access to secrets
		hasSecrets := false
		for _, rule := range controllerRole.Rules {
			for _, res := range rule.Resources {
				if res == "secrets" {
					hasSecrets = true
				}
			}
		}
		Expect(hasSecrets).To(BeTrue(), "controller role should have access to secrets")

		// 4. Router Role
		routerRole := &rbacv1.Role{}
		err = k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-role", Namespace: "default"}, routerRole)
		Expect(err).NotTo(HaveOccurred())
		Expect(routerRole.Labels).To(HaveKeyWithValue("app", "jumpstarter-router"))
		// Router role should only have read-only access to configmaps
		Expect(routerRole.Rules).To(HaveLen(1))
		Expect(routerRole.Rules[0].Resources).To(Equal([]string{"configmaps"}))
		Expect(routerRole.Rules[0].Verbs).To(ConsistOf("get", "list", "watch"))
		// Router role should NOT have access to secrets
		for _, rule := range routerRole.Rules {
			Expect(rule.Resources).NotTo(ContainElement("secrets"))
		}

		// 5. Controller RoleBinding
		controllerRB := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-rolebinding", Namespace: "default"}, controllerRB)
		Expect(err).NotTo(HaveOccurred())
		Expect(controllerRB.RoleRef.Name).To(Equal("rbac-integ-controller-role"))
		Expect(controllerRB.RoleRef.Kind).To(Equal("Role"))
		Expect(controllerRB.Subjects).To(HaveLen(1))
		Expect(controllerRB.Subjects[0].Name).To(Equal("rbac-integ-controller-manager"))
		Expect(controllerRB.Subjects[0].Kind).To(Equal("ServiceAccount"))

		// 6. Router RoleBinding
		routerRB := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-rolebinding", Namespace: "default"}, routerRB)
		Expect(err).NotTo(HaveOccurred())
		Expect(routerRB.RoleRef.Name).To(Equal("rbac-integ-router-role"))
		Expect(routerRB.RoleRef.Kind).To(Equal("Role"))
		Expect(routerRB.Subjects).To(HaveLen(1))
		Expect(routerRB.Subjects[0].Name).To(Equal("rbac-integ-router-sa"))
		Expect(routerRB.Subjects[0].Kind).To(Equal("ServiceAccount"))
	})

	It("should be idempotent on a second reconciliation", func() {
		// First reconciliation
		err := r.reconcileRBAC(ctx, js)
		Expect(err).NotTo(HaveOccurred())

		// Capture ResourceVersions
		controllerSA := &corev1.ServiceAccount{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-manager", Namespace: "default"}, controllerSA)).To(Succeed())
		routerSA := &corev1.ServiceAccount{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-sa", Namespace: "default"}, routerSA)).To(Succeed())
		controllerRole := &rbacv1.Role{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-role", Namespace: "default"}, controllerRole)).To(Succeed())
		routerRole := &rbacv1.Role{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-role", Namespace: "default"}, routerRole)).To(Succeed())
		controllerRB := &rbacv1.RoleBinding{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-rolebinding", Namespace: "default"}, controllerRB)).To(Succeed())
		routerRB := &rbacv1.RoleBinding{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-rolebinding", Namespace: "default"}, routerRB)).To(Succeed())

		// Second reconciliation
		err = r.reconcileRBAC(ctx, js)
		Expect(err).NotTo(HaveOccurred())

		// Verify no resources changed (no unnecessary API writes)
		controllerSAAfter := &corev1.ServiceAccount{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-manager", Namespace: "default"}, controllerSAAfter)).To(Succeed())
		Expect(controllerSAAfter.ResourceVersion).To(Equal(controllerSA.ResourceVersion), "controller SA ResourceVersion should be unchanged")

		routerSAAfter := &corev1.ServiceAccount{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-sa", Namespace: "default"}, routerSAAfter)).To(Succeed())
		Expect(routerSAAfter.ResourceVersion).To(Equal(routerSA.ResourceVersion), "router SA ResourceVersion should be unchanged")

		controllerRoleAfter := &rbacv1.Role{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-role", Namespace: "default"}, controllerRoleAfter)).To(Succeed())
		Expect(controllerRoleAfter.ResourceVersion).To(Equal(controllerRole.ResourceVersion), "controller Role ResourceVersion should be unchanged")

		routerRoleAfter := &rbacv1.Role{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-role", Namespace: "default"}, routerRoleAfter)).To(Succeed())
		Expect(routerRoleAfter.ResourceVersion).To(Equal(routerRole.ResourceVersion), "router Role ResourceVersion should be unchanged")

		controllerRBAfter := &rbacv1.RoleBinding{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-controller-rolebinding", Namespace: "default"}, controllerRBAfter)).To(Succeed())
		Expect(controllerRBAfter.ResourceVersion).To(Equal(controllerRB.ResourceVersion), "controller RoleBinding ResourceVersion should be unchanged")

		routerRBAfter := &rbacv1.RoleBinding{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-integ-router-rolebinding", Namespace: "default"}, routerRBAfter)).To(Succeed())
		Expect(routerRBAfter.ResourceVersion).To(Equal(routerRB.ResourceVersion), "router RoleBinding ResourceVersion should be unchanged")
	})
})

var _ = Describe("reconcileRoleBinding", func() {
	var (
		r   *JumpstarterReconciler
		js  *operatorv1alpha1.Jumpstarter
		ctx context.Context
	)

	BeforeEach(func() {
		r = &JumpstarterReconciler{
			Client: k8sClient,
			Scheme: k8sClient.Scheme(),
		}
		ctx = context.Background()

		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "rbac-test",
				Namespace: "default",
			},
		}
		// Create the Jumpstarter CR so that SetControllerReference works
		err := k8sClient.Get(ctx, types.NamespacedName{Name: js.Name, Namespace: js.Namespace}, &operatorv1alpha1.Jumpstarter{})
		if errors.IsNotFound(err) {
			js.Spec = operatorv1alpha1.JumpstarterSpec{
				BaseDomain: "example.com",
				Controller: operatorv1alpha1.ControllerConfig{
					Image:    "quay.io/jumpstarter/jumpstarter:latest",
					Replicas: 1,
				},
				Routers: operatorv1alpha1.RoutersConfig{
					Image:    "quay.io/jumpstarter/jumpstarter:latest",
					Replicas: 1,
				},
			}
			Expect(k8sClient.Create(ctx, js)).To(Succeed())
		}
		// Re-fetch to get the server-assigned UID
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: js.Name, Namespace: js.Namespace}, js)).To(Succeed())
	})

	AfterEach(func() {
		// Clean up the RoleBinding if it exists
		rb := &rbacv1.RoleBinding{}
		err := k8sClient.Get(ctx, types.NamespacedName{Name: "rbac-test-router-rolebinding", Namespace: "default"}, rb)
		if err == nil {
			Expect(k8sClient.Delete(ctx, rb)).To(Succeed())
		}
		// Clean up the Jumpstarter CR
		resource := &operatorv1alpha1.Jumpstarter{}
		err = k8sClient.Get(ctx, types.NamespacedName{Name: js.Name, Namespace: js.Namespace}, resource)
		if err == nil {
			Expect(k8sClient.Delete(ctx, resource)).To(Succeed())
		}
	})

	It("should create a RoleBinding when it does not exist", func() {
		desired := r.createRouterRoleBinding(js)
		err := r.reconcileRoleBinding(ctx, js, desired)
		Expect(err).NotTo(HaveOccurred())

		// Verify it was created
		created := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name:      "rbac-test-router-rolebinding",
			Namespace: "default",
		}, created)
		Expect(err).NotTo(HaveOccurred())
		Expect(created.RoleRef.Name).To(Equal("rbac-test-router-role"))
		Expect(created.Subjects).To(HaveLen(1))
		Expect(created.Subjects[0].Name).To(Equal("rbac-test-router-sa"))
	})

	It("should be a no-op when the RoleBinding already matches", func() {
		// Create it first
		desired := r.createRouterRoleBinding(js)
		err := r.reconcileRoleBinding(ctx, js, desired)
		Expect(err).NotTo(HaveOccurred())

		// Capture ResourceVersion before the no-op reconciliation
		before := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name:      "rbac-test-router-rolebinding",
			Namespace: "default",
		}, before)
		Expect(err).NotTo(HaveOccurred())
		rvBefore := before.ResourceVersion

		// Reconcile again with same desired state -- should be a no-op
		desired2 := r.createRouterRoleBinding(js)
		err = r.reconcileRoleBinding(ctx, js, desired2)
		Expect(err).NotTo(HaveOccurred())

		// Verify it still exists, is unchanged, and no unnecessary API write occurred
		existing := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name:      "rbac-test-router-rolebinding",
			Namespace: "default",
		}, existing)
		Expect(err).NotTo(HaveOccurred())
		Expect(existing.RoleRef.Name).To(Equal("rbac-test-router-role"))
		Expect(existing.ResourceVersion).To(Equal(rvBefore), "ResourceVersion should be unchanged when no update is needed")
	})

	It("should update Subjects when they change but RoleRef is unchanged", func() {
		// Create it first
		desired := r.createRouterRoleBinding(js)
		err := r.reconcileRoleBinding(ctx, js, desired)
		Expect(err).NotTo(HaveOccurred())

		// Now reconcile with updated Subjects but same RoleRef
		desired2 := r.createRouterRoleBinding(js)
		desired2.Subjects = []rbacv1.Subject{
			{
				Kind:      "ServiceAccount",
				Name:      "updated-sa",
				Namespace: "default",
			},
		}
		err = r.reconcileRoleBinding(ctx, js, desired2)
		Expect(err).NotTo(HaveOccurred())

		// Verify the Subjects were updated
		existing := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name:      "rbac-test-router-rolebinding",
			Namespace: "default",
		}, existing)
		Expect(err).NotTo(HaveOccurred())
		Expect(existing.Subjects).To(HaveLen(1))
		Expect(existing.Subjects[0].Name).To(Equal("updated-sa"))
		Expect(existing.RoleRef.Name).To(Equal("rbac-test-router-role"))
	})

	It("should delete and recreate when RoleRef changes", func() {
		// Create it first
		desired := r.createRouterRoleBinding(js)
		err := r.reconcileRoleBinding(ctx, js, desired)
		Expect(err).NotTo(HaveOccurred())

		// Get the original UID to verify it was recreated
		original := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name:      "rbac-test-router-rolebinding",
			Namespace: "default",
		}, original)
		Expect(err).NotTo(HaveOccurred())
		originalUID := original.UID

		// Reconcile with a different RoleRef
		desired2 := r.createRouterRoleBinding(js)
		desired2.RoleRef = rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "Role",
			Name:     "different-role",
		}
		err = r.reconcileRoleBinding(ctx, js, desired2)
		Expect(err).NotTo(HaveOccurred())

		// Verify it was recreated with the new RoleRef and a new UID
		recreated := &rbacv1.RoleBinding{}
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name:      "rbac-test-router-rolebinding",
			Namespace: "default",
		}, recreated)
		Expect(err).NotTo(HaveOccurred())
		Expect(recreated.RoleRef.Name).To(Equal("different-role"))
		Expect(recreated.UID).NotTo(Equal(originalUID))
	})
})
