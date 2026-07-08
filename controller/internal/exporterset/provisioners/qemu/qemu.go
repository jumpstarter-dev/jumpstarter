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

// Package qemu implements the qemu.jumpstarter.dev provisioner for ExporterSets.
// It renders Pods using the sidecar pattern: a native sidecar init container running
// the Jumpstarter exporter alongside a main container running the QEMU runtime.
package qemu

import (
	"context"
	"fmt"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	// ProvisionerName is the provisioner identifier for QEMU-based virtual targets.
	ProvisionerName = "qemu.jumpstarter.dev"
)

// Provisioner implements the qemu.jumpstarter.dev provisioner.
// It renders Pods with a QEMU runtime container and an exporter sidecar,
// communicating via Unix sockets on a shared emptyDir volume.
type Provisioner struct{}

// New creates a new QEMU provisioner.
func New() *Provisioner {
	return &Provisioner{}
}

// Name returns the provisioner identifier.
func (p *Provisioner) Name() string {
	return ProvisionerName
}

// RenderPod creates a Pod spec for a new QEMU-based exporter instance.
// The Pod uses the sidecar pattern:
//   - Exporter sidecar (native sidecar init container, restartPolicy: Always)
//   - QEMU runtime container (main container)
//   - Shared emptyDir volume for Unix socket communication (QMP, serial, launcher)
//
// TODO: This is a stub. Full implementation will:
//   - Apply scheduling constraints from VirtualTargetClass
//   - Configure QEMU runtime from merged parameters (CPU, memory, firmware, etc.)
//   - Set up exporter sidecar with driver configuration from ExporterSet template
//   - Mount firmware/OS images as OCI volume sources
func (p *Provisioner) RenderPod(
	ctx context.Context,
	exporterSet *jumpstarterdevv1alpha1.ExporterSet,
	vtc *jumpstarterdevv1alpha1.VirtualTargetClass,
	mergedParameters map[string]interface{},
) (*corev1.Pod, error) {
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: fmt.Sprintf("%s-", exporterSet.Name),
			Namespace:    exporterSet.Namespace,
			Labels:       exporterSet.Spec.Template.Labels,
		},
		Spec: corev1.PodSpec{
			// TODO: populate from VirtualTargetClass.scheduling
			// TODO: render exporter sidecar + QEMU runtime containers
			// TODO: add shared emptyDir volume
			Containers: []corev1.Container{
				{
					Name:  "target-runtime",
					Image: "quay.io/jumpstarter-dev/qemu-runtime:latest", // placeholder
				},
			},
		},
	}

	// Apply scheduling from VirtualTargetClass
	if vtc.Spec.Scheduling != nil {
		if vtc.Spec.Scheduling.NodeSelector != nil {
			pod.Spec.NodeSelector = vtc.Spec.Scheduling.NodeSelector
		}
		if vtc.Spec.Scheduling.Tolerations != nil {
			pod.Spec.Tolerations = vtc.Spec.Scheduling.Tolerations
		}
	}

	return pod, nil
}

// Cleanup handles teardown of QEMU-based exporter instances.
// For in-cluster QEMU, this is a no-op since deleting the Pod handles cleanup.
// Future: clean up any external state if needed.
func (p *Provisioner) Cleanup(
	ctx context.Context,
	exporterSet *jumpstarterdevv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	return nil
}
