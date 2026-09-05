package jumpstarter

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/equality"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

// reconcileRBAC reconciles all RBAC resources (ServiceAccount, Role, RoleBinding)
func (r *JumpstarterReconciler) reconcileRBAC(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	// Controller ServiceAccount
	// Note: We intentionally do NOT set controller reference on ServiceAccounts to prevent
	// them from being garbage collected when the Jumpstarter CR is deleted
	if err := r.reconcileServiceAccount(ctx, r.createServiceAccount(jumpstarter)); err != nil {
		return err
	}

	// Router ServiceAccount
	if err := r.reconcileServiceAccount(ctx, r.createRouterServiceAccount(jumpstarter)); err != nil {
		return err
	}

	// Controller Role
	if err := r.reconcileRole(ctx, jumpstarter, r.createRole(jumpstarter)); err != nil {
		return err
	}

	// Controller RoleBinding
	// Note: RoleRef is immutable in Kubernetes. If it changes, we must delete and recreate.
	if err := r.reconcileRoleBinding(ctx, jumpstarter, r.createRoleBinding(jumpstarter)); err != nil {
		return err
	}

	// Router Role (minimal permissions: read configmaps)
	if err := r.reconcileRole(ctx, jumpstarter, r.createRouterRole(jumpstarter)); err != nil {
		return err
	}

	// Router RoleBinding
	if err := r.reconcileRoleBinding(ctx, jumpstarter, r.createRouterRoleBinding(jumpstarter)); err != nil {
		return err
	}

	return nil
}

// reconcileServiceAccount reconciles a ServiceAccount using CreateOrUpdate.
// Note: controller reference is intentionally NOT set on ServiceAccounts to prevent
// them from being garbage collected when the Jumpstarter CR is deleted.
func (r *JumpstarterReconciler) reconcileServiceAccount(
	ctx context.Context,
	desired *corev1.ServiceAccount,
) error {
	log := logf.FromContext(ctx)

	existing := &corev1.ServiceAccount{}
	existing.Name = desired.Name
	existing.Namespace = desired.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existing, func() error {
		if existing.CreationTimestamp.IsZero() {
			existing.Labels = desired.Labels
			existing.Annotations = desired.Annotations
			return nil
		}

		if !serviceAccountNeedsUpdate(existing, desired) {
			log.V(1).Info("ServiceAccount is up to date, skipping update",
				"name", existing.Name,
				"namespace", existing.Namespace)
			return nil
		}

		existing.Labels = desired.Labels
		existing.Annotations = desired.Annotations
		return nil
	})

	if err != nil {
		log.Error(err, "Failed to reconcile ServiceAccount",
			"name", desired.Name,
			"namespace", desired.Namespace)
		return err
	}

	log.Info("ServiceAccount reconciled",
		"name", existing.Name,
		"namespace", existing.Namespace,
		"operation", op)
	return nil
}

// reconcileRole reconciles a Role using CreateOrUpdate and sets the controller reference.
func (r *JumpstarterReconciler) reconcileRole(
	ctx context.Context,
	jumpstarter *operatorv1alpha1.Jumpstarter,
	desired *rbacv1.Role,
) error {
	log := logf.FromContext(ctx)

	existing := &rbacv1.Role{}
	existing.Name = desired.Name
	existing.Namespace = desired.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existing, func() error {
		if existing.CreationTimestamp.IsZero() {
			existing.Labels = desired.Labels
			existing.Annotations = desired.Annotations
			existing.Rules = desired.Rules
			return controllerutil.SetControllerReference(jumpstarter, existing, r.Scheme)
		}

		if !roleNeedsUpdate(existing, desired) {
			log.V(1).Info("Role is up to date, skipping update",
				"name", existing.Name,
				"namespace", existing.Namespace)
			return nil
		}

		existing.Labels = desired.Labels
		existing.Annotations = desired.Annotations
		existing.Rules = desired.Rules
		return controllerutil.SetControllerReference(jumpstarter, existing, r.Scheme)
	})

	if err != nil {
		log.Error(err, "Failed to reconcile Role",
			"name", desired.Name,
			"namespace", desired.Namespace)
		return err
	}

	log.Info("Role reconciled",
		"name", existing.Name,
		"namespace", existing.Namespace,
		"operation", op)
	return nil
}

// reconcileRoleBinding reconciles a RoleBinding, handling the immutable RoleRef field.
// Kubernetes does not allow updating RoleRef on an existing RoleBinding. If the desired
// RoleRef differs from the existing one, this function deletes the old RoleBinding and
// creates a new one. For all other fields, it uses a standard get-and-update pattern.
func (r *JumpstarterReconciler) reconcileRoleBinding(
	ctx context.Context,
	jumpstarter *operatorv1alpha1.Jumpstarter,
	desired *rbacv1.RoleBinding,
) error {
	log := logf.FromContext(ctx)

	existing := &rbacv1.RoleBinding{}
	key := client.ObjectKeyFromObject(desired)
	err := r.Client.Get(ctx, key, existing)

	if apierrors.IsNotFound(err) {
		// RoleBinding does not exist, create it
		if err := controllerutil.SetControllerReference(jumpstarter, desired, r.Scheme); err != nil {
			return err
		}
		if err := r.Client.Create(ctx, desired); err != nil {
			log.Error(err, "Failed to create RoleBinding",
				"name", desired.Name,
				"namespace", desired.Namespace)
			return err
		}
		log.Info("RoleBinding reconciled",
			"name", desired.Name,
			"namespace", desired.Namespace,
			"operation", "created")
		return nil
	}

	if err != nil {
		log.Error(err, "Failed to get RoleBinding",
			"name", desired.Name,
			"namespace", desired.Namespace)
		return err
	}

	// RoleRef is immutable -- if it differs we must delete and recreate
	if !equality.Semantic.DeepEqual(existing.RoleRef, desired.RoleRef) {
		log.Info("RoleBinding RoleRef changed, deleting and recreating",
			"name", existing.Name,
			"namespace", existing.Namespace)
		if err := r.Client.Delete(ctx, existing); err != nil {
			log.Error(err, "Failed to delete RoleBinding for recreation",
				"name", existing.Name,
				"namespace", existing.Namespace)
			return err
		}
		if err := controllerutil.SetControllerReference(jumpstarter, desired, r.Scheme); err != nil {
			log.Error(err, "Failed to set controller reference after RoleBinding deletion; RoleBinding is absent until next reconciliation",
				"name", desired.Name,
				"namespace", desired.Namespace)
			return err
		}
		if err := r.Client.Create(ctx, desired); err != nil {
			log.Error(err, "Failed to recreate RoleBinding",
				"name", desired.Name,
				"namespace", desired.Namespace)
			return err
		}
		log.Info("RoleBinding reconciled",
			"name", desired.Name,
			"namespace", desired.Namespace,
			"operation", "recreated")
		return nil
	}

	// RoleRef unchanged -- update other fields if needed
	if !roleBindingNeedsUpdate(existing, desired) {
		log.V(1).Info("RoleBinding is up to date, skipping update",
			"name", existing.Name,
			"namespace", existing.Namespace)
		return nil
	}

	existing.Labels = desired.Labels
	existing.Annotations = desired.Annotations
	existing.Subjects = desired.Subjects
	if err := controllerutil.SetControllerReference(jumpstarter, existing, r.Scheme); err != nil {
		return err
	}
	if err := r.Client.Update(ctx, existing); err != nil {
		log.Error(err, "Failed to update RoleBinding",
			"name", existing.Name,
			"namespace", existing.Namespace)
		return err
	}

	log.Info("RoleBinding reconciled",
		"name", existing.Name,
		"namespace", existing.Namespace,
		"operation", "updated")
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

// createRouterServiceAccount creates a dedicated service account for router workloads
func (r *JumpstarterReconciler) createRouterServiceAccount(jumpstarter *operatorv1alpha1.Jumpstarter) *corev1.ServiceAccount {
	return &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-router-sa", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "jumpstarter-router",
				"app.kubernetes.io/name":       "jumpstarter-router",
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
				Resources: []string{"clients/status", "exporters/status", "leases/status", "exporteraccesspolicies/status"},
				Verbs:     []string{"get", "update", "patch"},
			},
			{
				APIGroups: []string{"jumpstarter.dev"},
				Resources: []string{"clients/finalizers", "exporters/finalizers", "leases/finalizers", "exporteraccesspolicies/finalizers"},
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

// createRouterRole creates a role with minimal permissions for the router (read configmaps)
func (r *JumpstarterReconciler) createRouterRole(jumpstarter *operatorv1alpha1.Jumpstarter) *rbacv1.Role {
	return &rbacv1.Role{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-router-role", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "jumpstarter-router",
				"app.kubernetes.io/name":       "jumpstarter-router",
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		Rules: []rbacv1.PolicyRule{
			{
				APIGroups: []string{""},
				Resources: []string{"configmaps"},
				Verbs:     []string{"get", "list", "watch"},
			},
		},
	}
}

// createRouterRoleBinding creates a role binding for the router service account
func (r *JumpstarterReconciler) createRouterRoleBinding(jumpstarter *operatorv1alpha1.Jumpstarter) *rbacv1.RoleBinding {
	return &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-router-rolebinding", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "jumpstarter-router",
				"app.kubernetes.io/name":       "jumpstarter-router",
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "Role",
			Name:     fmt.Sprintf("%s-router-role", jumpstarter.Name),
		},
		Subjects: []rbacv1.Subject{
			{
				Kind:      "ServiceAccount",
				Name:      fmt.Sprintf("%s-router-sa", jumpstarter.Name),
				Namespace: jumpstarter.Namespace,
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
