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

	routev1 "github.com/openshift/api/route/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/apimachinery/pkg/util/validation"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/internal/utils"
)

// createOrUpdateRoute creates or updates a route with proper handling of mutable fields
// and owner references. This follows the same pattern as createOrUpdateService and createOrUpdateIngress.
func (r *Reconciler) createOrUpdateRoute(ctx context.Context, route *routev1.Route, owner metav1.Object) error {
	log := logf.FromContext(ctx)

	existingRoute := &routev1.Route{}
	existingRoute.Name = route.Name
	existingRoute.Namespace = route.Namespace

	op, err := controllerutil.CreateOrUpdate(ctx, r.Client, existingRoute, func() error {
		// Update all mutable fields
		existingRoute.Labels = route.Labels
		existingRoute.Annotations = route.Annotations
		existingRoute.Spec.Host = route.Spec.Host
		existingRoute.Spec.Path = route.Spec.Path
		existingRoute.Spec.Port = route.Spec.Port
		existingRoute.Spec.TLS = route.Spec.TLS
		existingRoute.Spec.To = route.Spec.To
		existingRoute.Spec.WildcardPolicy = route.Spec.WildcardPolicy

		return controllerutil.SetControllerReference(owner, existingRoute, r.Scheme)
	})

	if err != nil {
		log.Error(err, "Failed to reconcile route",
			"name", route.Name,
			"namespace", route.Namespace)
		return err
	}

	log.Info("Route reconciled",
		"name", route.Name,
		"namespace", route.Namespace,
		"operation", op)

	return nil
}

// createRouteForEndpoint creates an OpenShift Route for a specific endpoint.
// The route points to the ClusterIP service (serviceName with no suffix).
func (r *Reconciler) createRouteForEndpoint(ctx context.Context, owner metav1.Object, serviceName string, servicePort int32,
	endpoint *operatorv1alpha1.Endpoint, baseLabels map[string]string) error {

	log := logf.FromContext(ctx)

	// Check if Route API is available in the cluster
	if !r.RouteAvailable {
		log.Info("Skipping route creation: Route API not available in cluster")
		// TODO: update status of the jumpstarter object to indicate that the route is not available
		return nil
	}

	// Extract hostname from address
	hostname := extractHostname(endpoint.Address)
	if hostname == "" {
		log.Info("Skipping route creation: no hostname in endpoint address",
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

	// Build default annotations for OpenShift HAProxy router with longer timeouts for gRPC
	defaultAnnotations := map[string]string{
		"haproxy.router.openshift.io/timeout":        "2d",
		"haproxy.router.openshift.io/timeout-tunnel": "2d",
	}

	// Merge with user-provided annotations (user annotations take precedence)
	annotations := utils.MergeMaps(defaultAnnotations, endpoint.Route.Annotations)

	// Merge labels (user labels take precedence)
	routeLabels := utils.MergeMaps(baseLabels, endpoint.Route.Labels)

	// Use passthrough TLS termination (TLS is handled by the backend service)
	// This is consistent with the Ingress configuration which uses ssl-passthrough
	tlsTermination := routev1.TLSTerminationPassthrough

	route := &routev1.Route{
		ObjectMeta: metav1.ObjectMeta{
			Name:        serviceName + "-route",
			Namespace:   owner.GetNamespace(),
			Labels:      routeLabels,
			Annotations: annotations,
		},
		Spec: routev1.RouteSpec{
			Host: hostname,
			Port: &routev1.RoutePort{
				TargetPort: intstr.FromInt(int(servicePort)),
			},
			To: routev1.RouteTargetReference{
				Kind:   "Service",
				Name:   serviceName,
				Weight: ptr.To(int32(100)),
			},
			TLS: &routev1.TLSConfig{
				Termination:                   tlsTermination,
				InsecureEdgeTerminationPolicy: routev1.InsecureEdgeTerminationPolicyNone,
			},
			WildcardPolicy: routev1.WildcardPolicyNone,
		},
	}

	return r.createOrUpdateRoute(ctx, route, owner)
}
