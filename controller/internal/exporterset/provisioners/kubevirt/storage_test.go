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
	"reflect"
	"testing"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	cdiv1beta1 "kubevirt.io/containerized-data-importer-api/pkg/apis/core/v1beta1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func namedDiskParams() map[string]any {
	return map[string]any{
		"namespace": "vm-ns", "cloudInit": "#cloud-config\nhostname: example",
		"storage": map[string]any{
			"type": "dataVolume", "storageClassName": "fast", "volumeMode": "Block",
			"uploadProxyURL": "https://upload.example.test", "uploadProxyCA": "test CA bundle",
			"disks": map[string]any{
				"root": map[string]any{"size": "30Gi", "source": map[string]any{
					"registry": map[string]any{"image": "quay.io/example/os:latest"},
				}},
				"data": map[string]any{"size": "50Gi", "accessModes": []any{"ReadWriteMany"}},
			},
		},
	}
}

func TestNamedDisksRenderedAndProvisioned(t *testing.T) {
	params, err := parseParams(namedDiskParams())
	if err != nil {
		t.Fatal(err)
	}
	prov := New("dev", nil)
	vm, err := prov.buildVirtualMachine(testExporter, params)
	if err != nil {
		t.Fatal(err)
	}
	vm.UID = types.UID("remote-vm-uid")
	if len(vm.Spec.DataVolumeTemplates) != 0 {
		t.Fatal("mutable disks must not have templates that recreate old images")
	}
	disks := vm.Spec.Template.Spec.Domain.Devices.Disks
	if len(disks) != 3 || disks[0].Name != "root" || disks[1].Name != "data" || disks[2].Name != "cloudinit" {
		t.Fatalf("unexpected or nondeterministic disk ordering: %#v", disks)
	}
	if disks[0].BootOrder == nil || *disks[0].BootOrder != 1 || disks[0].Serial != "root" {
		t.Fatalf("root disk has no stable boot identity: %#v", disks[0])
	}
	remote := fake.NewClientBuilder().WithScheme(remoteScheme()).Build()
	ctx := context.Background()
	if err := ensureDisks(ctx, remote, vm, params); err != nil {
		t.Fatal(err)
	}
	if err := ensureDisks(ctx, remote, vm, params); err != nil {
		t.Fatalf("reconcile must be idempotent: %v", err)
	}
	var root, data cdiv1beta1.DataVolume
	if err := remote.Get(ctx, client.ObjectKey{Namespace: "vm-ns", Name: "test-vm-root"}, &root); err != nil {
		t.Fatal(err)
	}
	if err := remote.Get(ctx, client.ObjectKey{Namespace: "vm-ns", Name: "test-vm-data"}, &data); err != nil {
		t.Fatal(err)
	}
	if !metav1.IsControlledBy(&root, vm) || !metav1.IsControlledBy(&data, vm) {
		t.Fatal("disk lifecycle must be owned by the remote VM UID")
	}
	if root.Spec.Source.Registry == nil || *root.Spec.Source.Registry.URL != "docker://quay.io/example/os:latest" {
		t.Fatalf("unexpected root source: %#v", root.Spec.Source)
	}
	if data.Spec.Source.Blank == nil || data.Spec.Storage.Resources.Requests.Storage().String() != "50Gi" {
		t.Fatalf("unexpected data disk: %#v", data.Spec)
	}
	if *data.Spec.Storage.StorageClassName != "fast" || *data.Spec.Storage.VolumeMode != corev1.PersistentVolumeBlock ||
		!reflect.DeepEqual(data.Spec.Storage.AccessModes, []corev1.PersistentVolumeAccessMode{corev1.ReadWriteMany}) {
		t.Fatalf("disk storage policy was not inherited/overridden: %#v", data.Spec.Storage)
	}
	config := diskDriverConfig(params)
	if config["data"].(map[string]any)["size"] != "50Gi" || config["root"].(map[string]any)["volume_mode"] != "Block" {
		t.Fatalf("exporter disk policy differs from provisioned disks: %#v", config)
	}
	result, err := prov.EnrichExporterExport(ctx, nil, nil, namedDiskParams(), testExporter)
	if err != nil {
		t.Fatal(err)
	}
	storage := unmarshalConfig(t, findDriver(result, "storage").Config)
	if storage["upload_proxy_url"] != "https://upload.example.test" || storage["upload_ca"] != "test CA bundle" {
		t.Fatalf("upload configuration missing: %#v", storage)
	}
}

func TestReconcileDoesNotRecreateReplacedDisk(t *testing.T) {
	params, err := parseParams(namedDiskParams())
	if err != nil {
		t.Fatal(err)
	}
	vm, err := New("dev", nil).buildVirtualMachine(testExporter, params)
	if err != nil {
		t.Fatal(err)
	}
	vm.UID = "remote-uid"
	for i := range vm.Spec.Template.Spec.Volumes {
		if vm.Spec.Template.Spec.Volumes[i].Name == "root" {
			vm.Spec.Template.Spec.Volumes[i].DataVolume.Name = "flashed-root"
		}
	}
	remote := fake.NewClientBuilder().WithScheme(remoteScheme()).Build()
	if err := ensureDisks(context.Background(), remote, vm, params); err != nil {
		t.Fatal(err)
	}
	var dv cdiv1beta1.DataVolume
	err = remote.Get(context.Background(), client.ObjectKey{Namespace: vm.Namespace, Name: vm.Name + "-root"}, &dv)
	if !apierrors.IsNotFound(err) {
		t.Fatalf("reconcile recreated an unused original disk: %v", err)
	}
}

func TestInitialDiskNameCollisionPreservesForeignVolume(t *testing.T) {
	params, err := parseParams(namedDiskParams())
	if err != nil {
		t.Fatal(err)
	}
	vm, err := New("dev", nil).buildVirtualMachine(testExporter, params)
	if err != nil {
		t.Fatal(err)
	}
	vm.UID = "remote-uid"
	foreign := &cdiv1beta1.DataVolume{ObjectMeta: metav1.ObjectMeta{Name: vm.Name + "-data", Namespace: vm.Namespace}}
	remote := fake.NewClientBuilder().WithScheme(remoteScheme()).WithObjects(foreign).Build()
	if err := ensureDisks(context.Background(), remote, vm, params); err == nil {
		t.Fatal("must reject an existing disk owned by somebody else")
	}
	var actual cdiv1beta1.DataVolume
	if err := remote.Get(context.Background(), client.ObjectKeyFromObject(foreign), &actual); err != nil {
		t.Fatal(err)
	}
	if len(actual.OwnerReferences) != 0 || actual.Spec.Source != nil {
		t.Fatalf("foreign disk was modified: %#v", actual)
	}
}

func TestLegacyStorageNormalizesToRoot(t *testing.T) {
	params, err := parseParams(map[string]any{
		"namespace": "ns", "resources": map[string]any{"storage": "42Gi"},
		"storage": map[string]any{
			"type": "dataVolume", "source": map[string]any{"pvc": map[string]any{"name": "golden"}},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	root := params.Storage.Disks["root"]
	if len(params.Storage.Disks) != 1 || root.Size != "42Gi" || root.Source.PVC.Name != "golden" {
		t.Fatalf("legacy storage did not normalize: %#v", params.Storage)
	}
}

func TestRejectInvalidDiskConfiguration(t *testing.T) {
	tests := map[string]map[string]any{
		"missing root":         {"disks": map[string]any{"data": map[string]any{}}},
		"reserved name":        {"disks": map[string]any{"root": map[string]any{}, "cloudinit": map[string]any{}}},
		"invalid name":         {"disks": map[string]any{"root": map[string]any{}, "Bad/Name": map[string]any{}}},
		"negative size":        {"size": "-1Gi"},
		"zero size":            {"size": "0"},
		"invalid size":         {"size": "oops"},
		"invalid mode":         {"volumeMode": "bad"},
		"empty access modes":   {"accessModes": []any{}},
		"readonly access mode": {"accessModes": []any{"ReadOnlyMany"}},
		"insecure upload":      {"uploadProxyURL": "http://upload.example.test"},
		"duplicate boot order": {"disks": map[string]any{"root": map[string]any{}, "data": map[string]any{"bootOrder": 1}}},
		"multiple sources":     {"source": map[string]any{"http": map[string]any{"url": "https://example.test"}, "blank": map[string]any{}}},
	}
	for name, storage := range tests {
		t.Run(name, func(t *testing.T) {
			storage["type"] = "dataVolume"
			if _, err := parseStorageConfig(storage, defaultDiskSize); err == nil {
				t.Fatal("invalid disk configuration was accepted")
			}
		})
	}
}
