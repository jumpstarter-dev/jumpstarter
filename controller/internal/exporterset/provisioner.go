/*
Copyright 2024.

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

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
)

// Provisioner defines the interface that each backend provisioner must implement.
// The ExporterSet reconciler delegates provisioner-specific logic through this interface,
// keeping scaling orchestration generic while allowing each backend to render Pods,
// manage external resources, and handle cleanup differently.
type Provisioner interface {
	// Name returns the provisioner identifier (e.g. "qemu.jumpstarter.dev").
	Name() string

	// RenderPod creates a Pod spec for a new exporter instance.
	// The reconciler provides the ExporterSet, the resolved VirtualTargetClass,
	// and the deep-merged parameters. The provisioner returns a Pod ready to create.
	RenderPod(
		ctx context.Context,
		exporterSet *jumpstarterdevv1alpha1.ExporterSet,
		vtc *jumpstarterdevv1alpha1.VirtualTargetClass,
		mergedParameters map[string]interface{},
	) (*corev1.Pod, error)

	// Cleanup is called when an exporter instance is being removed.
	// The provisioner can use this to clean up external resources (API instances, etc.).
	Cleanup(
		ctx context.Context,
		exporterSet *jumpstarterdevv1alpha1.ExporterSet,
		exporter *jumpstarterdevv1alpha1.Exporter,
	) error
}
