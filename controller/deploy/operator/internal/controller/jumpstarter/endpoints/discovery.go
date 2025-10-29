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

// discoverAPIResource checks if a specific API resource is available in the cluster
// groupVersion should be in the format "group/version" (e.g., "networking.k8s.io/v1", "route.openshift.io/v1")
// kind is the resource kind to look for (e.g., "Ingress", "Route")
func discoverAPIResource(config *rest.Config, groupVersion, kind string) bool {
	discoveryClient, err := discovery.NewDiscoveryClientForConfig(config)
	if err != nil {
		log.Log.Error(err, "Failed to create discovery client",
			"groupVersion", groupVersion,
			"kind", kind)
		return false
	}

	apiResourceList, err := discoveryClient.ServerResourcesForGroupVersion(groupVersion)
	if err != nil {
		// API group not found - resource not available
		return false
	}

	for _, resource := range apiResourceList.APIResources {
		if resource.Kind == kind {
			return true
		}
	}

	return false
}
