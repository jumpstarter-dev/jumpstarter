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
	"fmt"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
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
	r.reconcileSecrets(ctx, &jumpstarter)

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
		if err := r.reconcileEndpointService(ctx, jumpstarter, &endpoint, "controller-grpc"); err != nil {
			return err
		}
	}

	// Reconcile router services
	for _, endpoint := range jumpstarter.Spec.Routers.GRPC.Endpoints {
		if err := r.reconcileEndpointService(ctx, jumpstarter, &endpoint, "router-grpc"); err != nil {
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
func (r *JumpstarterReconciler) reconcileSecrets(_ context.Context, jumpstarter *operatorv1alpha1.Jumpstarter) {
	// Create TLS secrets for endpoints if cert-manager is not used
	// This is a placeholder - actual implementation would generate certificates
	_ = jumpstarter.Spec.UseCertManager
}

// reconcileEndpointService reconciles a single endpoint service
func (r *JumpstarterReconciler) reconcileEndpointService(ctx context.Context, jumpstarter *operatorv1alpha1.Jumpstarter, endpoint *operatorv1alpha1.Endpoint, endpointName string) error {
	// Create service port
	svcPort := corev1.ServicePort{
		Name:       "grpc",
		Port:       9090,
		TargetPort: intstr.FromInt(9090),
		Protocol:   corev1.ProtocolTCP,
	}

	// Use the endpoint reconciler to create/update the service
	return r.EndpointReconciler.ReconcileEndpoint(ctx, jumpstarter.Namespace, endpoint, endpointName, svcPort)
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
							Name:  "controller",
							Image: jumpstarter.Spec.Controller.Image,
							Ports: []corev1.ContainerPort{
								{
									ContainerPort: 9090,
									Name:          "grpc",
								},
								{
									ContainerPort: 8080,
									Name:          "http",
								},
							},
							Resources: jumpstarter.Spec.Controller.Resources,
						},
					},
				},
			},
		},
	}
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
							Name:  "router",
							Image: jumpstarter.Spec.Routers.Image,
							Ports: []corev1.ContainerPort{
								{
									ContainerPort: 9090,
									Name:          "grpc",
								},
							},
							Resources: jumpstarter.Spec.Routers.Resources,
						},
					},
					TopologySpreadConstraints: jumpstarter.Spec.Routers.TopologySpreadConstraints,
				},
			},
		},
	}
}

// createConfigMap creates a configmap for jumpstarter configuration
func (r *JumpstarterReconciler) createConfigMap(jumpstarter *operatorv1alpha1.Jumpstarter) *corev1.ConfigMap {
	return &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-config", jumpstarter.Name),
			Namespace: jumpstarter.Namespace,
			Labels: map[string]string{
				"app": jumpstarter.Name,
			},
		},
		Data: map[string]string{
			"baseDomain":      jumpstarter.Spec.BaseDomain,
			"useCertManager":  fmt.Sprintf("%t", jumpstarter.Spec.UseCertManager),
			"controllerImage": jumpstarter.Spec.Controller.Image,
			"routerImage":     jumpstarter.Spec.Routers.Image,
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
		Owns(&corev1.Secret{}).
		Owns(&corev1.ConfigMap{}).
		Complete(r)
}
