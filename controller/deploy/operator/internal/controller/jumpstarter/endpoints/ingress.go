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
	"strings"

	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/validation"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/utils"
)

// createOrUpdateIngress creates or updates an ingress with proper handling of mutable fields
// and owner references. This follows the same pattern as createOrUpdateService.
func (r *Reconciler) createOrUpdateIngress(ctx context.Context, ingress *networkingv1.Ingress, owner metav1.Object) error {
	log := logf.FromContext(ctx)

	existingIngress := &networkingv1.Ingress{}
	existingIngress.Name = ingress.Name
	existingIngress.Namespace = ingress.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existingIngress, func() error {
		// Update all mutable fields
		existingIngress.Labels = ingress.Labels
		existingIngress.Annotations = ingress.Annotations
		existingIngress.Spec.IngressClassName = ingress.Spec.IngressClassName
		existingIngress.Spec.Rules = ingress.Spec.Rules
		existingIngress.Spec.TLS = ingress.Spec.TLS

		return controllerutil.SetControllerReference(owner, existingIngress, r.Scheme)
	})

	if err != nil {
		log.Error(err, "Failed to reconcile ingress",
			"name", ingress.Name,
			"namespace", ingress.Namespace)
		return err
	}

	log.Info("Ingress reconciled",
		"name", ingress.Name,
		"namespace", ingress.Namespace,
		"operation", op)

	return nil
}

// extractHostname extracts the hostname from an endpoint address.
// It handles formats like: "hostname", "hostname:port", "IPv4:port", "[IPv6]", "[IPv6]:port"
func extractHostname(address string) string {
	// Handle IPv6 addresses in brackets
	if strings.HasPrefix(address, "[") {
		// Find the closing bracket
		if idx := strings.Index(address, "]"); idx != -1 {
			return address[1:idx]
		}
		return address
	}

	// For hostname or IPv4, strip port if present
	if idx := strings.LastIndex(address, ":"); idx != -1 {
		// Check if this is part of an IPv6 address (no brackets)
		// Count colons - if more than one, likely IPv6
		if strings.Count(address, ":") > 1 {
			return address
		}
		return address[:idx]
	}

	return address
}

// createIngressForEndpoint creates an ingress for a specific endpoint.
// The ingress points to the ClusterIP service (serviceName with no suffix).
func (r *Reconciler) createIngressForEndpoint(ctx context.Context, owner metav1.Object, serviceName string, servicePort int32,
	endpoint *operatorv1alpha1.Endpoint, baseLabels map[string]string) error {

	log := logf.FromContext(ctx)

	// Check if Ingress API is available in the cluster
	if !r.IngressAvailable {
		log.Info("Skipping ingress creation: Ingress API not available in cluster")
		// TODO: update status of the jumpstarter object to indicate that the ingress is not available
		return nil
	}

	// Extract hostname from address
	hostname := extractHostname(endpoint.Address)
	if hostname == "" {
		log.Info("Skipping ingress creation: no hostname in endpoint address",
			"address", endpoint.Address)
		return nil
	}

	if errs := validation.IsDNS1123Subdomain(hostname); errs != nil {
		log := logf.FromContext(ctx)
		log.Error(errors.New(strings.Join(errs, ", ")), "Skipping ingress creation: invalid hostname",
			"address", endpoint.Address,
			"hostname", hostname)
		// TODO: propagate error to status conditions
		return nil
	}

	// Build default annotations for TLS passthrough with GRPC with nginx ingress
	defaultAnnotations := map[string]string{
		"nginx.ingress.kubernetes.io/ssl-redirect":       "true",
		"nginx.ingress.kubernetes.io/backend-protocol":   "GRPC",
		"nginx.ingress.kubernetes.io/proxy-read-timeout": "300",
		"nginx.ingress.kubernetes.io/proxy-send-timeout": "300",
		"nginx.ingress.kubernetes.io/ssl-passthrough":    "true",
	}

	// Merge with user-provided annotations (user annotations take precedence)
	annotations := utils.MergeMaps(defaultAnnotations, endpoint.Ingress.Annotations)

	// Merge labels (user labels take precedence)
	ingressLabels := utils.MergeMaps(baseLabels, endpoint.Ingress.Labels)

	// Set ingress class name (only if specified, cannot be empty string)
	var ingressClassName *string
	if endpoint.Ingress.Class != "" {
		ingressClassName = &endpoint.Ingress.Class
	}

	// Build path type
	pathTypePrefix := networkingv1.PathTypePrefix

	ingress := &networkingv1.Ingress{
		ObjectMeta: metav1.ObjectMeta{
			Name:        serviceName + "-ing",
			Namespace:   owner.GetNamespace(),
			Labels:      ingressLabels,
			Annotations: annotations,
		},
		Spec: networkingv1.IngressSpec{
			IngressClassName: ingressClassName,
			Rules: []networkingv1.IngressRule{
				{
					Host: hostname,
					IngressRuleValue: networkingv1.IngressRuleValue{
						HTTP: &networkingv1.HTTPIngressRuleValue{
							Paths: []networkingv1.HTTPIngressPath{
								{
									Path:     "/",
									PathType: &pathTypePrefix,
									Backend: networkingv1.IngressBackend{
										Service: &networkingv1.IngressServiceBackend{
											Name: serviceName,
											Port: networkingv1.ServiceBackendPort{
												Number: servicePort,
											},
										},
									},
								},
							},
						},
					},
				},
			},
			TLS: []networkingv1.IngressTLS{
				{
					Hosts: []string{hostname},
					// No SecretName - passthrough mode handles TLS at the backend
				},
			},
		},
	}

	return r.createOrUpdateIngress(ctx, ingress, owner)
}
