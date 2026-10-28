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

package kubevirt

import (
	"context"
	"fmt"
	"maps"
	"slices"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	kubevirtv1 "kubevirt.io/api/core/v1"
	cdiv1beta1 "kubevirt.io/containerized-data-importer-api/pkg/apis/core/v1beta1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func configureDisks(vm *kubevirtv1.VirtualMachine, params *KubevirtParams) {
	spec := &vm.Spec.Template.Spec
	if params.Storage == nil || params.Storage.Type == StorageTypeContainerDisk {
		spec.Domain.Devices.Disks = []kubevirtv1.Disk{{
			Name: "root", Serial: "root",
			DiskDevice: kubevirtv1.DiskDevice{Disk: &kubevirtv1.DiskTarget{Bus: kubevirtv1.DiskBusVirtio}},
		}}
		spec.Volumes = []kubevirtv1.Volume{{Name: "root", VolumeSource: kubevirtv1.VolumeSource{
			ContainerDisk: &kubevirtv1.ContainerDiskSource{Image: params.Image},
		}}}
		return
	}
	// Keep root as the first guest disk even when another name sorts before it.
	names := slices.Sorted(maps.Keys(params.Storage.Disks))
	names = append([]string{"root"}, slices.DeleteFunc(names, func(name string) bool { return name == "root" })...)
	for _, name := range names {
		disk := params.Storage.Disks[name]
		spec.Domain.Devices.Disks = append(spec.Domain.Devices.Disks, kubevirtv1.Disk{
			Name: name, Serial: name, BootOrder: disk.BootOrder,
			DiskDevice: kubevirtv1.DiskDevice{Disk: &kubevirtv1.DiskTarget{Bus: kubevirtv1.DiskBusVirtio}},
		})
		spec.Volumes = append(spec.Volumes, kubevirtv1.Volume{Name: name, VolumeSource: kubevirtv1.VolumeSource{
			DataVolume: &kubevirtv1.DataVolumeSource{Name: vm.Name + "-" + name},
		}})
	}
}

// Only initialize volumes still referenced under their original names. A later
// reconcile must never recreate a boot image that storage.flash has replaced.
func ensureDisks(ctx context.Context, remote client.Client, vm *kubevirtv1.VirtualMachine, params *KubevirtParams) error {
	if params.Storage == nil || params.Storage.Type != StorageTypeDataVolume {
		return nil
	}
	for _, volume := range vm.Spec.Template.Spec.Volumes {
		disk, ok := params.Storage.Disks[volume.Name]
		if !ok || volume.DataVolume == nil || volume.DataVolume.Name != vm.Name+"-"+volume.Name {
			continue
		}
		dv := initialDataVolume(vm, volume.Name, disk)
		if err := remote.Create(ctx, dv); err != nil {
			if !apierrors.IsAlreadyExists(err) {
				return err
			}
			existing := &cdiv1beta1.DataVolume{}
			if err := remote.Get(ctx, client.ObjectKeyFromObject(dv), existing); err != nil {
				return err
			}
			if !metav1.IsControlledBy(existing, vm) {
				return fmt.Errorf("DataVolume %s/%s is not owned by VM %s", dv.Namespace, dv.Name, vm.UID)
			}
		}
	}
	return nil
}

func initialDataVolume(vm *kubevirtv1.VirtualMachine, name string, disk DiskConfig) *cdiv1beta1.DataVolume {
	source := &cdiv1beta1.DataVolumeSource{}
	switch {
	case disk.Source.HTTP != nil:
		source.HTTP = &cdiv1beta1.DataVolumeSourceHTTP{URL: disk.Source.HTTP.URL}
	case disk.Source.Registry != nil:
		url := "docker://" + disk.Source.Registry.Image
		source.Registry = &cdiv1beta1.DataVolumeSourceRegistry{URL: &url}
	case disk.Source.PVC != nil:
		ns := disk.Source.PVC.Namespace
		if ns == "" {
			ns = vm.Namespace
		}
		source.PVC = &cdiv1beta1.DataVolumeSourcePVC{Name: disk.Source.PVC.Name, Namespace: ns}
	default:
		source.Blank = &cdiv1beta1.DataVolumeBlankImage{}
	}
	storage := &cdiv1beta1.StorageSpec{
		Resources: corev1.VolumeResourceRequirements{
			Requests: corev1.ResourceList{corev1.ResourceStorage: resource.MustParse(disk.Size)},
		},
	}
	for _, mode := range disk.AccessModes {
		storage.AccessModes = append(storage.AccessModes, corev1.PersistentVolumeAccessMode(mode))
	}
	if disk.StorageClassName != "" {
		storage.StorageClassName = &disk.StorageClassName
	}
	if disk.VolumeMode != "" {
		mode := corev1.PersistentVolumeMode(disk.VolumeMode)
		storage.VolumeMode = &mode
	}
	ownerRef := metav1.NewControllerRef(vm, kubevirtv1.VirtualMachineGroupVersionKind)
	// VM ownership provides garbage collection without blocking VM deletion.
	// Match uploaded disks without requiring virtualmachines/finalizers permission.
	ownerRef.BlockOwnerDeletion = nil
	return &cdiv1beta1.DataVolume{
		ObjectMeta: metav1.ObjectMeta{
			Name: vm.Name + "-" + name, Namespace: vm.Namespace,
			Labels:          maps.Clone(vm.Labels),
			Annotations:     map[string]string{"cdi.kubevirt.io/storage.deleteAfterCompletion": "false"},
			OwnerReferences: []metav1.OwnerReference{*ownerRef},
		},
		Spec: cdiv1beta1.DataVolumeSpec{Source: source, Storage: storage},
	}
}

func diskDriverConfig(params *KubevirtParams) map[string]any {
	disks := make(map[string]any)
	if params.Storage != nil && params.Storage.Type == StorageTypeDataVolume {
		for name, disk := range params.Storage.Disks {
			disks[name] = map[string]any{
				"size": disk.Size, "storage_class_name": disk.StorageClassName,
				"access_modes": disk.AccessModes, "volume_mode": disk.VolumeMode,
			}
		}
	}
	return disks
}
