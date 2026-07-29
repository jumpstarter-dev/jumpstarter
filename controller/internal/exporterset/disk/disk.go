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

// Package disk provides shared helpers for guest-disk volume provisioning
// used by ExporterSet provisioners and the reconciler.
package disk

import (
	"fmt"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	// VolumeName is the Pod volume name for guest disk storage.
	VolumeName = "disk"

	// MountPath is where guest disk storage is mounted in exporter and runtime.
	MountPath = "/disk"

	// DefaultSize is used when parameters.resources.storage is unset.
	DefaultSize = "10Gi"

	pvcNamePrefix = "disk-"
)

// PVCName returns the per-exporter PersistentVolumeClaim name.
func PVCName(exporterName string) string {
	return pvcNamePrefix + exporterName
}

// SizeFromParameters reads parameters.resources.storage, defaulting to DefaultSize.
func SizeFromParameters(params map[string]interface{}) (resource.Quantity, error) {
	raw := DefaultSize
	if params != nil {
		if resources, ok := params["resources"].(map[string]interface{}); ok {
			switch v := resources["storage"].(type) {
			case string:
				if v != "" {
					raw = v
				}
			case float64:
				// JSON numbers land as float64; treat as Gi if unitless is awkward —
				// require string quantities in the API.
				return resource.Quantity{}, fmt.Errorf("parameters.resources.storage must be a string quantity (e.g. \"10Gi\"), got number %v", v)
			case nil:
				// use default
			default:
				return resource.Quantity{}, fmt.Errorf("parameters.resources.storage must be a string quantity (e.g. \"10Gi\"), got %T", v)
			}
		}
	}

	qty, err := resource.ParseQuantity(raw)
	if err != nil {
		return resource.Quantity{}, fmt.Errorf("parse parameters.resources.storage %q: %w", raw, err)
	}
	return qty, nil
}

// BuildPVC constructs a guest-disk PVC owned by the given exporter metadata.
func BuildPVC(namespace, exporterName, storageClassName string, size resource.Quantity, labels map[string]string) *corev1.PersistentVolumeClaim {
	return &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      PVCName(exporterName),
			Namespace: namespace,
			Labels:    labels,
		},
		Spec: corev1.PersistentVolumeClaimSpec{
			AccessModes: []corev1.PersistentVolumeAccessMode{
				corev1.ReadWriteOnce,
			},
			StorageClassName: &storageClassName,
			Resources: corev1.VolumeResourceRequirements{
				Requests: corev1.ResourceList{
					corev1.ResourceStorage: size,
				},
			},
		},
	}
}

// SetEphemeralStorage ensures requests and limits include ephemeral-storage
// equal to size (used when guest disk is backed by emptyDir).
func SetEphemeralStorage(resources *corev1.ResourceRequirements, size resource.Quantity) {
	if resources.Requests == nil {
		resources.Requests = corev1.ResourceList{}
	}
	if resources.Limits == nil {
		resources.Limits = corev1.ResourceList{}
	}
	// Only set when unset so explicit scheduling.resources win.
	if _, ok := resources.Requests[corev1.ResourceEphemeralStorage]; !ok {
		resources.Requests[corev1.ResourceEphemeralStorage] = size.DeepCopy()
	}
	if _, ok := resources.Limits[corev1.ResourceEphemeralStorage]; !ok {
		resources.Limits[corev1.ResourceEphemeralStorage] = size.DeepCopy()
	}
}
