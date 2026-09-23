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
)

// Deployer is an optional interface that off-cluster provisioners
// implement to manage exporter instances outside the Kubernetes
// cluster. When a Provisioner also implements Deployer, the
// reconciler calls Deploy/IsDeployed instead of creating Pods.
//
// In-cluster provisioners (e.g. qemu.jumpstarter.dev) do not
// implement this interface — they use RenderPod and standard Pod
// lifecycle.
type Deployer interface {
	// Deploy sets up the exporter on the remote host. Called after
	// the Exporter CR has credentials (endpoint + token) and the
	// config Secret has been synced.
	Deploy(
		ctx context.Context,
		es *virtualtargetv1alpha1.ExporterSet,
		vtc *virtualtargetv1alpha1.VirtualTargetClass,
		mergedParameters map[string]any,
		images *virtualtargetv1alpha1.ImageOverrides,
		exporter *jumpstarterdevv1alpha1.Exporter,
		caBundle string,
	) error

	// IsDeployed reports whether the exporter instance is already
	// running on its assigned remote host.
	IsDeployed(
		ctx context.Context,
		exporter *jumpstarterdevv1alpha1.Exporter,
	) (bool, error)
}
