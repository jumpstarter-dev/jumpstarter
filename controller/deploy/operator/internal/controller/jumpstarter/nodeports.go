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

package jumpstarter

import (
	"context"
	"fmt"
	"strings"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

// desiredNodePorts validates and collects explicit and automatically allocated
// NodePorts across components before any existing Services are changed.
func (r *JumpstarterReconciler) desiredNodePorts(js *operatorv1alpha1.Jumpstarter) (map[string]int32, error) {
	wanted := make(map[string]int32)
	ports := make(map[int32]string)
	add := func(name string, endpoint operatorv1alpha1.Endpoint, replica int32) error {
		if endpoint.NodePort == nil || !endpoint.NodePort.Enabled {
			return nil
		}
		name += "-np"
		if _, exists := wanted[name]; exists {
			return fmt.Errorf("service %s supports only one NodePort endpoint", name)
		}
		port := int64(endpoint.NodePort.Port)
		if port != 0 {
			port += int64(replica)
			if port < 1 || port > 65535 {
				return fmt.Errorf("service %s requires a NodePort between 1 and 65535", name)
			}
			if other, exists := ports[int32(port)]; exists {
				return fmt.Errorf("services %s and %s request the same NodePort %d", other, name, port)
			}
			ports[int32(port)] = name
		}
		wanted[name] = int32(port)
		return nil
	}
	for _, endpoint := range js.Spec.Controller.GRPC.Endpoints {
		if err := add("controller-grpc", endpoint, 0); err != nil {
			return nil, err
		}
	}
	for _, endpoint := range js.Spec.Controller.Login.Endpoints {
		if err := add("login", endpoint, 0); err != nil {
			return nil, err
		}
	}
	replicas := routerReplicas(&js.Spec.Routers)
	for replica := range replicas {
		for i, endpoint := range js.Spec.Routers.GRPC.Endpoints {
			if err := add(r.buildServiceNameForReplicaEndpoint(js, replica, i), endpoint, replica); err != nil {
				return nil, err
			}
		}
	}
	if js.Spec.Telemetry != nil && js.Spec.Telemetry.Enabled {
		for i, endpoint := range js.Spec.Telemetry.GRPC.Endpoints {
			if err := add(fmt.Sprintf("%s-grpc-%d", telemetryServiceName, i), endpoint, 0); err != nil {
				return nil, err
			}
		}
	}
	return wanted, nil
}

// prepareNodePorts releases obsolete and changing allocations before any Service
// is reconciled, including swaps between different components. A zero desired
// port preserves automatic allocation. Suspended routers retain their Services.
func (r *JumpstarterReconciler) prepareNodePorts(ctx context.Context, js *operatorv1alpha1.Jumpstarter) error {
	wanted, err := r.desiredNodePorts(js)
	if err != nil {
		return err
	}
	services := &corev1.ServiceList{}
	if err := r.List(ctx, services, client.InNamespace(js.Namespace)); err != nil {
		return err
	}
	for i := range services.Items {
		svc := &services.Items[i]
		if !metav1.IsControlledBy(svc, js) || !strings.HasSuffix(svc.Name, "-np") {
			continue
		}
		isRouter := strings.HasPrefix(svc.Name, routerServiceNamePrefix(js))
		if isRouter && routerReplicas(&js.Spec.Routers) == 0 {
			continue
		}
		if !isRouter && svc.Name != "controller-grpc-np" && svc.Name != "login-np" &&
			!strings.HasPrefix(svc.Name, telemetryServiceName+"-grpc-") {
			continue
		}
		port, keep := wanted[svc.Name]
		if keep && (port == 0 || (len(svc.Spec.Ports) == 1 && svc.Spec.Ports[0].NodePort == port)) {
			continue
		}
		// ponytail: recreate changing Services; stable endpoint identities if uninterrupted updates are needed.
		if err := r.Delete(ctx, svc, client.Preconditions{UID: &svc.UID}); client.IgnoreNotFound(err) != nil {
			return fmt.Errorf("failed to release NodePort service %s: %w", svc.Name, err)
		}
	}
	return nil
}
