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
	"net"
	"strings"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/intstr"
	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/yaml"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/controller/jumpstarter/endpoints"
	loglevels "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/log"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/config"
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

// reconcileRouterDeployment reconciles router deployments (one per replica)
func (r *JumpstarterReconciler) reconcileRouterDeployment(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	// Create one deployment per replica
	for i := int32(0); i < jumpstarter.Spec.Routers.Replicas; i++ {
		deployment := r.createRouterDeployment(jumpstarter, i)

		// Set the owner reference
		if err := controllerutil.SetControllerReference(jumpstarter, deployment, r.Scheme); err != nil {
			return err
		}

		// Create or update the deployment
		_, err := controllerutil.CreateOrUpdate(ctx, r.Client, deployment, func() error {
			// Update deployment spec if needed
			return nil
		})
		if err != nil {
			return err
		}
	}

	// Clean up deployments for scaled-down replicas
	if err := r.cleanupExcessRouterDeployments(ctx, jumpstarter); err != nil {
		log.Error(err, "Failed to cleanup excess router deployments")
		return err
	}

	return nil
}

// reconcileServices reconciles all services
func (r *JumpstarterReconciler) reconcileServices(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	// Reconcile controller services
	for _, endpoint := range jumpstarter.Spec.Controller.GRPC.Endpoints {
		appProtocol := "h2c"
		svcPort := corev1.ServicePort{
			Name:        "controller-grpc",
			Port:        8082,
			TargetPort:  intstr.FromInt(8082),
			Protocol:    corev1.ProtocolTCP,
			AppProtocol: &appProtocol,
		}
		// Set NodePort if configured
		if endpoint.NodePort != nil && endpoint.NodePort.Enabled && endpoint.NodePort.Port > 0 {
			svcPort.NodePort = endpoint.NodePort.Port
		}
		if err := r.EndpointReconciler.ReconcileControllerEndpoint(ctx, jumpstarter, &endpoint, svcPort); err != nil {
			return err
		}
	}

	// Reconcile router services - one per replica, all endpoints per replica
	for i := int32(0); i < jumpstarter.Spec.Routers.Replicas; i++ {
		if len(jumpstarter.Spec.Routers.GRPC.Endpoints) > 0 {
			// Each replica gets ALL configured endpoints with replica substitution
			for endpointIdx, baseEndpoint := range jumpstarter.Spec.Routers.GRPC.Endpoints {
				endpoint := r.buildEndpointForReplica(jumpstarter, i, endpointIdx, &baseEndpoint)

				// Build unique service name for this replica AND endpoint
				// This allows multiple service types (NodePort, LoadBalancer, etc.) per replica
				serviceName := r.buildServiceNameForReplicaEndpoint(jumpstarter, i, endpointIdx)

				appProtocol := "h2c"
				svcPort := corev1.ServicePort{
					Name:        serviceName, // Unique name per replica+endpoint
					Port:        8083,
					TargetPort:  intstr.FromInt(8083),
					Protocol:    corev1.ProtocolTCP,
					AppProtocol: &appProtocol,
				}
				// Set NodePort if configured
				if endpoint.NodePort != nil && endpoint.NodePort.Enabled && endpoint.NodePort.Port > 0 {
					// increase nodeport numbers based in replica, not perfect because it needs to be
					// consecutive, but this is mostly for E2E testing.
					svcPort.NodePort = endpoint.NodePort.Port + int32(i)
				}
				if err := r.EndpointReconciler.ReconcileRouterReplicaEndpoint(ctx, jumpstarter, i, endpointIdx, &endpoint, svcPort); err != nil {
					return err
				}
			}
		} else {
			// No endpoints configured, create a default service without ingress/route
			endpoint := operatorv1alpha1.Endpoint{
				Address: fmt.Sprintf("router-%d.%s", i, jumpstarter.Spec.BaseDomain),
			}

			serviceName := fmt.Sprintf("%s-router-%d", jumpstarter.Name, i)
			appProtocol := "h2c"
			svcPort := corev1.ServicePort{
				Name:        serviceName,
				Port:        8083,
				TargetPort:  intstr.FromInt(8083),
				Protocol:    corev1.ProtocolTCP,
				AppProtocol: &appProtocol,
			}
			if err := r.EndpointReconciler.ReconcileRouterReplicaEndpoint(ctx, jumpstarter, i, 0, &endpoint, svcPort); err != nil {
				return err
			}
		}
	}

	// Clean up services for scaled-down replicas
	if err := r.cleanupExcessRouterServices(ctx, jumpstarter); err != nil {
		log.Error(err, "Failed to cleanup excess router services")
		return err
	}

	return nil
}

// reconcileConfigMaps reconciles all configmaps
func (r *JumpstarterReconciler) reconcileConfigMaps(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	configMap, err := r.createConfigMap(jumpstarter)
	if err != nil {
		return fmt.Errorf("failed to create configmap: %w", err)
	}

	// Set the owner reference
	if err := controllerutil.SetControllerReference(jumpstarter, configMap, r.Scheme); err != nil {
		return err
	}

	// Create or update the configmap
	_, err = controllerutil.CreateOrUpdate(ctx, r.Client, configMap, func() error {
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
		if ep.Address != "" {
			grpcEndpoint = ensurePort(ep.Address, "443")
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
									ContainerPort: 8082,
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

// createRouterDeployment creates a deployment for a specific router replica
func (r *JumpstarterReconciler) createRouterDeployment(jumpstarter *operatorv1alpha1.Jumpstarter, replicaIndex int32) *appsv1.Deployment {
	// Base app label that ALL services for this replica will select
	// Individual services will be named with endpoint suffixes, but all select the same pods
	baseAppLabel := fmt.Sprintf("%s-router-%d", jumpstarter.Name, replicaIndex)

	labels := map[string]string{
		"app":          baseAppLabel, // All services for this replica select by this label
		"router":       jumpstarter.Name,
		"router-index": fmt.Sprintf("%d", replicaIndex),
	}

	// Build router endpoint for this specific replica
	routerEndpoint := r.buildRouterEndpointForReplica(jumpstarter, replicaIndex)

	replicas := int32(1) // Each deployment has exactly 1 replica

	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-router-%d", jumpstarter.Name, replicaIndex),
			Namespace: jumpstarter.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.DeploymentSpec{
			Replicas: &replicas,
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
							Command:         []string{"/router"},
							Env: []corev1.EnvVar{
								{
									Name:  "GRPC_ROUTER_ENDPOINT",
									Value: routerEndpoint,
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
							},
							Ports: []corev1.ContainerPort{
								{
									ContainerPort: 8083,
									Name:          "grpc",
								},
							},
							Resources: jumpstarter.Spec.Routers.Resources,
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
					ServiceAccountName:            fmt.Sprintf("%s-controller-manager", jumpstarter.Name),
					TopologySpreadConstraints:     jumpstarter.Spec.Routers.TopologySpreadConstraints,
					TerminationGracePeriodSeconds: int64Ptr(10),
				},
			},
		},
	}
}

// createConfigMap creates a configmap for jumpstarter configuration
func (r *JumpstarterReconciler) createConfigMap(jumpstarter *operatorv1alpha1.Jumpstarter) (*corev1.ConfigMap, error) {
	// Build config struct from spec
	cfg := r.buildConfig(jumpstarter)

	// Marshal to YAML
	configYAML, err := yaml.Marshal(cfg)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal config to YAML: %w", err)
	}

	// Build router configuration for all replicas
	router := r.buildRouter(jumpstarter)

	// Marshal router to YAML
	routerYAML, err := yaml.Marshal(router)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal router to YAML: %w", err)
	}

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
			"config": string(configYAML),
			"router": string(routerYAML),
		},
	}, nil
}

// buildConfig builds the controller configuration struct from the CR spec
func (r *JumpstarterReconciler) buildConfig(jumpstarter *operatorv1alpha1.Jumpstarter) config.Config {
	cfg := config.Config{
		Provisioning: config.Provisioning{
			Enabled: false,
		},
		Grpc: config.Grpc{
			Keepalive: config.Keepalive{
				MinTime:             "1s",
				PermitWithoutStream: true,
			},
		},
	}

	// Authentication configuration
	auth := config.Authentication{
		JWT: jumpstarter.Spec.Controller.Authentication.JWT,
	}

	// Internal authentication
	if jumpstarter.Spec.Controller.Authentication.Internal.Enabled {
		prefix := jumpstarter.Spec.Controller.Authentication.Internal.Prefix
		if prefix == "" {
			prefix = "internal:"
		}
		auth.Internal.Prefix = prefix

		if jumpstarter.Spec.Controller.Authentication.Internal.TokenLifetime != nil {
			auth.Internal.TokenLifetime = jumpstarter.Spec.Controller.Authentication.Internal.TokenLifetime.Duration.String()
		}
	}

	// Kubernetes authentication
	if jumpstarter.Spec.Controller.Authentication.K8s.Enabled {
		auth.K8s.Enabled = true
	}

	// Ensure JWT is an empty array, not null
	if auth.JWT == nil {
		auth.JWT = []apiserverv1beta1.JWTAuthenticator{}
	}

	cfg.Authentication = auth

	// gRPC keepalive configuration
	if jumpstarter.Spec.Controller.GRPC.Keepalive != nil {
		ka := &cfg.Grpc.Keepalive

		if jumpstarter.Spec.Controller.GRPC.Keepalive.MinTime != nil {
			ka.MinTime = jumpstarter.Spec.Controller.GRPC.Keepalive.MinTime.Duration.String()
		}

		ka.PermitWithoutStream = jumpstarter.Spec.Controller.GRPC.Keepalive.PermitWithoutStream

		if jumpstarter.Spec.Controller.GRPC.Keepalive.Timeout != nil {
			ka.Timeout = jumpstarter.Spec.Controller.GRPC.Keepalive.Timeout.Duration.String()
		}

		if jumpstarter.Spec.Controller.GRPC.Keepalive.IntervalTime != nil {
			ka.IntervalTime = jumpstarter.Spec.Controller.GRPC.Keepalive.IntervalTime.Duration.String()
		}

		if jumpstarter.Spec.Controller.GRPC.Keepalive.MaxConnectionIdle != nil {
			ka.MaxConnectionIdle = jumpstarter.Spec.Controller.GRPC.Keepalive.MaxConnectionIdle.Duration.String()
		}

		if jumpstarter.Spec.Controller.GRPC.Keepalive.MaxConnectionAge != nil {
			ka.MaxConnectionAge = jumpstarter.Spec.Controller.GRPC.Keepalive.MaxConnectionAge.Duration.String()
		}

		if jumpstarter.Spec.Controller.GRPC.Keepalive.MaxConnectionAgeGrace != nil {
			ka.MaxConnectionAgeGrace = jumpstarter.Spec.Controller.GRPC.Keepalive.MaxConnectionAgeGrace.Duration.String()
		}
	}

	return cfg
}

// buildRouter builds the router configuration with entries for all replicas
func (r *JumpstarterReconciler) buildRouter(jumpstarter *operatorv1alpha1.Jumpstarter) config.Router {
	router := make(config.Router)

	// Create router entry for each replica
	for i := int32(0); i < jumpstarter.Spec.Routers.Replicas; i++ {
		// First replica is named "default" for backwards compatibility
		routerName := "default"
		if i > 0 {
			routerName = fmt.Sprintf("router-%d", i)
		}

		entry := config.RouterEntry{
			Endpoint: r.buildRouterEndpointForReplica(jumpstarter, i),
		}

		// Add labels if this is not the default router (replica 0)
		// Additional routers get labels to distinguish them
		if i > 0 {
			entry.Labels = map[string]string{
				"router-index": fmt.Sprintf("%d", i),
			}
		}

		router[routerName] = entry
	}

	return router
}

// buildRouterEndpointForReplica builds the GRPC_ROUTER_ENDPOINT for a specific replica
// This is the primary endpoint the router advertises itself as
func (r *JumpstarterReconciler) buildRouterEndpointForReplica(jumpstarter *operatorv1alpha1.Jumpstarter, replicaIndex int32) string {
	// If endpoints are specified, use the first one as the primary endpoint
	if len(jumpstarter.Spec.Routers.GRPC.Endpoints) > 0 {
		ep := jumpstarter.Spec.Routers.GRPC.Endpoints[0]
		address := ep.Address
		if address != "" {
			address = r.substituteReplica(address, replicaIndex)
			return ensurePort(address, "443")
		}
	}
	// Default pattern: router-N.baseDomain
	return fmt.Sprintf("router-%d.%s:443", replicaIndex, jumpstarter.Spec.BaseDomain)
}

// substituteReplica replaces $(replica) placeholder with actual replica index
func (r *JumpstarterReconciler) substituteReplica(address string, replicaIndex int32) string {
	return strings.ReplaceAll(address, "$(replica)", fmt.Sprintf("%d", replicaIndex))
}

// int64Ptr returns a pointer to an int64 value
func int64Ptr(i int64) *int64 {
	return &i
}

// ensurePort adds a default port to an address if it doesn't already have one
// Handles IPv4, IPv6, and hostnames correctly using net.SplitHostPort
func ensurePort(address, defaultPort string) string {
	// Try to split the address into host and port
	_, _, err := net.SplitHostPort(address)
	if err == nil {
		// Address already has a port, return as-is
		return address
	}

	// No port found, need to add one
	// net.JoinHostPort handles IPv6 addresses correctly (adds brackets if needed)
	return net.JoinHostPort(address, defaultPort)
}

// buildServiceNameForReplicaEndpoint creates a unique service name for a router replica and endpoint
func (r *JumpstarterReconciler) buildServiceNameForReplicaEndpoint(jumpstarter *operatorv1alpha1.Jumpstarter, replicaIndex int32, endpointIdx int) string {
	if endpointIdx == 0 {
		// First endpoint uses base name for backwards compatibility
		return fmt.Sprintf("%s-router-%d", jumpstarter.Name, replicaIndex)
	}
	// Additional endpoints get a suffix
	return fmt.Sprintf("%s-router-%d-%d", jumpstarter.Name, replicaIndex, endpointIdx)
}

// buildEndpointForReplica creates an Endpoint struct for a specific router replica and endpoint
func (r *JumpstarterReconciler) buildEndpointForReplica(jumpstarter *operatorv1alpha1.Jumpstarter, replicaIndex int32, endpointIdx int, baseEndpoint *operatorv1alpha1.Endpoint) operatorv1alpha1.Endpoint {
	// Copy the base endpoint
	endpoint := *baseEndpoint

	// Set or substitute address
	if endpoint.Address != "" {
		endpoint.Address = r.substituteReplica(endpoint.Address, replicaIndex)
	} else {
		// Default address pattern when none specified
		if endpointIdx == 0 {
			endpoint.Address = fmt.Sprintf("router-%d.%s", replicaIndex, jumpstarter.Spec.BaseDomain)
		} else {
			endpoint.Address = fmt.Sprintf("router-%d-%d.%s", replicaIndex, endpointIdx, jumpstarter.Spec.BaseDomain)
		}
	}

	return endpoint
}

// cleanupExcessRouterDeployments deletes router deployments that exceed the current replica count
func (r *JumpstarterReconciler) cleanupExcessRouterDeployments(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	// List all deployments with our router label
	deploymentList := &appsv1.DeploymentList{}
	listOpts := []client.ListOption{
		client.InNamespace(jumpstarter.Namespace),
		client.MatchingLabels{
			"router": jumpstarter.Name,
		},
	}

	if err := r.List(ctx, deploymentList, listOpts...); err != nil {
		return fmt.Errorf("failed to list router deployments: %w", err)
	}

	// Delete deployments with replica index >= current replica count
	for i := range deploymentList.Items {
		deployment := &deploymentList.Items[i]

		// Check if this deployment's name indicates it's beyond the current replica count
		// We need to check all indices from current replicas onwards
		for idx := jumpstarter.Spec.Routers.Replicas; idx < 100; idx++ { // reasonable upper bound
			excessName := fmt.Sprintf("%s-router-%d", jumpstarter.Name, idx)
			if deployment.Name == excessName {
				log.Info("Deleting excess router deployment", "deployment", deployment.Name, "replicaIndex", idx)
				if err := r.Delete(ctx, deployment); err != nil {
					if !errors.IsNotFound(err) {
						return fmt.Errorf("failed to delete excess deployment %s: %w", deployment.Name, err)
					}
				}
				break
			}
		}
	}

	return nil
}

// cleanupExcessRouterServices deletes router services that exceed the current replica count
// or endpoint count. This ensures that when replicas or endpoints are scaled down, the
// corresponding services are removed.
func (r *JumpstarterReconciler) cleanupExcessRouterServices(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	// Services can have suffixes for different service types
	// ClusterIP has no suffix, LoadBalancer has "-lb", NodePort has "-np"
	suffixes := []string{"", "-lb", "-np"}

	// 1. Delete services for excess replicas (replica index >= current replica count)
	for idx := jumpstarter.Spec.Routers.Replicas; idx < 100; idx++ { // reasonable upper bound
		foundAny := false

		// Try to delete services for all endpoints and service types for this replica
		for endpointIdx := 0; endpointIdx < 10; endpointIdx++ { // reasonable upper bound for endpoints
			for _, suffix := range suffixes {
				var serviceName string
				if endpointIdx == 0 {
					serviceName = fmt.Sprintf("%s-router-%d%s", jumpstarter.Name, idx, suffix)
				} else {
					serviceName = fmt.Sprintf("%s-router-%d-%d%s", jumpstarter.Name, idx, endpointIdx, suffix)
				}

				service := &corev1.Service{
					ObjectMeta: metav1.ObjectMeta{
						Name:      serviceName,
						Namespace: jumpstarter.Namespace,
					},
				}

				err := r.Delete(ctx, service)
				if err != nil {
					if !errors.IsNotFound(err) {
						return fmt.Errorf("failed to delete excess service %s: %w", serviceName, err)
					}
				} else {
					foundAny = true
					log.Info("Deleted excess router service", "service", serviceName, "replicaIndex", idx, "endpointIdx", endpointIdx)
				}
			}
		}

		// If we didn't find any services for this replica index, we've gone past all excess services
		if !foundAny {
			break
		}
	}

	// 2. Delete services for excess endpoints within valid replicas
	numEndpoints := len(jumpstarter.Spec.Routers.GRPC.Endpoints)
	if numEndpoints == 0 {
		numEndpoints = 1 // default endpoint
	}

	for replicaIdx := int32(0); replicaIdx < jumpstarter.Spec.Routers.Replicas; replicaIdx++ {
		for endpointIdx := numEndpoints; endpointIdx < 10; endpointIdx++ { // reasonable upper bound
			foundAny := false

			for _, suffix := range suffixes {
				var serviceName string
				if endpointIdx == 0 {
					serviceName = fmt.Sprintf("%s-router-%d%s", jumpstarter.Name, replicaIdx, suffix)
				} else {
					serviceName = fmt.Sprintf("%s-router-%d-%d%s", jumpstarter.Name, replicaIdx, endpointIdx, suffix)
				}

				service := &corev1.Service{
					ObjectMeta: metav1.ObjectMeta{
						Name:      serviceName,
						Namespace: jumpstarter.Namespace,
					},
				}

				err := r.Delete(ctx, service)
				if err != nil {
					if !errors.IsNotFound(err) {
						return fmt.Errorf("failed to delete excess endpoint service %s: %w", serviceName, err)
					}
				} else {
					foundAny = true
					log.Info("Deleted excess endpoint service", "service", serviceName, "replicaIndex", replicaIdx, "endpointIdx", endpointIdx)
				}
			}

			// If we didn't find any services for this endpoint index, we've gone past all excess endpoints
			if !foundAny {
				break
			}
		}
	}

	return nil
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
