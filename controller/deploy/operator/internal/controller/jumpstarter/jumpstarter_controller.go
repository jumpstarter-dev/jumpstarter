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
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/intstr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/controller/jumpstarter/endpoints"
	loglevels "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/log"
)

// JumpstarterReconciler reconciles a Jumpstarter object
type JumpstarterReconciler struct {
	client.Client
	Scheme             *runtime.Scheme
	EndpointReconciler *endpoints.Reconciler
}

// +kubebuilder:rbac:groups=operator.jumpstarter.dev,resources=jumpstarters,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=operator.jumpstarter.dev,resources=jumpstarters/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=operator.jumpstarter.dev,resources=jumpstarters/finalizers,verbs=update

// Core Kubernetes resources
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=apps,resources=deployments/status,verbs=get;update;patch
// +kubebuilder:rbac:groups="",resources=services,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=services/status,verbs=get;update;patch
// +kubebuilder:rbac:groups="",resources=configmaps,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=secrets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=serviceaccounts,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch

// RBAC resources
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=roles,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=rbac.authorization.k8s.io,resources=rolebindings,verbs=get;list;watch;create;update;patch;delete

// Leader election
// +kubebuilder:rbac:groups=coordination.k8s.io,resources=leases,verbs=get;list;watch;create;update;patch;delete

// Networking resources
// +kubebuilder:rbac:groups=networking.k8s.io,resources=ingresses,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=networking.k8s.io,resources=ingresses/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=route.openshift.io,resources=routes,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=route.openshift.io,resources=routes/status,verbs=get;update;patch

// Monitoring resources
// +kubebuilder:rbac:groups=monitoring.coreos.com,resources=servicemonitors,verbs=get;list;watch;create;update;patch;delete

// Jumpstarter CRD resources (needed to grant permissions to managed controllers)
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=clients,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=clients/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=clients/finalizers,verbs=update
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters/finalizers,verbs=update
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=leases,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=leases/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=leases/finalizers,verbs=update
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporteraccesspolicies,verbs=get;list;watch;create;update;patch;delete

// For more details, check Reconcile and its Result here:
// - https://pkg.go.dev/sigs.k8s.io/controller-runtime@v0.21.0/pkg/reconcile
func (r *JumpstarterReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	// Fetch the Jumpstarter instance
	var jumpstarter operatorv1alpha1.Jumpstarter
	if err := r.Get(ctx, req.NamespacedName, &jumpstarter); err != nil {
		if errors.IsNotFound(err) {
			// Request object not found, could have been deleted after reconcile request.
			// Owned objects are automatically garbage collected. For additional cleanup logic use finalizers.
			log.Info("Jumpstarter resource not found. Ignoring since object must be deleted.")
			return ctrl.Result{}, nil
		}
		// Error reading the object - requeue the request.
		log.Error(err, "Failed to get Jumpstarter")
		return ctrl.Result{}, err
	}

	// Check if the instance is marked to be deleted
	if jumpstarter.GetDeletionTimestamp() != nil {
		// Handle finalizer logic here if needed
		return ctrl.Result{}, nil
	}

	// Reconcile RBAC resources first
	if err := r.reconcileRBAC(ctx, &jumpstarter); err != nil {
		log.Error(err, "Failed to reconcile RBAC")
		return ctrl.Result{}, err
	}

	// Reconcile Controller Deployment
	if err := r.reconcileControllerDeployment(ctx, &jumpstarter); err != nil {
		log.Error(err, "Failed to reconcile Controller Deployment")
		return ctrl.Result{}, err
	}

	// Reconcile Router Deployment
	if err := r.reconcileRouterDeployment(ctx, &jumpstarter); err != nil {
		log.Error(err, "Failed to reconcile Router Deployment")
		return ctrl.Result{}, err
	}

	// Reconcile Services
	if err := r.reconcileServices(ctx, &jumpstarter); err != nil {
		log.Error(err, "Failed to reconcile Services")
		return ctrl.Result{}, err
	}

	// Reconcile ConfigMaps
	if err := r.reconcileConfigMaps(ctx, &jumpstarter); err != nil {
		log.Error(err, "Failed to reconcile ConfigMaps")
		return ctrl.Result{}, err
	}

	// Reconcile Secrets
	if err := r.reconcileSecrets(ctx, &jumpstarter); err != nil {
		log.Error(err, "Failed to reconcile Secrets")
		return ctrl.Result{}, err
	}

	// Update status
	if err := r.updateStatus(ctx, &jumpstarter); err != nil {
		log.Error(err, "Failed to update status")
		return ctrl.Result{}, err
	}

	// Requeue after 10 seconds to check for changes
	return ctrl.Result{RequeueAfter: 10 * time.Second}, nil
}

// reconcileControllerDeployment reconciles the controller deployment
func (r *JumpstarterReconciler) reconcileControllerDeployment(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	deployment := r.createControllerDeployment(jumpstarter)

	// Set the owner reference
	if err := controllerutil.SetControllerReference(jumpstarter, deployment, r.Scheme); err != nil {
		return err
	}

	// Create or update the deployment
	_, err := controllerutil.CreateOrUpdate(ctx, r.Client, deployment, func() error {
		// Update deployment spec if needed
		return nil
	})

	return err
}

// reconcileRouterDeployment reconciles the router deployment
func (r *JumpstarterReconciler) reconcileRouterDeployment(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	deployment := r.createRouterDeployment(jumpstarter)

	// Set the owner reference
	if err := controllerutil.SetControllerReference(jumpstarter, deployment, r.Scheme); err != nil {
		return err
	}

	// Create or update the deployment
	_, err := controllerutil.CreateOrUpdate(ctx, r.Client, deployment, func() error {
		// Update deployment spec if needed
		return nil
	})

	return err
}

// reconcileServices reconciles all services
func (r *JumpstarterReconciler) reconcileServices(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	// Reconcile controller services
	for _, endpoint := range jumpstarter.Spec.Controller.GRPC.Endpoints {
		svcPort := corev1.ServicePort{
			Name:       "controller-grpc",
			Port:       9090,
			TargetPort: intstr.FromInt(9090),
			Protocol:   corev1.ProtocolTCP,
		}
		if err := r.reconcileEndpointService(ctx, jumpstarter, &endpoint, svcPort); err != nil {
			return err
		}
	}

	// Reconcile router services
	for _, endpoint := range jumpstarter.Spec.Routers.GRPC.Endpoints {
		svcPort := corev1.ServicePort{
			Name:       "router-grpc",
			Port:       9090,
			TargetPort: intstr.FromInt(9090),
			Protocol:   corev1.ProtocolTCP,
		}
		if err := r.reconcileEndpointService(ctx, jumpstarter, &endpoint, svcPort); err != nil {
			return err
		}
	}

	return nil
}

// reconcileConfigMaps reconciles all configmaps
func (r *JumpstarterReconciler) reconcileConfigMaps(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	configMap := r.createConfigMap(jumpstarter)

	// Set the owner reference
	if err := controllerutil.SetControllerReference(jumpstarter, configMap, r.Scheme); err != nil {
		return err
	}

	// Create or update the configmap
	_, err := controllerutil.CreateOrUpdate(ctx, r.Client, configMap, func() error {
		// Update configmap data if needed
		return nil
	})

	return err
}

// reconcileSecrets reconciles all secrets
// Secrets are only created if they don't exist. They are not updated or deleted
// to preserve secret keys across CR updates and deletions.
func (r *JumpstarterReconciler) reconcileSecrets(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	// Create controller secret if it doesn't exist
	// Use fixed name to match Helm chart for migration compatibility
	controllerSecretName := "jumpstarter-controller-secret"
	if err := r.ensureSecretExists(ctx, jumpstarter, controllerSecretName); err != nil {
		log.Error(err, "Failed to ensure controller secret exists", "secret", controllerSecretName)
		return err
	}

	// Create router secret if it doesn't exist
	// Use fixed name to match Helm chart for migration compatibility
	routerSecretName := "jumpstarter-router-secret"
	if err := r.ensureSecretExists(ctx, jumpstarter, routerSecretName); err != nil {
		log.Error(err, "Failed to ensure router secret exists", "secret", routerSecretName)
		return err
	}

	return nil
}

// ensureSecretExists creates a secret only if it doesn't already exist
func (r *JumpstarterReconciler) ensureSecretExists(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter, name string) error {
	log := logf.FromContext(ctx)

	// Check if secret already exists
	existingSecret := &corev1.Secret{}
	err := r.Get(ctx, client.ObjectKey{
		Namespace: jumpstarter.Namespace,
		Name:      name,
	}, existingSecret)

	if err == nil {
		// Secret already exists, don't update it
		log.V(loglevels.LevelTrace).Info("Secret already exists, skipping creation", "secret", name)
		return nil
	}

	if !errors.IsNotFound(err) {
		// Some other error occurred
		return err
	}

	// Secret doesn't exist, create it with a random key
	randomKey, err := generateRandomKey(32)
	if err != nil {
		return fmt.Errorf("failed to generate random key: %w", err)
	}

	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":                          jumpstarter.Name,
				"app.kubernetes.io/managed-by": "jumpstarter-operator",
			},
			Annotations: map[string]string{
				"jumpstarter.dev/orphan": "true",
			},
		},
		StringData: map[string]string{
			"key": randomKey,
		},
	}

	// Note: We intentionally do NOT set owner reference here so that
	// secrets are not deleted when the Jumpstarter CR is deleted.
	// This preserves the secret keys across CR deletions and recreations.

	if err := r.Create(ctx, secret); err != nil {
		// Handle race condition where secret was created between Get and Create
		if errors.IsAlreadyExists(err) {
			log.V(loglevels.LevelDebug).Info("Secret was created by another reconciliation", "secret", name)
			return nil
		}
		return fmt.Errorf("failed to create secret: %w", err)
	}

	log.Info("Created new secret with random key", "secret", name)
	return nil
}

// generateRandomKey generates a cryptographically secure random key
func generateRandomKey(length int) (string, error) {
	bytes := make([]byte, length)
	if _, err := rand.Read(bytes); err != nil {
		return "", err
	}
	return base64.URLEncoding.EncodeToString(bytes), nil
}

// reconcileEndpointService reconciles a single endpoint service
func (r *JumpstarterReconciler) reconcileEndpointService(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter, endpoint *operatorv1alpha1.Endpoint, servicePort corev1.ServicePort) error {
	// Use the endpoint reconciler to create/update the service
	return r.EndpointReconciler.ReconcileEndpoint(ctx, jumpstarter.Namespace, endpoint, servicePort)
}

// updateStatus updates the status of the Jumpstarter resource
func (r *JumpstarterReconciler) updateStatus(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	// Update status fields based on current state
	// This is a placeholder - actual implementation would check deployment status, etc.
	// TODO: Add status fields to JumpstarterStatus in the API types

	return nil
}

// createControllerDeployment creates a deployment for the controller
func (r *JumpstarterReconciler) createControllerDeployment(jumpstarter *operatorv1alpha1.Jumpstarter) *appsv1.Deployment {
	labels := map[string]string{
		"app":        "jumpstarter-controller",
		"controller": jumpstarter.Name,
	}

	// Build GRPC endpoint from first controller endpoint
	// Default to port 443 for TLS gRPC endpoints
	grpcEndpoint := ""
	if len(jumpstarter.Spec.Controller.GRPC.Endpoints) > 0 {
		ep := jumpstarter.Spec.Controller.GRPC.Endpoints[0]
		if ep.Hostname != "" {
			grpcEndpoint = fmt.Sprintf("%s:443", ep.Hostname)
		} else {
			grpcEndpoint = fmt.Sprintf("grpc.%s:443", jumpstarter.Spec.BaseDomain)
		}
	}

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-controller", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: &jumpstarter.Spec.Controller.Replicas,
			Selector: &metav1.LabelSelector{
				MatchLabels: labels,
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: labels,
				},
				Spec: corev1.PodSpec{
					Containers: []corev1.Container{
						{
							Name:            "manager",
							Image:           jumpstarter.Spec.Controller.Image,
							ImagePullPolicy: jumpstarter.Spec.Controller.ImagePullPolicy,
							Args: []string{
								"--leader-elect",
								"--health-probe-bind-address=:8081",
								"-metrics-bind-address=:8080",
							},
							Env: []corev1.EnvVar{
								{
									Name:  "GRPC_ENDPOINT",
									Value: grpcEndpoint,
								},
								{
									Name: "CONTROLLER_KEY",
									ValueFrom: &corev1.EnvVarSource{
										SecretKeyRef: &corev1.SecretKeySelector{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "jumpstarter-controller-secret",
											},
											Key: "key",
										},
									},
								},
								{
									Name: "ROUTER_KEY",
									ValueFrom: &corev1.EnvVarSource{
										SecretKeyRef: &corev1.SecretKeySelector{
											LocalObjectReference: corev1.LocalObjectReference{
												Name: "jumpstarter-router-secret",
											},
											Key: "key",
										},
									},
								},
								{
									Name: "NAMESPACE",
									ValueFrom: &corev1.EnvVarSource{
										FieldRef: &corev1.ObjectFieldSelector{
											FieldPath: "metadata.namespace",
										},
									},
								},
								{
									Name:  "GIN_MODE",
									Value: "release",
								},
							},
							Ports: []corev1.ContainerPort{
								{
									ContainerPort: 9090,
									Name:          "grpc",
								},
								{
									ContainerPort: 8080,
									Name:          "metrics",
								},
								{
									ContainerPort: 8081,
									Name:          "health",
								},
							},
							LivenessProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									HTTPGet: &corev1.HTTPGetAction{
										Path: "/healthz",
										Port: intstr.FromInt(8081),
									},
								},
								InitialDelaySeconds: 15,
								PeriodSeconds:       20,
							},
							ReadinessProbe: &corev1.Probe{
								ProbeHandler: corev1.ProbeHandler{
									HTTPGet: &corev1.HTTPGetAction{
										Path: "/readyz",
										Port: intstr.FromInt(8081),
									},
								},
								InitialDelaySeconds: 5,
								PeriodSeconds:       10,
							},
							Resources: jumpstarter.Spec.Controller.Resources,
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
					ServiceAccountName: fmt.Sprintf("%s-controller-manager", jumpstarter.Name),
				},
			},
		},
	}
}

func boolPtr(b bool) *bool {
	return &b
}

// createRouterDeployment creates a deployment for the router
func (r *JumpstarterReconciler) createRouterDeployment(jumpstarter *operatorv1alpha1.Jumpstarter) *appsv1.Deployment {
	labels := map[string]string{
		"app":    "jumpstarter-router",
		"router": jumpstarter.Name,
	}

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-router", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: &jumpstarter.Spec.Routers.Replicas,
			Selector: &metav1.LabelSelector{
				MatchLabels: labels,
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: labels,
				},
				Spec: corev1.PodSpec{
					Containers: []corev1.Container{
						{
							Name:            "router",
							Image:           jumpstarter.Spec.Routers.Image,
							ImagePullPolicy: jumpstarter.Spec.Routers.ImagePullPolicy,
							Env: []corev1.EnvVar{
								{
									Name: "NAMESPACE",
									ValueFrom: &corev1.EnvVarSource{
										FieldRef: &corev1.ObjectFieldSelector{
											FieldPath: "metadata.namespace",
										},
									},
								},
							},
							Ports: []corev1.ContainerPort{
								{
									ContainerPort: 9090,
									Name:          "grpc",
								},
							},
							Resources: jumpstarter.Spec.Routers.Resources,
						},
					},
					ServiceAccountName:        fmt.Sprintf("%s-controller-manager", jumpstarter.Name),
					TopologySpreadConstraints: jumpstarter.Spec.Routers.TopologySpreadConstraints,
				},
			},
		},
	}
}

// createConfigMap creates a configmap for jumpstarter configuration
func (r *JumpstarterReconciler) createConfigMap(jumpstarter *operatorv1alpha1.Jumpstarter) *corev1.ConfigMap {
	// Build router configuration
	// Default to port 443 for TLS gRPC endpoints
	routerConfig := "default:\n"
	if len(jumpstarter.Spec.Routers.GRPC.Endpoints) > 0 {
		ep := jumpstarter.Spec.Routers.GRPC.Endpoints[0]
		if ep.Hostname != "" {
			routerConfig += fmt.Sprintf("  endpoint: %s:443\n", ep.Hostname)
		} else {
			routerConfig += fmt.Sprintf("  endpoint: router.%s:443\n", jumpstarter.Spec.BaseDomain)
		}
	}

	// Build config YAML
	configYAML := `authentication:
  internal:
    prefix: internal
  jwt: []
provisioning:
  enabled: false
grpc:
  keepalive:
    minTime: "1s"
    permitWithoutStream: true
`

	return &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-controller", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app":           "jumpstarter-controller",
				"control-plane": "controller-manager",
			},
		},
		Data: map[string]string{
			"config": configYAML,
			"router": routerConfig,
		},
	}
}

// SetupWithManager sets up the controller with the Manager.
func (r *JumpstarterReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&operatorv1alpha1.Jumpstarter{}).
		Named("jumpstarter").
		Owns(&appsv1.Deployment{}).
		Owns(&corev1.Service{}).
		Owns(&corev1.ConfigMap{}).
		Owns(&rbacv1.Role{}).
		Owns(&rbacv1.RoleBinding{}).
		// Note: Secrets and ServiceAccounts are intentionally NOT owned to prevent deletion
		Complete(r)
}
