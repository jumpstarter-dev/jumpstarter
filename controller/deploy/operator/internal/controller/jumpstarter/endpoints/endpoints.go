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

	"sigs.k8s.io/controller-runtime/pkg/client"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/utils"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// Reconciler provides endpoint reconciliation functionality
type Reconciler struct {
	Client client.Client
}

// serviceDetails contains the configuration details for creating a service
type serviceDetails struct {
	ServiceType corev1.ServiceType
	Annotations map[string]string
	Labels      map[string]string
	Suffix      string
}

// NewReconciler creates a new endpoint reconciler
func NewReconciler(client client.Client) *Reconciler {
	return &Reconciler{
		Client: client,
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
