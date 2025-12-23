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

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/discovery"
	"k8s.io/client-go/dynamic"
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

// detectOpenShiftBaseDomain attempts to detect the cluster's base domain from OpenShift's
// ingresses.config.openshift.io/cluster resource. Returns empty string if not available.
func detectOpenShiftBaseDomain(config *rest.Config) string {
	logger := log.Log.WithName("basedomain-detection")

	// Create dynamic client for unstructured access to OpenShift config API
	dynamicClient, err := dynamic.NewForConfig(config)
	if err != nil {
		logger.Error(err, "Failed to create dynamic client for baseDomain detection")
		return ""
	}

	// Define the GVR for ingresses.config.openshift.io
	ingressGVR := schema.GroupVersionResource{
		Group:    "config.openshift.io",
		Version:  "v1",
		Resource: "ingresses",
	}

	// Get the cluster-scoped "cluster" ingress config
	ingressConfig, err := dynamicClient.Resource(ingressGVR).Get(context.Background(), "cluster", metav1.GetOptions{})
	if err != nil {
		// This is expected on non-OpenShift clusters, log at debug level
		logger.V(1).Info("Could not fetch OpenShift ingress config (expected on non-OpenShift clusters)", "error", err.Error())
		return ""
	}

	// Extract spec.domain from the unstructured object
	domain, found, err := unstructured.NestedString(ingressConfig.Object, "spec", "domain")
	if err != nil || !found || domain == "" {
		logger.Info("OpenShift ingress config found but spec.domain not available")
		return ""
	}

	logger.Info("Auto-detected OpenShift cluster domain", "domain", domain)
	return domain
}
