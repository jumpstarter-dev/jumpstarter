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

// Package qemu implements the qemu.jumpstarter.dev provisioner
// for ExporterSets. It renders Pods using the sidecar pattern:
// a native sidecar init container running the Jumpstarter exporter
// alongside a main container running the QEMU runtime.
package qemu

import (
	"context"
	"fmt"
	"maps"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	// ProvisionerName is the provisioner identifier for
	// QEMU-based virtual targets.
	ProvisionerName = "qemu.jumpstarter.dev"

	// DefaultExporterImage is the exporter sidecar image.
	DefaultExporterImage = "quay.io/jumpstarter-dev/jumpstarter:latest"

	// DefaultQEMURuntimeImage is the QEMU runtime container image.
	DefaultQEMURuntimeImage = "quay.io/jumpstarter-dev/virtual/qemu-runtime:latest"

	// sharedVolumeName is the name of the shared emptyDir volume
	// used for Unix socket communication between the exporter
	// sidecar and the QEMU runtime (QMP, serial, launcher).
	sharedVolumeName = "shared"
	sharedMountPath  = "/shared"

	// sharedVolumeSizeLimit caps emptyDir usage so a misbehaving
	// container cannot exhaust node ephemeral storage.
	sharedVolumeSizeLimit = "100Mi"

	// jmpExecBinaryPath is the location of jumpstarter-exec inside
	// the exporter image (installed by the Rust builder stage).
	jmpExecBinaryPath = "/jumpstarter/bin/jumpstarter-exec"

	// launcherSocketPath is the Unix socket used by jumpstarter-exec
	// for remote command execution between the exporter and
	// the QEMU runtime container.
	launcherSocketPath = "/shared/launcher.sock"
)

// Provisioner implements the qemu.jumpstarter.dev provisioner.
// It renders Pods with a QEMU runtime container and an exporter
// sidecar, communicating via Unix sockets on a shared emptyDir
// volume.
type Provisioner struct{}

// New creates a new QEMU provisioner.
func New() *Provisioner {
	return &Provisioner{}
}

// Name returns the provisioner identifier.
func (p *Provisioner) Name() string {
	return ProvisionerName
}

// RenderPod creates a Pod for a new QEMU-based exporter instance
// using the native sidecar pattern (KEP-753):
//
//   - Exporter sidecar (init container with restartPolicy: Always)
//     starts first and drains last; registers with the controller.
//   - QEMU runtime (main container) runs the virtual machine.
//   - Shared emptyDir volume for Unix socket communication
//     (QMP, serial console, launcher socket).
//
// The caller (reconciler) is responsible for setting
// OwnerReferences on the Pod to ensure garbage collection when
// the ExporterSet is deleted.
//
// TODO: Full implementation will:
//   - Configure QEMU runtime from merged parameters
//     (CPU, memory, firmware, machine type, etc.)
//   - Set up exporter with driver config from ExporterSet
//     template
//   - Mount firmware/OS images as OCI volume sources
//   - Inventory shared interfaces (serial, USB, CAN,
//     network) and map them to QEMU device models plus
//     any required container capabilities/privileges
func (p *Provisioner) RenderPod(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	mergedParameters map[string]interface{},
	exporter *jumpstarterdevv1alpha1.Exporter,
) (*corev1.Pod, error) {
	restartAlways := corev1.ContainerRestartPolicyAlways
	sizeLimit := resource.MustParse(sharedVolumeSizeLimit)

	// JEP-0013 persistent log context for jumpstarter-exec (matches
	// set_persistent_log_context in the Python exporter).
	runtimeEnv := []corev1.EnvVar{}
	if exporter != nil {
		runtimeEnv = append(runtimeEnv, corev1.EnvVar{
			Name: "JUMPSTARTER_EXEC_LOG_FIELDS",
			Value: fmt.Sprintf(
				"component=exporter,exporter=%s,namespace=%s",
				exporter.Name, exporter.Namespace,
			),
		})
	}

	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: fmt.Sprintf("%s-", exporterSet.Name),
			Namespace:    exporterSet.Namespace,
			Labels:       maps.Clone(exporterSet.Spec.Template.Metadata.Labels),
			Annotations:  maps.Clone(exporterSet.Spec.Template.Metadata.Annotations),
		},
		Spec: corev1.PodSpec{
			// Exporter runs as a native sidecar init container
			// (KEP-753): starts before main containers and
			// drains after them.
			InitContainers: []corev1.Container{
				{
					Name:            "exporter",
					Image:           DefaultExporterImage,
					ImagePullPolicy: corev1.PullIfNotPresent,
					RestartPolicy:   &restartAlways,
					Command:         []string{"sh", "-c"},
					Args: []string{
						fmt.Sprintf(
							"cp %s %s/jumpstarter-exec && exec sleep infinity",
							jmpExecBinaryPath, sharedMountPath,
						),
					},
					Env: []corev1.EnvVar{
						{
							Name:  "JUMPSTARTER_LAUNCHER_SOCKET",
							Value: launcherSocketPath,
						},
					},
					VolumeMounts: []corev1.VolumeMount{
						{
							Name:      sharedVolumeName,
							MountPath: sharedMountPath,
						},
					},
					// TODO: replace "sleep infinity" with the actual
					// exporter startup command once driver config,
					// controller endpoint, and credentials injection
					// are implemented.
				},
			},
			// QEMU runtime is the main container — independent
			// image that can be versioned separately.
			Containers: []corev1.Container{
				{
					Name:            "target-runtime",
					Image:           DefaultQEMURuntimeImage,
					ImagePullPolicy: corev1.PullIfNotPresent,
					Env:             runtimeEnv,
					VolumeMounts: []corev1.VolumeMount{
						{
							Name:      sharedVolumeName,
							MountPath: sharedMountPath,
						},
					},
					// Command/Args are left unset so the container
					// uses its Containerfile ENTRYPOINT/CMD, which
					// waits for /shared/jumpstarter-exec then runs it in
					// serve mode on the launcher socket.
					//
					// TODO: configure QEMU from merged
					// parameters (CPU, memory, firmware, etc.)
				},
			},
			Volumes: []corev1.Volume{
				{
					Name: sharedVolumeName,
					VolumeSource: corev1.VolumeSource{
						EmptyDir: &corev1.EmptyDirVolumeSource{
							SizeLimit: &sizeLimit,
						},
					},
				},
			},
		},
	}

	// Apply scheduling from VirtualTargetClass.
	// Clone maps and slices to avoid mutating the VTC's fields.
	if vtc.Spec.Scheduling != nil {
		if vtc.Spec.Scheduling.NodeSelector != nil {
			pod.Spec.NodeSelector = maps.Clone(vtc.Spec.Scheduling.NodeSelector)
		}
		if vtc.Spec.Scheduling.Tolerations != nil {
			pod.Spec.Tolerations = append([]corev1.Toleration(nil), vtc.Spec.Scheduling.Tolerations...)
		}
		if vtc.Spec.Scheduling.Resources != nil {
			// Apply resource requirements to target-runtime
			pod.Spec.Containers[0].Resources = *vtc.Spec.Scheduling.Resources.DeepCopy()
		}
	}

	return pod, nil
}

// Cleanup handles teardown of QEMU-based exporter instances.
// For in-cluster QEMU, this is a no-op since deleting the Pod
// (via OwnerReference cascade) handles cleanup.
func (p *Provisioner) Cleanup(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	return nil
}
