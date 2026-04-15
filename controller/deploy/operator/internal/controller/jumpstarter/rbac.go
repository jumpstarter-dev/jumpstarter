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

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

// reconcileRBAC reconciles all RBAC resources (ServiceAccount, Role, RoleBinding)
func (r *JumpstarterReconciler) reconcileRBAC(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	// ServiceAccount
	// Note: We intentionally do NOT set controller reference on ServiceAccount to prevent
	// it from being garbage collected when the Jumpstarter CR is deleted
	desiredSA := r.createServiceAccount(jumpstarter)

	existingSA := &corev1.ServiceAccount{}
	existingSA.Name = desiredSA.Name
	existingSA.Namespace = desiredSA.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existingSA, func() error {
		// Check if this is a new service account or an existing one
		if existingSA.CreationTimestamp.IsZero() {
			// ServiceAccount is being created, copy all fields from desired
			existingSA.Labels = desiredSA.Labels
			existingSA.Annotations = desiredSA.Annotations
			return nil
		}

		// ServiceAccount exists, check if update is needed
		if !serviceAccountNeedsUpdate(existingSA, desiredSA) {
			log.V(1).Info("ServiceAccount is up to date, skipping update",
				"name", existingSA.Name,
				"namespace", existingSA.Namespace)
			return nil
		}

		// Update needed - apply changes
		existingSA.Labels = desiredSA.Labels
		existingSA.Annotations = desiredSA.Annotations
		return nil
	})

	if err != nil {
		log.Error(err, "Failed to reconcile ServiceAccount",
			"name", desiredSA.Name,
			"namespace", desiredSA.Namespace)
		return err
	}

	log.Info("ServiceAccount reconciled",
		"name", existingSA.Name,
		"namespace", existingSA.Namespace,
		"operation", op)

	// Router ServiceAccount (uses dedicated minimal Role)
	// Note: We intentionally do NOT set controller reference on ServiceAccount to prevent
	// it from being garbage collected when the Jumpstarter CR is deleted
	desiredRouterSA := r.createRouterServiceAccount(jumpstarter)

	existingRouterSA := &corev1.ServiceAccount{}
	existingRouterSA.Name = desiredRouterSA.Name
	existingRouterSA.Namespace = desiredRouterSA.Namespace

	op, err = controllerutil.CreateOrUpdate(ctx, r.Client, existingRouterSA, func() error {
		if existingRouterSA.CreationTimestamp.IsZero() {
			existingRouterSA.Labels = desiredRouterSA.Labels
			existingRouterSA.Annotations = desiredRouterSA.Annotations
			return nil
		}

		if !serviceAccountNeedsUpdate(existingRouterSA, desiredRouterSA) {
			log.V(1).Info("Router ServiceAccount is up to date, skipping update",
				"name", existingRouterSA.Name,
				"namespace", existingRouterSA.Namespace)
			return nil
		}

		existingRouterSA.Labels = desiredRouterSA.Labels
		existingRouterSA.Annotations = desiredRouterSA.Annotations
		return nil
	})

	if err != nil {
		log.Error(err, "Failed to reconcile Router ServiceAccount",
			"name", desiredRouterSA.Name,
			"namespace", desiredRouterSA.Namespace)
		return err
	}

	log.Info("Router ServiceAccount reconciled",
		"name", existingRouterSA.Name,
		"namespace", existingRouterSA.Namespace,
		"operation", op)

	// Role
	desiredRole := r.createRole(jumpstarter)

	existingRole := &rbacv1.Role{}
	existingRole.Name = desiredRole.Name
	existingRole.Namespace = desiredRole.Namespace

	op, err = controllerutil.CreateOrUpdate(ctx, r.Client, existingRole, func() error {
		// Check if this is a new role or an existing one
		if existingRole.CreationTimestamp.IsZero() {
			// Role is being created, copy all fields from desired
			existingRole.Labels = desiredRole.Labels
			existingRole.Annotations = desiredRole.Annotations
			existingRole.Rules = desiredRole.Rules
			return controllerutil.SetControllerReference(jumpstarter, existingRole, r.Scheme)
		}

		// Role exists, check if update is needed
		if !roleNeedsUpdate(existingRole, desiredRole) {
			log.V(1).Info("Role is up to date, skipping update",
				"name", existingRole.Name,
				"namespace", existingRole.Namespace)
			return nil
		}

		// Update needed - apply changes
		existingRole.Labels = desiredRole.Labels
		existingRole.Annotations = desiredRole.Annotations
		existingRole.Rules = desiredRole.Rules
		return controllerutil.SetControllerReference(jumpstarter, existingRole, r.Scheme)
	})

	if err != nil {
		log.Error(err, "Failed to reconcile Role",
			"name", desiredRole.Name,
			"namespace", desiredRole.Namespace)
		return err
	}

	log.Info("Role reconciled",
		"name", existingRole.Name,
		"namespace", existingRole.Namespace,
		"operation", op)

	// RoleBinding
	// Note: RoleRef is immutable in Kubernetes. If it changes, we must delete and recreate.
	desiredRoleBinding := r.createRoleBinding(jumpstarter)
	if err := r.reconcileRoleBinding(ctx, jumpstarter, desiredRoleBinding); err != nil {
		return err
	}

	// Router Role (minimal permissions: read configmaps)
	desiredRouterRole := r.createRouterRole(jumpstarter)

	existingRouterRole := &rbacv1.Role{}
	existingRouterRole.Name = desiredRouterRole.Name
	existingRouterRole.Namespace = desiredRouterRole.Namespace

	op, err = controllerutil.CreateOrUpdate(ctx, r.Client, existingRouterRole, func() error {
		if existingRouterRole.CreationTimestamp.IsZero() {
			existingRouterRole.Labels = desiredRouterRole.Labels
			existingRouterRole.Annotations = desiredRouterRole.Annotations
			existingRouterRole.Rules = desiredRouterRole.Rules
			return controllerutil.SetControllerReference(jumpstarter, existingRouterRole, r.Scheme)
		}

		if !roleNeedsUpdate(existingRouterRole, desiredRouterRole) {
			log.V(1).Info("Router Role is up to date, skipping update",
				"name", existingRouterRole.Name,
				"namespace", existingRouterRole.Namespace)
			return nil
		}

		existingRouterRole.Labels = desiredRouterRole.Labels
		existingRouterRole.Annotations = desiredRouterRole.Annotations
		existingRouterRole.Rules = desiredRouterRole.Rules
		return controllerutil.SetControllerReference(jumpstarter, existingRouterRole, r.Scheme)
	})

	if err != nil {
		log.Error(err, "Failed to reconcile Router Role",
			"name", desiredRouterRole.Name,
			"namespace", desiredRouterRole.Namespace)
		return err
	}

	log.Info("Router Role reconciled",
		"name", existingRouterRole.Name,
		"namespace", existingRouterRole.Namespace,
		"operation", op)

	// Router RoleBinding
	// Note: RoleRef is immutable in Kubernetes. If it changes, we must delete and recreate.
	desiredRouterRoleBinding := r.createRouterRoleBinding(jumpstarter)
	if err := r.reconcileRoleBinding(ctx, jumpstarter, desiredRouterRoleBinding); err != nil {
		return err
	}

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
