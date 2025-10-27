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
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

// Reconciler provides endpoint reconciliation functionality
type Reconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// NewReconciler creates a new endpoint reconciler
func NewReconciler(client client.Client, scheme *runtime.Scheme) *Reconciler {
	return &Reconciler{
		Client: client,
		Scheme: scheme,
	}
}

// createOrUpdateService creates or updates a service with proper handling of immutable fields
// and owner references. This is the unified service creation method.
func (r *Reconciler) createOrUpdateService(ctx context.Context, service *corev1.Service, owner metav1.Object) error {
	log := logf.FromContext(ctx)

	existingService := &corev1.Service{}
	existingService.Name = service.Name
	existingService.Namespace = service.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existingService, func() error {
		// Preserve immutable fields if service already exists
		if existingService.CreationTimestamp.IsZero() {
			// Service is being created, copy all fields from desired service
			existingService.Spec = service.Spec
			existingService.Labels = service.Labels
			existingService.Annotations = service.Annotations
			return controllerutil.SetControllerReference(owner, existingService, r.Scheme)

		} else {
			// Preserve existing NodePorts to prevent "port already allocated" errors
			if service.Spec.Type == corev1.ServiceTypeNodePort || service.Spec.Type == corev1.ServiceTypeLoadBalancer {
				for i := range existingService.Spec.Ports {
					if existingService.Spec.Ports[i].NodePort != 0 && i < len(service.Spec.Ports) {
						service.Spec.Ports[i].NodePort = existingService.Spec.Ports[i].NodePort
					}
				}
			}

			// Update all mutable fields
			if service.Spec.LoadBalancerClass != nil && *service.Spec.LoadBalancerClass != "" {
				existingService.Spec.LoadBalancerClass = service.Spec.LoadBalancerClass
			}
			if service.Spec.ExternalTrafficPolicy != "" {
				existingService.Spec.ExternalTrafficPolicy = service.Spec.ExternalTrafficPolicy
			}

			existingService.Spec.Ports = service.Spec.Ports
			existingService.Spec.Selector = service.Spec.Selector
			existingService.Spec.Type = service.Spec.Type
			existingService.Labels = service.Labels
			existingService.Annotations = service.Annotations
			return controllerutil.SetControllerReference(owner, existingService, r.Scheme)
		}
	})

	if err != nil {
		log.Error(err, "Failed to reconcile service",
			"name", service.Name,
			"namespace", service.Namespace,
			"type", service.Spec.Type)
		return err
	}

	log.Info("Service reconciled",
		"name", service.Name,
		"namespace", service.Namespace,
		"type", service.Spec.Type,
		"selector", service.Spec.Selector,
		"operation", op)

	return nil
}

// ReconcileControllerEndpoint reconciles a controller endpoint service with proper pod selector
// This function creates a separate service for each enabled service type (ClusterIP, NodePort, LoadBalancer)
func (r *Reconciler) ReconcileControllerEndpoint(ctx context.Context, owner metav1.Object, endpoint *operatorv1alpha1.Endpoint, servicePort corev1.ServicePort) error {
	// Controller pods have fixed labels: app=jumpstarter-controller
	// We need to create a service with selector matching those labels
	baseLabels := map[string]string{
		"app":        "jumpstarter-controller",
		"controller": owner.GetName(),
	}

	// Pod selector for controller pods
	podSelector := map[string]string{
		"app": "jumpstarter-controller",
	}

	// Create a service for each enabled service type
	// This allows multiple service types to coexist for the same endpoint
	// Note: ClusterIP uses no suffix (most common for in-cluster communication)
	//       LoadBalancer uses "-lb" suffix, NodePort uses "-np" suffix

	// LoadBalancer service
	if endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled {
		if err := r.createService(ctx, owner, servicePort, "-lb", corev1.ServiceTypeLoadBalancer,
			podSelector, baseLabels, endpoint.LoadBalancer.Annotations, endpoint.LoadBalancer.Labels); err != nil {
			return err
		}
	}

	// NodePort service
	if endpoint.NodePort != nil && endpoint.NodePort.Enabled {
		if err := r.createService(ctx, owner, servicePort, "-np", corev1.ServiceTypeNodePort,
			podSelector, baseLabels, endpoint.NodePort.Annotations, endpoint.NodePort.Labels); err != nil {
			return err
		}
	}

	// ClusterIP service (no suffix for cleaner in-cluster service names)
	if endpoint.ClusterIP != nil && endpoint.ClusterIP.Enabled {
		if err := r.createService(ctx, owner, servicePort, "", corev1.ServiceTypeClusterIP,
			podSelector, baseLabels, endpoint.ClusterIP.Annotations, endpoint.ClusterIP.Labels); err != nil {
			return err
		}
	}

	// If no service type is explicitly enabled, create a default ClusterIP service
	if (endpoint.LoadBalancer == nil || !endpoint.LoadBalancer.Enabled) &&
		(endpoint.NodePort == nil || !endpoint.NodePort.Enabled) &&
		(endpoint.ClusterIP == nil || !endpoint.ClusterIP.Enabled) {

		// TODO: Default to Route or Ingress depending of the type of cluster
		if err := r.createService(ctx, owner, servicePort, "", corev1.ServiceTypeClusterIP,
			podSelector, baseLabels, nil, nil); err != nil {
			return err
		}
	}

	return nil
}

// ReconcileRouterReplicaEndpoint reconciles service, ingress, and route for a specific router replica endpoint
// This function creates a separate service for each enabled service type (ClusterIP, NodePort, LoadBalancer)
func (r *Reconciler) ReconcileRouterReplicaEndpoint(ctx context.Context, owner metav1.Object, replicaIndex int32, endpointIdx int, endpoint *operatorv1alpha1.Endpoint, servicePort corev1.ServicePort) error {
	// IMPORTANT: The pod selector must match the actual pod labels
	// Router pods have label: app: jumpstarter-router-0 (for replica 0)
	baseAppLabel := fmt.Sprintf("%s-router-%d", owner.GetName(), replicaIndex)

	baseLabels := map[string]string{
		"app":          "jumpstarter-router",
		"router":       owner.GetName(),
		"router-index": fmt.Sprintf("%d", replicaIndex),
		"endpoint-idx": fmt.Sprintf("%d", endpointIdx),
	}

	// Pod selector - this MUST match the deployment's pod template labels
	podSelector := map[string]string{
		"app": baseAppLabel, // e.g., "jumpstarter-router-0"
	}

	// Create a service for each enabled service type
	// This allows multiple service types to coexist for the same endpoint
	// Note: ClusterIP uses no suffix (most common for in-cluster communication)
	//       LoadBalancer uses "-lb" suffix, NodePort uses "-np" suffix

	// Ingress service
	if endpoint.Ingress != nil && endpoint.Ingress.Enabled {
		if err := r.createService(ctx, owner, servicePort, "-ing", corev1.ServiceTypeClusterIP,
			podSelector, baseLabels, endpoint.Ingress.Annotations, endpoint.Ingress.Labels); err != nil {
			return err
		}
	}

	// Route service
	if endpoint.Route != nil && endpoint.Route.Enabled {
		if err := r.createService(ctx, owner, servicePort, "-route", corev1.ServiceTypeClusterIP,
			podSelector, baseLabels, endpoint.Route.Annotations, endpoint.Route.Labels); err != nil {
			return err
		}
	}

	// LoadBalancer service
	if endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled {
		if err := r.createService(ctx, owner, servicePort, "-lb", corev1.ServiceTypeLoadBalancer,
			podSelector, baseLabels, endpoint.LoadBalancer.Annotations, endpoint.LoadBalancer.Labels); err != nil {
			return err
		}
	}

	// NodePort service
	if endpoint.NodePort != nil && endpoint.NodePort.Enabled {
		if err := r.createService(ctx, owner, servicePort, "-np", corev1.ServiceTypeNodePort,
			podSelector, baseLabels, endpoint.NodePort.Annotations, endpoint.NodePort.Labels); err != nil {
			return err
		}
	}

	// ClusterIP service (no suffix for cleaner in-cluster service names)
	if endpoint.ClusterIP != nil && endpoint.ClusterIP.Enabled {
		if err := r.createService(ctx, owner, servicePort, "", corev1.ServiceTypeClusterIP,
			podSelector, baseLabels, endpoint.ClusterIP.Annotations, endpoint.ClusterIP.Labels); err != nil {
			return err
		}
	}

	// If no service type is explicitly enabled, create a default ClusterIP service
	if (endpoint.LoadBalancer == nil || !endpoint.LoadBalancer.Enabled) &&
		(endpoint.NodePort == nil || !endpoint.NodePort.Enabled) &&
		(endpoint.ClusterIP == nil || !endpoint.ClusterIP.Enabled) &&
		(endpoint.Ingress == nil || !endpoint.Ingress.Enabled) &&
		(endpoint.Route == nil || !endpoint.Route.Enabled) {
		if err := r.createService(ctx, owner, servicePort, "", corev1.ServiceTypeClusterIP,
			podSelector, baseLabels, nil, nil); err != nil {
			return err
		}
	}

	// TODO: Create ingress/route resources here instead of calling the deprecated ReconcileEndpoint
	// For now, ingress and route are handled by creating ClusterIP services above

	return nil
}

// createService creates or updates a single service with the specified type and suffix
// This is the unified service creation method that uses createOrUpdateService internally
func (r *Reconciler) createService(ctx context.Context, owner metav1.Object, servicePort corev1.ServicePort,
	nameSuffix string, serviceType corev1.ServiceType, podSelector map[string]string,
	baseLabels map[string]string, annotations map[string]string, extraLabels map[string]string) error {

	// Build service name with suffix to avoid conflicts
	serviceName := servicePort.Name + nameSuffix

	// Merge labels
	serviceLabels := make(map[string]string)
	for k, v := range baseLabels {
		serviceLabels[k] = v
	}
	for k, v := range extraLabels {
		serviceLabels[k] = v
	}

	// Ensure annotations map is initialized
	if annotations == nil {
		annotations = make(map[string]string)
	}

	service := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:        serviceName,
			Namespace:   owner.GetNamespace(),
			Labels:      serviceLabels,
			Annotations: annotations,
		},
		Spec: corev1.ServiceSpec{
			Selector: podSelector, // Use the provided pod selector map
			Ports:    []corev1.ServicePort{servicePort},
			Type:     serviceType,
		},
	}

	return r.createOrUpdateService(ctx, service, owner)
}
