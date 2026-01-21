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
	"fmt"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

// ensureEndpointServiceType ensures an endpoint has a service type enabled.
// If no service type is enabled, it auto-selects Route (if available), Ingress (if available),
// or ClusterIP as a fallback.
func ensureEndpointServiceType(endpoint *operatorv1alpha1.Endpoint, routeAvailable, ingressAvailable bool) {
	// Skip if any service type is already enabled
	if (endpoint.Route != nil && endpoint.Route.Enabled) ||
		(endpoint.Ingress != nil && endpoint.Ingress.Enabled) ||
		(endpoint.LoadBalancer != nil && endpoint.LoadBalancer.Enabled) ||
		(endpoint.NodePort != nil && endpoint.NodePort.Enabled) ||
		(endpoint.ClusterIP != nil && endpoint.ClusterIP.Enabled) {
		return
	}

	// Auto-select based on cluster capabilities, fallback to ClusterIP
	if routeAvailable {
		endpoint.Route = &operatorv1alpha1.RouteConfig{Enabled: true}
	} else if ingressAvailable {
		endpoint.Ingress = &operatorv1alpha1.IngressConfig{Enabled: true}
	} else {
		endpoint.ClusterIP = &operatorv1alpha1.ClusterIPConfig{Enabled: true}
	}
}

// ApplyEndpointDefaults generates default endpoints for a JumpstarterSpec
// based on the baseDomain and cluster capabilities (Route vs Ingress availability).
// It also ensures all existing endpoints have a service type enabled.
func ApplyEndpointDefaults(spec *operatorv1alpha1.JumpstarterSpec, routeAvailable, ingressAvailable bool) {
	// Skip endpoint generation if no baseDomain is set
	if spec.BaseDomain == "" {
		return
	}

	// Generate default controller gRPC endpoint if none specified
	if len(spec.Controller.GRPC.Endpoints) == 0 {
		endpoint := operatorv1alpha1.Endpoint{
			Address: fmt.Sprintf("grpc.%s", spec.BaseDomain),
		}
		ensureEndpointServiceType(&endpoint, routeAvailable, ingressAvailable)
		spec.Controller.GRPC.Endpoints = []operatorv1alpha1.Endpoint{endpoint}
	} else {
		// Ensure existing endpoints have a service type enabled
		for i := range spec.Controller.GRPC.Endpoints {
			ensureEndpointServiceType(&spec.Controller.GRPC.Endpoints[i], routeAvailable, ingressAvailable)
		}
	}

	// Generate default router gRPC endpoints if none specified
	if len(spec.Routers.GRPC.Endpoints) == 0 {
		endpoint := operatorv1alpha1.Endpoint{
			// Use $(replica) placeholder for per-replica addresses
			Address: fmt.Sprintf("router-$(replica).%s", spec.BaseDomain),
		}
		ensureEndpointServiceType(&endpoint, routeAvailable, ingressAvailable)
		spec.Routers.GRPC.Endpoints = []operatorv1alpha1.Endpoint{endpoint}
	} else {
		// Ensure existing endpoints have a service type enabled
		for i := range spec.Routers.GRPC.Endpoints {
			ensureEndpointServiceType(&spec.Routers.GRPC.Endpoints[i], routeAvailable, ingressAvailable)
		}
	}
}
