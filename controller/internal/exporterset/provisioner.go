/*
Copyright 2026 The Jumpstarter Authors

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

package exporterset

import (
	"context"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
)

// Provisioner defines the interface that each backend provisioner
// must implement. The ExporterSet reconciler delegates
// provisioner-specific logic through this interface, keeping
// scaling orchestration generic while allowing each backend to
// render Pods, manage external resources, and handle cleanup
// differently.
//
// Future extensions may add methods for rendering supporting
// resources that provisioned Pods depend on:
//   - RenderSecrets: create Secrets (e.g. API credentials,
//     TLS certs) injected into exporter or runtime containers.
//   - RenderConfigMaps: create ConfigMaps (e.g. QEMU machine
//     profiles, driver configuration) mounted into Pods.
//
// These are not part of the initial interface because the
// reconciler can be extended to call them when needed without
// breaking existing provisioner implementations.
type Provisioner interface {
	// Name returns the provisioner identifier
	// (e.g. "qemu.jumpstarter.dev").
	Name() string

	// RenderPod creates a Pod spec for a new exporter instance.
	// The reconciler provides the ExporterSet, the resolved
	// VirtualTargetClass, the deep-merged parameters, and the
	// Exporter CR that owns this instance. The provisioner returns
	// a Pod ready to create. The reconciler sets OwnerReferences
	// on the Pod before creation.
	RenderPod(
		ctx context.Context,
		exporterSet *virtualtargetv1alpha1.ExporterSet,
		vtc *virtualtargetv1alpha1.VirtualTargetClass,
		mergedParameters map[string]interface{},
		exporter *jumpstarterdevv1alpha1.Exporter,
	) (*corev1.Pod, error)

	// Cleanup is called when an exporter instance is being
	// removed. The provisioner can use this to clean up external
	// resources (API-backed instances, off-cluster processes,
	// etc.). For in-cluster Pods, OwnerReference cascade handles
	// deletion automatically.
	Cleanup(
		ctx context.Context,
		exporterSet *virtualtargetv1alpha1.ExporterSet,
		exporter *jumpstarterdevv1alpha1.Exporter,
	) error
}
