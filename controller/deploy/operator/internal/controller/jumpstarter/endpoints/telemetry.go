/*
Copyright 2026. The Jumpstarter Authors.

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
	"net"
	"strconv"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

// ReconcileTelemetryEndpoint exposes only the telemetry gRPC port, not HTTP metrics.
func (r *Reconciler) ReconcileTelemetryEndpoint(ctx context.Context, owner metav1.Object, endpoint *operatorv1alpha1.Endpoint,
	servicePort corev1.ServicePort) error {
	labels := map[string]string{
		"component": "telemetry", "app": "jumpstarter-telemetry", "controller": owner.GetName(),
	}
	if endpoint.Ingress != nil && endpoint.Ingress.Enabled {
		if !r.IngressAvailable {
			return fmt.Errorf("telemetry endpoint %q enables ingress, but the Ingress API is not available in the cluster", endpoint.Address)
		}
		if err := r.createIngressForEndpoint(ctx, owner, servicePort.Name, servicePort.Port, endpoint, labels); err != nil {
			return err
		}
	}
	if endpoint.Route != nil && endpoint.Route.Enabled {
		if !r.RouteAvailable {
			return fmt.Errorf("telemetry endpoint %q enables route, but the Route API is not available in the cluster", endpoint.Address)
		}
		if err := r.createRouteForEndpoint(ctx, owner, servicePort.Name, servicePort.Port, endpoint, labels); err != nil {
			return err
		}
	}
	if endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled {
		lbPort := servicePort
		if endpoint.LoadBalancer.Port > 0 {
			lbPort.Port = endpoint.LoadBalancer.Port
		} else if _, port, err := net.SplitHostPort(endpoint.Address); err == nil {
			// The advertised endpoint is ensurePort(address, "443"): expose the
			// same port here or clients would dial a closed port.
			p, err := strconv.Atoi(port)
			if err != nil || p < 1 || p > 65535 {
				return fmt.Errorf("telemetry endpoint %q requires a numeric port between 1 and 65535", endpoint.Address)
			}
			lbPort.Port = int32(p)
		} else {
			lbPort.Port = 443
		}
		if err := r.createService(ctx, owner, lbPort, "-lb", corev1.ServiceTypeLoadBalancer,
			labels, labels, endpoint.LoadBalancer.Annotations, endpoint.LoadBalancer.Labels); err != nil {
			return err
		}
	}
	if endpoint.NodePort != nil && endpoint.NodePort.Enabled {
		nodePort := servicePort
		nodePort.NodePort = endpoint.NodePort.Port
		if err := r.createService(ctx, owner, nodePort, "-np", corev1.ServiceTypeNodePort,
			labels, labels, endpoint.NodePort.Annotations, endpoint.NodePort.Labels); err != nil {
			return err
		}
	}
	if needsClusterIPService(endpoint) {
		var annotations, extraLabels map[string]string
		if endpoint.ClusterIP != nil {
			annotations, extraLabels = endpoint.ClusterIP.Annotations, endpoint.ClusterIP.Labels
		}
		if err := r.createService(ctx, owner, servicePort, "", corev1.ServiceTypeClusterIP,
			labels, labels, annotations, extraLabels); err != nil {
			return err
		}
	}
	return nil
}
