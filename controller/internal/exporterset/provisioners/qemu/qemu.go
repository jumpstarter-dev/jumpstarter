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
// a one-shot init container that stages jumpstarter-exec onto a
// shared volume, a native sidecar init container running the
// Jumpstarter exporter, and a main container running the QEMU runtime.
package qemu

import (
	"context"
	"fmt"
	"maps"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/exporterset/disk"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/exporterset/provisioners/qemucommon"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	// ProvisionerName is the provisioner identifier for
	// QEMU-based virtual targets.
	ProvisionerName = "qemu.jumpstarter.dev"

	// sharedVolumeName is the name of the shared emptyDir volume
	// used for Unix socket communication between the exporter
	// sidecar and the QEMU runtime (QMP, serial, launcher).
	sharedVolumeName = "shared"
	sharedMountPath  = "/shared"

	// sharedVolumeSizeLimit caps emptyDir usage so a misbehaving
	// container cannot exhaust node ephemeral storage.
	sharedVolumeSizeLimit = "100Mi"

	// runtimeContainerName is the native sidecar that runs jumpstarter-exec /
	// QEMU. Kept as a const so scheduling and RenderPod stay in sync.
	runtimeContainerName = "target-runtime"

	// jmpExecBinaryPath is the location of jumpstarter-exec inside
	// the exporter image (installed by the Rust builder stage).
	jmpExecBinaryPath = "/jumpstarter/bin/jumpstarter-exec"

	// exporterNonRootUID is the UID for the exporter main container.
	// The runtime sidecar runs as root so it can read exporter-created
	// paths on the shared volume without world-writable permissions.
	exporterNonRootUID int64 = 65532

	// exporterConfigPath is the jmp run config path. Must match
	// exporterset.ExporterConfigMountPath + "/" + exporterConfigKey
	// (cannot import the parent package — test import cycle).
	exporterConfigPath = "/etc/jumpstarter/exporters/config.yaml"
)

// Provisioner implements the qemu.jumpstarter.dev provisioner.
// It renders Pods with a QEMU runtime container and an exporter
// sidecar, staging jumpstarter-exec via a one-shot init container
// and communicating via Unix sockets on a shared emptyDir volume.
type Provisioner struct {
	// Version is the build-time version string (e.g. "v0.9.0", "dev").
	// Used to resolve :latest image tags to the correct version.
	Version string
}

// New creates a new QEMU provisioner with the given build-time version.
func New(version string) *Provisioner {
	return &Provisioner{Version: version}
}

// Name returns the provisioner identifier.
func (p *Provisioner) Name() string {
	return ProvisionerName
}

// resolveImage replaces the :latest tag with the controller's own version tag.
// If the version is unknown ("dev"), dirty (contains "-g", indicating a
// resolveImageSpec returns the image from an ImageSpec override, falling back to
// the default image passed through qemucommon.ResolveImage. Also returns the pull policy.
func (p *Provisioner) resolveImageSpec(spec *virtualtargetv1alpha1.ImageSpec, defaultImage string) (string, corev1.PullPolicy) {
	image := qemucommon.ResolveImage(p.Version, defaultImage)
	pullPolicy := corev1.PullIfNotPresent

	if spec != nil {
		if spec.Image != "" {
			image = spec.Image
		}
		if spec.ImagePullPolicy != "" {
			pullPolicy = spec.ImagePullPolicy
		}
	}

	return image, pullPolicy
}

// RenderPod creates a Pod for a new QEMU-based exporter instance
// using the native sidecar pattern (KEP-753):
//
//   - copy-jumpstarter-exec (regular init container) copies the
//     jumpstarter-exec binary from the exporter image onto the
//     shared volume and exits.
//   - target-runtime (native sidecar, restartPolicy: Always) starts
//     next so launcher.sock is ready before the exporter; runs
//     jumpstarter-exec serve / QEMU.
//   - exporter (main container) runs `jmp run` — default kubectl logs
//     target; when it exits (exitOnLeaseEnd / ExitAndReplace),
//     Kubernetes terminates sidecars and the Pod completes.
//     Pod restartPolicy is Never so a clean exporter exit is not
//     restarted in-place (ExporterSet replaces the instance instead).
//   - Shared emptyDir for Unix sockets (QMP, serial, launcher).
//   - Guest disk volume at /disk (ephemeral PVC when
//     parameters.storage.storageClassName is set, otherwise sized
//     emptyDir with ephemeral-storage requests/limits).
//
// The caller (reconciler) is responsible for setting
// OwnerReferences on the Pod and injecting the config volume.
func (p *Provisioner) RenderPod(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	mergedParameters map[string]any,
	images *virtualtargetv1alpha1.ImageOverrides,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (*corev1.Pod, error) {
	restartAlways := corev1.ContainerRestartPolicyAlways
	sizeLimit := resource.MustParse(sharedVolumeSizeLimit)
	runAsRoot := int64(0)
	runAsExporter := exporterNonRootUID
	exporterNonRoot := true

	diskSpec, err := disk.FromParameters(mergedParameters)
	if err != nil {
		return nil, err
	}

	var exporterSpec, runtimeSpec *virtualtargetv1alpha1.ImageSpec
	if images != nil {
		exporterSpec = images.Exporter
		runtimeSpec = images.Runtime
	}

	exporterImage, exporterPullPolicy := p.resolveImageSpec(exporterSpec, qemucommon.DefaultExporterImage)
	runtimeImage, runtimePullPolicy := p.resolveImageSpec(runtimeSpec, qemucommon.DefaultQEMURuntimeImage)

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

	diskMount := disk.Mount()

	podMeta := metav1.ObjectMeta{
		Namespace:   exporterSet.Namespace,
		Labels:      maps.Clone(exporterSet.Spec.Template.Metadata.Labels),
		Annotations: maps.Clone(exporterSet.Spec.Template.Metadata.Annotations),
	}
	if exporter != nil {
		podMeta.Name = exporter.Name
	} else {
		podMeta.GenerateName = fmt.Sprintf("%s-", exporterSet.Name)
	}

	pod := &corev1.Pod{
		ObjectMeta: podMeta,
		Spec: corev1.PodSpec{
			// Never: ExitAndReplace relies on exporter (main) exit completing
			// the Pod. Always would restart jmp run in-place and skip recycle.
			RestartPolicy: corev1.RestartPolicyNever,
			InitContainers: []corev1.Container{
				{
					Name:            "copy-jumpstarter-exec",
					Image:           exporterImage,
					ImagePullPolicy: exporterPullPolicy,
					Command: []string{
						"cp",
						jmpExecBinaryPath,
						sharedMountPath + "/jumpstarter-exec",
					},
					VolumeMounts: []corev1.VolumeMount{
						{
							Name:      sharedVolumeName,
							MountPath: sharedMountPath,
						},
					},
				},
				{
					// Native sidecar: starts before the main exporter so
					// launcher.sock exists when jmp run begins. Torn down
					// automatically when the exporter (main) container exits.
					// Runs as root so QEMU can use KVM devices and read
					// exporter-owned paths on the shared volume.
					Name:            runtimeContainerName,
					Image:           runtimeImage,
					ImagePullPolicy: runtimePullPolicy,
					RestartPolicy:   &restartAlways,
					Env:             runtimeEnv,
					SecurityContext: &corev1.SecurityContext{
						RunAsUser:    &runAsRoot,
						RunAsNonRoot: new(false),
					},
					VolumeMounts: []corev1.VolumeMount{
						{
							Name:      sharedVolumeName,
							MountPath: sharedMountPath,
						},
						diskMount,
					},
				},
			},
			Containers: []corev1.Container{
				{
					Name:            "exporter",
					Image:           exporterImage,
					ImagePullPolicy: exporterPullPolicy,
					Command: []string{
						"jmp", "run", "--exporter-config",
						exporterConfigPath,
					},
					Env: []corev1.EnvVar{
						{
							Name:  "JUMPSTARTER_LAUNCHER_SOCKET",
							Value: qemucommon.LauncherSocketPath,
						},
					},
					SecurityContext: &corev1.SecurityContext{
						RunAsUser:    &runAsExporter,
						RunAsNonRoot: &exporterNonRoot,
					},
					VolumeMounts: []corev1.VolumeMount{
						{
							Name:      sharedVolumeName,
							MountPath: sharedMountPath,
						},
						diskMount,
					},
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

	pod.Spec.Volumes = append(pod.Spec.Volumes, disk.Volume(diskSpec))

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
			// CPU/memory belong on the runtime sidecar (where QEMU runs).
			for i := range pod.Spec.InitContainers {
				if pod.Spec.InitContainers[i].Name == runtimeContainerName {
					pod.Spec.InitContainers[i].Resources = *vtc.Spec.Scheduling.Resources.DeepCopy()
					break
				}
			}
		}
	}

	if diskSpec.UsePVC() {
		// fsGroup so the non-root exporter can write the ephemeral claim.
		if pod.Spec.SecurityContext == nil {
			pod.Spec.SecurityContext = &corev1.PodSecurityContext{}
		}
		pod.Spec.SecurityContext.FSGroup = &runAsExporter
	} else {
		// emptyDir guest disks consume node ephemeral storage. Reserve capacity
		// on one container only — the scheduler sums requests from all containers
		// (including restartable init containers / native sidecars).
		for i := range pod.Spec.InitContainers {
			if pod.Spec.InitContainers[i].Name == runtimeContainerName {
				disk.SetEphemeralStorage(&pod.Spec.InitContainers[i].Resources, diskSpec.VolumeSize)
				break
			}
		}
	}

	return pod, nil
}

// EnrichExporterExport injects QEMU-specific driver configuration.
func (p *Provisioner) EnrichExporterExport(
	drivers []virtualtargetv1alpha1.DriverConfig,
	mergedParameters map[string]any,
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	return qemucommon.EnrichExporterExport(drivers, mergedParameters)
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
