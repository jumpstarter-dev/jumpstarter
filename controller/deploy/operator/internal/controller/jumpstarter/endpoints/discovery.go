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
	"k8s.io/client-go/discovery"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// discoverIngressAPI checks if the Ingress API (networking.k8s.io/v1) is available in the cluster
func discoverIngressAPI(config *rest.Config) bool {
	discoveryClient, err := discovery.NewDiscoveryClientForConfig(config)
	if err != nil {
		log.Log.Error(err, "Failed to create discovery client for Ingress API check")
		return false
	}

	apiResourceList, err := discoveryClient.ServerResourcesForGroupVersion("networking.k8s.io/v1")
	if err != nil {
		// API group not found - Ingress not available
		return false
	}

	for _, resource := range apiResourceList.APIResources {
		if resource.Kind == "Ingress" {
			return true
		}
	}

	return false
}

// discoverRouteAPI checks if the OpenShift Route API (route.openshift.io/v1) is available in the cluster
func discoverRouteAPI(config *rest.Config) bool {
	discoveryClient, err := discovery.NewDiscoveryClientForConfig(config)
	if err != nil {
		log.Log.Error(err, "Failed to create discovery client for Route API check")
		return false
	}

	apiResourceList, err := discoveryClient.ServerResourcesForGroupVersion("route.openshift.io/v1")
	if err != nil {
		// API group not found - Route not available
		return false
	}

	for _, resource := range apiResourceList.APIResources {
		if resource.Kind == "Route" {
			return true
		}
	}

	return false
}
