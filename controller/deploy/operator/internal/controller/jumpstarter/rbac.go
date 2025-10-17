package jumpstarter

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

// reconcileRBAC reconciles all RBAC resources (ServiceAccount, Role, RoleBinding)
func (r *JumpstarterReconciler) reconcileRBAC(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	// Create ServiceAccount
	sa := r.createServiceAccount(jumpstarter)
	if err := controllerutil.SetControllerReference(jumpstarter, sa, r.Scheme); err != nil {
		return err
	}
	if _, err := controllerutil.CreateOrUpdate(ctx, r.Client, sa, func() error {
		return nil
	}); err != nil {
		return err
	}

	// Create Role
	role := r.createRole(jumpstarter)
	if err := controllerutil.SetControllerReference(jumpstarter, role, r.Scheme); err != nil {
		return err
	}
	if _, err := controllerutil.CreateOrUpdate(ctx, r.Client, role, func() error {
		return nil
	}); err != nil {
		return err
	}

	// Create RoleBinding
	roleBinding := r.createRoleBinding(jumpstarter)
	if err := controllerutil.SetControllerReference(jumpstarter, roleBinding, r.Scheme); err != nil {
		return err
	}
	if _, err := controllerutil.CreateOrUpdate(ctx, r.Client, roleBinding, func() error {
		return nil
	}); err != nil {
		return err
	}

	return nil
}

// createServiceAccount creates a service account for the controller
func (r *JumpstarterReconciler) createServiceAccount(jumpstarter *operatorv1alpha1.Jumpstarter) *corev1.ServiceAccount {
	return &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-controller-manager", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "jumpstarter-controller",
				"app.kubernetes.io/name":       "jumpstarter-controller",
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
	}
}

// createRole creates a role with necessary permissions for the controller
func (r *JumpstarterReconciler) createRole(jumpstarter *operatorv1alpha1.Jumpstarter) *rbacv1.Role {
	return &rbacv1.Role{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-controller-role", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "jumpstarter-controller",
				"app.kubernetes.io/name":       "jumpstarter-controller",
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		Rules: []rbacv1.PolicyRule{
			{
				APIGroups: []string{""},
				Resources: []string{"configmaps"},
				Verbs:     []string{"get", "list", "watch"},
			},
			{
				APIGroups: []string{""},
				Resources: []string{"secrets"},
				Verbs:     []string{"get", "list", "watch", "create", "update", "patch", "delete"},
			},
			{
				APIGroups: []string{"jumpstarter.dev"},
				Resources: []string{"clients", "exporters", "leases", "exporteraccesspolicies"},
				Verbs:     []string{"get", "list", "watch", "create", "update", "patch", "delete"},
			},
			{
				APIGroups: []string{"jumpstarter.dev"},
				Resources: []string{"clients/status", "exporters/status", "leases/status"},
				Verbs:     []string{"get", "update", "patch"},
			},
			{
				APIGroups: []string{"jumpstarter.dev"},
				Resources: []string{"clients/finalizers", "exporters/finalizers", "leases/finalizers"},
				Verbs:     []string{"update"},
			},
			{
				APIGroups: []string{""},
				Resources: []string{"events"},
				Verbs:     []string{"create", "patch"},
			},
			{
				APIGroups: []string{"coordination.k8s.io"},
				Resources: []string{"leases"},
				Verbs:     []string{"get", "list", "watch", "create", "update", "patch", "delete"},
			},
		},
	}
}

// createRoleBinding creates a role binding for the controller
func (r *JumpstarterReconciler) createRoleBinding(jumpstarter *operatorv1alpha1.Jumpstarter) *rbacv1.RoleBinding {
	return &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-controller-rolebinding", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "jumpstarter-controller",
				"app.kubernetes.io/name":       "jumpstarter-controller",
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "Role",
			Name:     fmt.Sprintf("%s-controller-role", jumpstarter.Name),
		},
		Subjects: []rbacv1.Subject{
			{
				Kind:      "ServiceAccount",
				Name:      fmt.Sprintf("%s-controller-manager", jumpstarter.Name),
				Namespace: jumpstarter.Namespace,
			},
		},
	}
}
