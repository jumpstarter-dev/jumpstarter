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
	"errors"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/utils"
)

// Reconciler provides endpoint reconciliation functionality
type Reconciler struct {
	Client client.Client
	Scheme *runtime.Scheme
}

// serviceDetails contains the configuration details for creating a service
type serviceDetails struct {
	ServiceType corev1.ServiceType
	Annotations map[string]string
	Labels      map[string]string
	Suffix      string
}

// NewReconciler creates a new endpoint reconciler
func NewReconciler(client client.Client, scheme *runtime.Scheme) *Reconciler {
	return &Reconciler{
		Client: client,
		Scheme: scheme,
	}
}

// ReconcileEndpoint creates or updates a service for the given endpoint
func (r *Reconciler) ReconcileEndpoint(ctx context.Context, namespace string, endpoint *operatorv1alpha1.Endpoint, svcPort corev1.ServicePort) error {
	// Extract endpoint name from service port name
	endpointName := svcPort.Name

	details, err := serviceDetailsForEndpoint(*endpoint)
	if err != nil {
		return fmt.Errorf("reconcileEndpoint: failed calculate service type for endpoint %q: %w", endpointName, err)
	}
	// add app label to the service
	if details.Labels == nil {
		details.Labels = make(map[string]string)
	}
	details.Labels["app"] = endpointName

	// ensure annotations is not nil
	if details.Annotations == nil {
		details.Annotations = make(map[string]string)
	}

	// create the service for the endpoint
	service := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:        endpointName,
			Namespace:   namespace,
			Annotations: details.Annotations,
			Labels:      details.Labels,
		},
		Spec: corev1.ServiceSpec{
			Selector: map[string]string{
				"app": endpointName,
			},
			Ports: []corev1.ServicePort{svcPort},
			Type:  details.ServiceType,
		},
	}

	// Create or update the service using controller-runtime's CreateOrUpdate
	_, err = r.createOrUpdateService(ctx, service)
	return err
}

// createOrUpdateService creates or updates a service using controller-runtime pattern
func (r *Reconciler) createOrUpdateService(ctx context.Context, desiredService *corev1.Service) (bool, error) {
	existingService := &corev1.Service{}
	err := r.Client.Get(ctx, client.ObjectKeyFromObject(desiredService), existingService)
	if err != nil {
		if client.IgnoreNotFound(err) != nil {
			return false, err
		}
		// Service doesn't exist, create it
		if err := r.Client.Create(ctx, desiredService); err != nil {
			return false, err
		}
		return true, nil
	}

	// Service exists, check if it needs updating
	if r.serviceNeedsUpdate(existingService, desiredService) {
		// Preserve existing NodePorts to prevent "port already allocated" errors
		if existingService.Spec.Type == corev1.ServiceTypeNodePort || existingService.Spec.Type == corev1.ServiceTypeLoadBalancer {
			for i := range existingService.Spec.Ports {
				if existingService.Spec.Ports[i].NodePort != 0 && i < len(desiredService.Spec.Ports) {
					desiredService.Spec.Ports[i].NodePort = existingService.Spec.Ports[i].NodePort
				}
			}
		}
		// Preserve immutable fields
		desiredService.Spec.ClusterIP = existingService.Spec.ClusterIP
		desiredService.Spec.ClusterIPs = existingService.Spec.ClusterIPs
		desiredService.Spec.IPFamilies = existingService.Spec.IPFamilies
		desiredService.Spec.IPFamilyPolicy = existingService.Spec.IPFamilyPolicy

		// finally update the existing service spec
		existingService.Spec = desiredService.Spec
		existingService.Annotations = desiredService.Annotations
		existingService.Labels = desiredService.Labels

		if err := r.Client.Update(ctx, existingService); err != nil {
			return false, err
		}
		return true, nil
	}

	return false, nil
}

// serviceNeedsUpdate checks if the service needs to be updated
func (r *Reconciler) serviceNeedsUpdate(existing, desired *corev1.Service) bool {
	// Check if specs are different
	if existing.Spec.Type != desired.Spec.Type ||
		len(existing.Spec.Ports) != len(desired.Spec.Ports) ||
		!utils.MapsEqual(existing.Spec.Selector, desired.Spec.Selector) {
		return true
	}

	// Check if port details are different
	for i := range existing.Spec.Ports {
		existingPort := existing.Spec.Ports[i]
		desiredPort := desired.Spec.Ports[i]

		// Compare port fields (excluding NodePort which is handled separately)
		if existingPort.Name != desiredPort.Name ||
			existingPort.Protocol != desiredPort.Protocol ||
			existingPort.Port != desiredPort.Port ||
			existingPort.TargetPort != desiredPort.TargetPort {
			return true
		}

		// Compare AppProtocol (handle nil cases)
		if (existingPort.AppProtocol == nil) != (desiredPort.AppProtocol == nil) {
			return true
		}
		if existingPort.AppProtocol != nil && desiredPort.AppProtocol != nil &&
			*existingPort.AppProtocol != *desiredPort.AppProtocol {
			return true
		}
	}

	// Check if annotations or labels are different
	if !utils.MapsEqual(existing.Annotations, desired.Annotations) ||
		!utils.MapsEqual(existing.Labels, desired.Labels) {
		return true
	}

	return false
}

// serviceDetailsForEndpoint returns the service configuration details for the endpoint.
// It returns an error if both LoadBalancer and NodePort are enabled for the same endpoint.
func serviceDetailsForEndpoint(endpoint operatorv1alpha1.Endpoint) (*serviceDetails, error) {
	if endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled &&
		endpoint.NodePort != nil && endpoint.NodePort.Enabled {
		return nil, errors.New("both LoadBalancer and NodePort are enabled for the same endpoint")
	}
	if endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled {
		return &serviceDetails{
			ServiceType: corev1.ServiceTypeLoadBalancer,
			Annotations: endpoint.LoadBalancer.Annotations,
			Labels:      endpoint.LoadBalancer.Labels,
			Suffix:      "-lb",
		}, nil
	}
	if endpoint.NodePort != nil && endpoint.NodePort.Enabled {
		return &serviceDetails{
			ServiceType: corev1.ServiceTypeNodePort,
			Annotations: endpoint.NodePort.Annotations,
			Labels:      endpoint.NodePort.Labels,
			Suffix:      "-nodeport",
		}, nil
	}

	return &serviceDetails{
		ServiceType: corev1.ServiceTypeClusterIP,
		Annotations: nil,
		Labels:      nil,
		Suffix:      "",
	}, nil
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

	// Create a service for each enabled service type
	// This allows multiple service types to coexist for the same endpoint
	// Note: ClusterIP uses no suffix (most common for in-cluster communication)
	//       LoadBalancer uses "-lb" suffix, NodePort uses "-np" suffix

	// LoadBalancer service
	if endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeLoadBalancer, "-lb", "jumpstarter-controller", baseLabels, endpoint.LoadBalancer.Annotations, endpoint.LoadBalancer.Labels); err != nil {
			return err
		}
	}

	// NodePort service
	if endpoint.NodePort != nil && endpoint.NodePort.Enabled {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeNodePort, "-np", "jumpstarter-controller", baseLabels, endpoint.NodePort.Annotations, endpoint.NodePort.Labels); err != nil {
			return err
		}
	}

	// ClusterIP service (no suffix for cleaner in-cluster service names)
	if endpoint.ClusterIP != nil && endpoint.ClusterIP.Enabled {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeClusterIP, "", "jumpstarter-controller", baseLabels, endpoint.ClusterIP.Annotations, endpoint.ClusterIP.Labels); err != nil {
			return err
		}
	}

	// If no service type is explicitly enabled, create a default ClusterIP service
	if (endpoint.LoadBalancer == nil || !endpoint.LoadBalancer.Enabled) &&
		(endpoint.NodePort == nil || !endpoint.NodePort.Enabled) &&
		(endpoint.ClusterIP == nil || !endpoint.ClusterIP.Enabled) {

		// TODO: Default to Route or Ingress depending of the type of cluster
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeClusterIP, "", "jumpstarter-controller", baseLabels, nil, nil); err != nil {
			return err
		}
	}

	return nil
}

// ReconcileRouterReplicaEndpoint reconciles service, ingress, and route for a specific router replica endpoint
// This function creates a separate service for each enabled service type (ClusterIP, NodePort, LoadBalancer)
func (r *Reconciler) ReconcileRouterReplicaEndpoint(ctx context.Context, owner metav1.Object, replicaIndex int32, endpointIdx int, endpoint *operatorv1alpha1.Endpoint, servicePort corev1.ServicePort) error {
	// Create service with proper selector pointing to the deployment pods
	// All services for this replica select the same pods using the base app label
	baseAppLabel := fmt.Sprintf("%s-router-%d", owner.GetName(), replicaIndex)

	baseLabels := map[string]string{
		"app":          "jumpstarter-router",
		"router":       owner.GetName(),
		"router-index": fmt.Sprintf("%d", replicaIndex),
		"endpoint-idx": fmt.Sprintf("%d", endpointIdx),
	}

	// Create a service for each enabled service type
	// This allows multiple service types to coexist for the same endpoint
	// Note: ClusterIP uses no suffix (most common for in-cluster communication)
	//       LoadBalancer uses "-lb" suffix, NodePort uses "-np" suffix

	// Ingress service
	if endpoint.Ingress != nil && endpoint.Ingress.Enabled {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeClusterIP, "-ing", baseAppLabel, baseLabels, endpoint.Ingress.Annotations, endpoint.Ingress.Labels); err != nil {
			return err
		}
	}

	// Route service
	if endpoint.Route != nil && endpoint.Route.Enabled {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeClusterIP, "-route", baseAppLabel, baseLabels, endpoint.Route.Annotations, endpoint.Route.Labels); err != nil {
			return err
		}
	}

	// LoadBalancer service
	if endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeLoadBalancer, "-lb", baseAppLabel, baseLabels, endpoint.LoadBalancer.Annotations, endpoint.LoadBalancer.Labels); err != nil {
			return err
		}
	}

	// NodePort service
	if endpoint.NodePort != nil && endpoint.NodePort.Enabled {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeNodePort, "-np", baseAppLabel, baseLabels, endpoint.NodePort.Annotations, endpoint.NodePort.Labels); err != nil {
			return err
		}
	}

	// ClusterIP service (no suffix for cleaner in-cluster service names)
	if endpoint.ClusterIP != nil && endpoint.ClusterIP.Enabled {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeClusterIP, "", baseAppLabel, baseLabels, endpoint.ClusterIP.Annotations, endpoint.ClusterIP.Labels); err != nil {
			return err
		}
	}

	// If no service type is explicitly enabled, create a default ClusterIP service
	if (endpoint.LoadBalancer == nil || !endpoint.LoadBalancer.Enabled) &&
		(endpoint.NodePort == nil || !endpoint.NodePort.Enabled) &&
		(endpoint.ClusterIP == nil || !endpoint.ClusterIP.Enabled) {
		if err := r.createService(ctx, owner, endpoint, servicePort, corev1.ServiceTypeClusterIP, "", baseAppLabel, baseLabels, nil, nil); err != nil {
			return err
		}
	}

	// Now create ingress/route if configured
	// Use the first service (or default) for ingress/route endpoints
	// Priority: LoadBalancer > NodePort > ClusterIP (no suffix)
	serviceName := servicePort.Name
	if endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled {
		serviceName = servicePort.Name + "-lb"
	} else if endpoint.NodePort != nil && endpoint.NodePort.Enabled {
		serviceName = servicePort.Name + "-np"
	}
	// ClusterIP uses base name (no suffix), so no else clause needed

	servicePortForEndpoint := servicePort
	servicePortForEndpoint.Name = serviceName
	return r.ReconcileEndpoint(ctx, owner.GetNamespace(), endpoint, servicePortForEndpoint)
}

// createService creates or updates a single service with the specified type and suffix
func (r *Reconciler) createService(ctx context.Context, owner metav1.Object, endpoint *operatorv1alpha1.Endpoint, servicePort corev1.ServicePort, serviceType corev1.ServiceType, nameSuffix string, podSelector string, baseLabels map[string]string, annotations map[string]string, extraLabels map[string]string) error {
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
			Selector: map[string]string{
				"app": podSelector, // Select pods with the specified selector
			},
			Ports: []corev1.ServicePort{servicePort},
			Type:  serviceType,
		},
	}

	if err := controllerutil.SetControllerReference(owner, service, r.Scheme); err != nil {
		return err
	}

	_, err := controllerutil.CreateOrUpdate(ctx, r.Client, service, func() error {
		// Preserve existing NodePort if the service already exists
		// This prevents "port already allocated" errors during updates
		if serviceType == corev1.ServiceTypeNodePort && len(service.Spec.Ports) > 0 && service.Spec.Ports[0].NodePort != 0 {
			// Service already exists with a NodePort, preserve it
			servicePort.NodePort = service.Spec.Ports[0].NodePort
		}

		service.Spec.Selector = map[string]string{
			"app": podSelector,
		}
		service.Spec.Ports = []corev1.ServicePort{servicePort}
		service.Spec.Type = serviceType
		service.Labels = serviceLabels
		service.Annotations = annotations
		return nil
	})
	return err
}
