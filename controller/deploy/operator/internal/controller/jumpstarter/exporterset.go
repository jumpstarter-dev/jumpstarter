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
	"context"
	"fmt"
	"regexp"
	"strings"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

// reconcileExporterSetControllers reconciles Deployments, ServiceAccounts, Roles, and
// RoleBindings for each enabled provisioner in spec.exporterSets.provisioners.
func (r *JumpstarterReconciler) reconcileExporterSetControllers(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	if jumpstarter.Spec.ExporterSets == nil || len(jumpstarter.Spec.ExporterSets.Provisioners) == 0 {
		return r.cleanupAllExporterSetControllers(ctx, jumpstarter)
	}

	enabledProvisioners := make(map[string]bool)
	sanitizedToOriginal := make(map[string]string)

	for _, prov := range jumpstarter.Spec.ExporterSets.Provisioners {
		enabled := prov.Enabled == nil || *prov.Enabled
		sanitized := sanitizeProvisionerName(prov.Name)

		if prev, exists := sanitizedToOriginal[sanitized]; exists {
			return fmt.Errorf("provisioner names %q and %q collide after sanitization (both become %q)", prev, prov.Name, sanitized)
		}
		sanitizedToOriginal[sanitized] = prov.Name
		enabledProvisioners[sanitized] = enabled

		if !enabled {
			continue
		}

		if err := r.reconcileExporterSetRBAC(ctx, jumpstarter, prov); err != nil {
			log.Error(err, "Failed to reconcile ExporterSet RBAC", "provisioner", prov.Name)
			return err
		}

		if err := r.reconcileExporterSetDeployment(ctx, jumpstarter, prov); err != nil {
			log.Error(err, "Failed to reconcile ExporterSet Deployment", "provisioner", prov.Name)
			return err
		}
	}

	return r.cleanupDisabledExporterSetControllers(ctx, jumpstarter, enabledProvisioners)
}

// reconcileExporterSetRBAC creates or updates the ServiceAccount, Role, and RoleBinding
// for a provisioner controller.
func (r *JumpstarterReconciler) reconcileExporterSetRBAC(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter, prov operatorv1alpha1.ProvisionerConfig) error {
	log := logf.FromContext(ctx)
	sanitized := sanitizeProvisionerName(prov.Name)

	// ServiceAccount (orphan pattern — no owner ref, same as existing SA in rbac.go)
	desiredSA := r.createExporterSetServiceAccount(jumpstarter, sanitized)
	existingSA := &corev1.ServiceAccount{}
	existingSA.Name = desiredSA.Name
	existingSA.Namespace = desiredSA.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existingSA, func() error {
		if existingSA.CreationTimestamp.IsZero() {
			existingSA.Labels = desiredSA.Labels
			return nil
		}
		if !serviceAccountNeedsUpdate(existingSA, desiredSA) {
			return nil
		}
		existingSA.Labels = desiredSA.Labels
		return nil
	})
	if err != nil {
		return fmt.Errorf("failed to reconcile ExporterSet ServiceAccount %s: %w", desiredSA.Name, err)
	}
	log.Info("ExporterSet ServiceAccount reconciled", "name", desiredSA.Name, "operation", op)

	// Role (owned by the Jumpstarter CR)
	desiredRole := r.createExporterSetRole(jumpstarter, sanitized)
	existingRole := &rbacv1.Role{}
	existingRole.Name = desiredRole.Name
	existingRole.Namespace = desiredRole.Namespace

	op, err = controllerutil.CreateOrUpdate(ctx, r.Client, existingRole, func() error {
		if existingRole.CreationTimestamp.IsZero() {
			existingRole.Labels = desiredRole.Labels
			existingRole.Rules = desiredRole.Rules
			return controllerutil.SetControllerReference(jumpstarter, existingRole, r.Scheme)
		}
		if !roleNeedsUpdate(existingRole, desiredRole) {
			return nil
		}
		existingRole.Labels = desiredRole.Labels
		existingRole.Rules = desiredRole.Rules
		return controllerutil.SetControllerReference(jumpstarter, existingRole, r.Scheme)
	})
	if err != nil {
		return fmt.Errorf("failed to reconcile ExporterSet Role %s: %w", desiredRole.Name, err)
	}
	log.Info("ExporterSet Role reconciled", "name", desiredRole.Name, "operation", op)

	// RoleBinding (owned by the Jumpstarter CR)
	desiredRB := r.createExporterSetRoleBinding(jumpstarter, sanitized)
	existingRB := &rbacv1.RoleBinding{}
	existingRB.Name = desiredRB.Name
	existingRB.Namespace = desiredRB.Namespace

	op, err = controllerutil.CreateOrUpdate(ctx, r.Client, existingRB, func() error {
		if existingRB.CreationTimestamp.IsZero() {
			existingRB.Labels = desiredRB.Labels
			existingRB.Subjects = desiredRB.Subjects
			existingRB.RoleRef = desiredRB.RoleRef
			return controllerutil.SetControllerReference(jumpstarter, existingRB, r.Scheme)
		}
		if !roleBindingNeedsUpdate(existingRB, desiredRB) {
			return nil
		}
		existingRB.Labels = desiredRB.Labels
		existingRB.Subjects = desiredRB.Subjects
		existingRB.RoleRef = desiredRB.RoleRef
		return controllerutil.SetControllerReference(jumpstarter, existingRB, r.Scheme)
	})
	if err != nil {
		return fmt.Errorf("failed to reconcile ExporterSet RoleBinding %s: %w", desiredRB.Name, err)
	}
	log.Info("ExporterSet RoleBinding reconciled", "name", desiredRB.Name, "operation", op)

	return nil
}

// reconcileExporterSetDeployment creates or updates the Deployment for a provisioner controller.
func (r *JumpstarterReconciler) reconcileExporterSetDeployment(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter, prov operatorv1alpha1.ProvisionerConfig) error {
	log := logf.FromContext(ctx)
	desiredDeployment := r.createExporterSetDeployment(jumpstarter, prov)

	existingDeployment := &appsv1.Deployment{}
	existingDeployment.Name = desiredDeployment.Name
	existingDeployment.Namespace = desiredDeployment.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existingDeployment, func() error {
		if existingDeployment.CreationTimestamp.IsZero() {
			existingDeployment.Labels = desiredDeployment.Labels
			existingDeployment.Spec = desiredDeployment.Spec
			return controllerutil.SetControllerReference(jumpstarter, existingDeployment, r.Scheme)
		}

		desiredDeployment.Spec.Template.Spec.DeprecatedServiceAccount = existingDeployment.Spec.Template.Spec.DeprecatedServiceAccount
		desiredDeployment.Spec.Template.Spec.SchedulerName = existingDeployment.Spec.Template.Spec.SchedulerName

		if !deploymentNeedsUpdate(existingDeployment, desiredDeployment) {
			log.V(1).Info("ExporterSet deployment is up to date", "name", existingDeployment.Name)
			return nil
		}

		diff, diffErr := generateDiff(existingDeployment, desiredDeployment)
		if diffErr != nil {
			log.V(1).Info("Failed to generate deployment diff", "error", diffErr)
		} else if diff != "" {
			fmt.Printf("\n=== ExporterSet controller deployment differences detected ===\n")
			fmt.Printf("Name: %s\n", existingDeployment.Name)
			fmt.Printf("Namespace: %s\n", existingDeployment.Namespace)
			fmt.Printf("\n%s\n", diff)
			fmt.Printf("==============================================================\n\n")
		}

		existingDeployment.Labels = desiredDeployment.Labels
		existingDeployment.Spec.Replicas = desiredDeployment.Spec.Replicas
		existingDeployment.Spec.Selector = desiredDeployment.Spec.Selector
		existingDeployment.Spec.Template = desiredDeployment.Spec.Template
		return controllerutil.SetControllerReference(jumpstarter, existingDeployment, r.Scheme)
	})

	if err != nil {
		log.Error(err, "Failed to reconcile ExporterSet deployment",
			"name", desiredDeployment.Name, "provisioner", prov.Name)
		return err
	}

	log.Info("ExporterSet deployment reconciled",
		"name", existingDeployment.Name, "provisioner", prov.Name, "operation", op)

	switch op {
	case controllerutil.OperationResultCreated:
		r.emitEventf(jumpstarter, corev1.EventTypeNormal, "ExporterSetControllerCreated",
			"ExporterSet controller deployment created: name=%s provisioner=%s",
			existingDeployment.Name, prov.Name)
	case controllerutil.OperationResultUpdated:
		r.emitEventf(jumpstarter, corev1.EventTypeNormal, "ExporterSetControllerUpdated",
			"ExporterSet controller deployment updated: name=%s provisioner=%s",
			existingDeployment.Name, prov.Name)
	}

	return nil
}

// cleanupDisabledExporterSetControllers deletes Deployments (and their RBAC) for
// provisioners that are no longer enabled.
func (r *JumpstarterReconciler) cleanupDisabledExporterSetControllers(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter, enabledProvisioners map[string]bool) error {
	log := logf.FromContext(ctx)

	deploymentList := &appsv1.DeploymentList{}
	if err := r.List(ctx, deploymentList,
		client.InNamespace(jumpstarter.Namespace),
		client.MatchingLabels{
			"component":  "exporterset-controller",
			"controller": jumpstarter.Name,
		},
	); err != nil {
		return fmt.Errorf("failed to list ExporterSet deployments: %w", err)
	}

	for i := range deploymentList.Items {
		dep := &deploymentList.Items[i]
		provLabel := dep.Labels["provisioner"]
		if provLabel == "" {
			continue
		}

		if enabled, found := enabledProvisioners[provLabel]; found && enabled {
			continue
		}

		log.Info("Deleting disabled ExporterSet controller", "deployment", dep.Name, "provisioner", provLabel)
		if err := r.Delete(ctx, dep); err != nil && !errors.IsNotFound(err) {
			return fmt.Errorf("failed to delete ExporterSet deployment %s: %w", dep.Name, err)
		}
		r.emitEventf(jumpstarter, corev1.EventTypeNormal, "ExporterSetControllerDeleted",
			"ExporterSet controller deployment deleted: name=%s provisioner=%s",
			dep.Name, provLabel)

		// Clean up the owned Role and RoleBinding (SA is kept as orphan)
		roleName := fmt.Sprintf("%s-exporterset-%s-role", jumpstarter.Name, provLabel)
		role := &rbacv1.Role{ObjectMeta: metav1.ObjectMeta{Name: roleName, Namespace: jumpstarter.Namespace}}
		if err := r.Delete(ctx, role); err != nil && !errors.IsNotFound(err) {
			log.Error(err, "Failed to delete ExporterSet Role", "name", roleName)
		}

		rbName := fmt.Sprintf("%s-exporterset-%s-rolebinding", jumpstarter.Name, provLabel)
		rb := &rbacv1.RoleBinding{ObjectMeta: metav1.ObjectMeta{Name: rbName, Namespace: jumpstarter.Namespace}}
		if err := r.Delete(ctx, rb); err != nil && !errors.IsNotFound(err) {
			log.Error(err, "Failed to delete ExporterSet RoleBinding", "name", rbName)
		}
	}

	return nil
}

// cleanupAllExporterSetControllers removes all ExporterSet controller resources when
// spec.exporterSets is nil or has no provisioners.
func (r *JumpstarterReconciler) cleanupAllExporterSetControllers(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	return r.cleanupDisabledExporterSetControllers(ctx, jumpstarter, map[string]bool{})
}

// --- Resource constructors ---

func (r *JumpstarterReconciler) createExporterSetServiceAccount(jumpstarter *operatorv1alpha1.Jumpstarter, sanitizedName string) *corev1.ServiceAccount {
	return &corev1.ServiceAccount{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-exporterset-%s", jumpstarter.Name, sanitizedName),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "exporterset-controller",
				"component":                    "exporterset-controller",
				"provisioner":                  sanitizedName,
				"controller":                   jumpstarter.Name,
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
	}
}

func (r *JumpstarterReconciler) createExporterSetRole(jumpstarter *operatorv1alpha1.Jumpstarter, sanitizedName string) *rbacv1.Role {
	return &rbacv1.Role{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-exporterset-%s-role", jumpstarter.Name, sanitizedName),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "exporterset-controller",
				"component":                    "exporterset-controller",
				"provisioner":                  sanitizedName,
				"controller":                   jumpstarter.Name,
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		Rules: exporterSetPolicyRules(),
	}
}

func (r *JumpstarterReconciler) createExporterSetRoleBinding(jumpstarter *operatorv1alpha1.Jumpstarter, sanitizedName string) *rbacv1.RoleBinding {
	saName := fmt.Sprintf("%s-exporterset-%s", jumpstarter.Name, sanitizedName)
	roleName := fmt.Sprintf("%s-exporterset-%s-role", jumpstarter.Name, sanitizedName)

	return &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-exporterset-%s-rolebinding", jumpstarter.Name, sanitizedName),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          "exporterset-controller",
				"component":                    "exporterset-controller",
				"provisioner":                  sanitizedName,
				"controller":                   jumpstarter.Name,
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
		},
		RoleRef: rbacv1.RoleRef{
			APIGroup: "rbac.authorization.k8s.io",
			Kind:     "Role",
			Name:     roleName,
		},
		Subjects: []rbacv1.Subject{
			{
				Kind:      "ServiceAccount",
				Name:      saName,
				Namespace: jumpstarter.Namespace,
			},
		},
	}
}

func (r *JumpstarterReconciler) createExporterSetDeployment(jumpstarter *operatorv1alpha1.Jumpstarter, prov operatorv1alpha1.ProvisionerConfig) *appsv1.Deployment {
	sanitized := sanitizeProvisionerName(prov.Name)
	esConfig := jumpstarter.Spec.ExporterSets

	image := esConfig.Image
	if prov.Image != "" {
		image = prov.Image
	}

	imagePullPolicy := esConfig.ImagePullPolicy
	if imagePullPolicy == "" {
		imagePullPolicy = corev1.PullIfNotPresent
	}

	replicas := int32(1)
	if prov.Replicas != nil {
		replicas = *prov.Replicas
	}

	labels := map[string]string{
		"component":   "exporterset-controller",
		"provisioner": sanitized,
		"controller":  jumpstarter.Name,
	}

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-exporterset-%s", jumpstarter.Name, sanitized),
			Namespace: jumpstarter.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas:                &replicas,
			ProgressDeadlineSeconds: ptr.To(int32(600)),
			RevisionHistoryLimit:    ptr.To(int32(10)),
			Strategy: appsv1.DeploymentStrategy{
				Type: appsv1.RollingUpdateDeploymentStrategyType,
				RollingUpdate: &appsv1.RollingUpdateDeployment{
					MaxSurge:       &intstr.IntOrString{Type: intstr.String, StrVal: "25%"},
					MaxUnavailable: &intstr.IntOrString{Type: intstr.String, StrVal: "25%"},
				},
			},
			Selector: &metav1.LabelSelector{
				MatchLabels: labels,
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: labels,
				},
				Spec: corev1.PodSpec{
					RestartPolicy:                 corev1.RestartPolicyAlways,
					DNSPolicy:                     corev1.DNSClusterFirst,
					TerminationGracePeriodSeconds: ptr.To(int64(30)),
					Containers: []corev1.Container{
						{
							Name:            "manager",
							Image:           image,
							ImagePullPolicy: imagePullPolicy,
							Command:         []string{"/exporter-set-controller"},
							Args: []string{
								fmt.Sprintf("--provisioner=%s", prov.Name),
								"--leader-elect",
								"--health-probe-bind-address=:8081",
								"--metrics-bind-address=0",
							},
							Ports: []corev1.ContainerPort{
								{
									ContainerPort: 8081,
									Name:          "health",
									Protocol:      corev1.ProtocolTCP,
								},
							},
							LivenessProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									HTTPGet: &corev1.HTTPGetAction{
										Path:   "/healthz",
										Port:   intstr.FromInt(8081),
										Scheme: corev1.URISchemeHTTP,
									},
								},
								InitialDelaySeconds: 15,
								PeriodSeconds:       20,
								TimeoutSeconds:      1,
								SuccessThreshold:    1,
								FailureThreshold:    3,
							},
							ReadinessProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									HTTPGet: &corev1.HTTPGetAction{
										Path:   "/readyz",
										Port:   intstr.FromInt(8081),
										Scheme: corev1.URISchemeHTTP,
									},
								},
								InitialDelaySeconds: 5,
								PeriodSeconds:       10,
								TimeoutSeconds:      1,
								SuccessThreshold:    1,
								FailureThreshold:    3,
							},
							Resources:                resolveExporterSetResources(esConfig.Resources, prov.Resources),
							TerminationMessagePath:   "/dev/termination-log",
							TerminationMessagePolicy: corev1.TerminationMessageReadFile,
							SecurityContext: &corev1.SecurityContext{
								AllowPrivilegeEscalation: boolPtr(false),
								Capabilities: &corev1.Capabilities{
									Drop: []corev1.Capability{"ALL"},
								},
							},
						},
					},
					SecurityContext: &corev1.PodSecurityContext{
						RunAsNonRoot: boolPtr(true),
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeRuntimeDefault,
						},
					},
					ServiceAccountName: fmt.Sprintf("%s-exporterset-%s", jumpstarter.Name, sanitized),
				},
			},
		},
	}
}

// exporterSetPolicyRules returns the RBAC rules needed by the exporter-set controller.
// The controller watches ExporterSets and manages Pods/Exporters; it does not create or
// delete ExporterSets themselves. update/patch on the main resource is needed for finalizers.
func exporterSetPolicyRules() []rbacv1.PolicyRule {
	return []rbacv1.PolicyRule{
		{
			APIGroups: []string{"virtualtarget.jumpstarter.dev"},
			Resources: []string{"exportersets"},
			Verbs:     []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{"virtualtarget.jumpstarter.dev"},
			Resources: []string{"exportersets/status", "exportersets/scale"},
			Verbs:     []string{"get", "update", "patch"},
		},
		{
			APIGroups: []string{"virtualtarget.jumpstarter.dev"},
			Resources: []string{"exportersets/finalizers"},
			Verbs:     []string{"update"},
		},
		{
			APIGroups: []string{"virtualtarget.jumpstarter.dev"},
			Resources: []string{"virtualtargetclasses"},
			Verbs:     []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{"jumpstarter.dev"},
			Resources: []string{"exporters"},
			Verbs:     []string{"get", "list", "watch", "create", "update", "patch", "delete"},
		},
		{
			APIGroups: []string{"jumpstarter.dev"},
			Resources: []string{"leases"},
			Verbs:     []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{""},
			Resources: []string{"pods"},
			Verbs:     []string{"get", "list", "watch", "create", "update", "patch", "delete"},
		},
		{
			APIGroups: []string{""},
			Resources: []string{"events"},
			Verbs:     []string{"create", "patch"},
		},
		{
			APIGroups: []string{""},
			Resources: []string{"secrets", "configmaps"},
			Verbs:     []string{"get", "list", "watch"},
		},
		{
			APIGroups: []string{"coordination.k8s.io"},
			Resources: []string{"leases"},
			Verbs:     []string{"get", "list", "watch", "create", "update", "patch", "delete"},
		},
	}
}

// resolveExporterSetResources returns per-provisioner resources if set,
// then falls back to global resources, then to defaults.
func resolveExporterSetResources(global corev1.ResourceRequirements, perProv *corev1.ResourceRequirements) corev1.ResourceRequirements {
	if perProv != nil {
		return defaultExporterSetControllerResources(*perProv)
	}
	return defaultExporterSetControllerResources(global)
}

// defaultExporterSetControllerResources returns sensible defaults for an exporter-set
// controller pod if no explicit resource requirements are provided.
func defaultExporterSetControllerResources(spec corev1.ResourceRequirements) corev1.ResourceRequirements {
	if len(spec.Requests) == 0 && len(spec.Limits) == 0 && len(spec.Claims) == 0 {
		return corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("100m"),
				corev1.ResourceMemory: resource.MustParse("256Mi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("500m"),
				corev1.ResourceMemory: resource.MustParse("512Mi"),
			},
		}
	}
	return spec
}

// invalidLabelChars matches any character not allowed in a DNS-1123 label value.
var invalidLabelChars = regexp.MustCompile(`[^a-z0-9-]`)

// sanitizeProvisionerName converts a provisioner name to a K8s-safe label/suffix value.
// It lowercases, replaces dots with dashes, strips any remaining invalid characters,
// and trims leading/trailing dashes.
// Example: "qemu.jumpstarter.dev" → "qemu-jumpstarter-dev"
func sanitizeProvisionerName(name string) string {
	s := strings.ToLower(name)
	s = strings.ReplaceAll(s, ".", "-")
	s = invalidLabelChars.ReplaceAllString(s, "")
	s = strings.Trim(s, "-")
	return s
}
