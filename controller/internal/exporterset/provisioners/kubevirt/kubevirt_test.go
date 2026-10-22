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
	"encoding/json"
	"maps"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

var testExporter = &jumpstarterdevv1alpha1.Exporter{
	ObjectMeta: metav1.ObjectMeta{
		Name:      "test-vm",
		Namespace: "test-ns",
	},
}

func TestParseParams_defaults(t *testing.T) {
	params := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
	}

	p, err := parseParams(params)
	if err != nil {
		t.Fatal(err)
	}

	if p.Namespace != "test-ns" {
		t.Errorf("namespace = %q, want test-ns", p.Namespace)
	}
	if p.CPU != defaultCPU {
		t.Errorf("cpu = %d, want %d", p.CPU, defaultCPU)
	}
	if p.Memory != defaultMemory {
		t.Errorf("memory = %q, want %q", p.Memory, defaultMemory)
	}
	if p.DiskSize != defaultDiskSize {
		t.Errorf("diskSize = %q, want %q", p.DiskSize, defaultDiskSize)
	}
	if p.Image != "quay.io/test/image:latest" {
		t.Errorf("image = %q", p.Image)
	}
}

func TestParseParams_requiresNamespace(t *testing.T) {
	params := map[string]any{
		"image": "quay.io/test/image:latest",
	}

	_, err := parseParams(params)
	if err == nil {
		t.Fatal("expected error for missing namespace")
	}
}

func TestParseParams_requiresImage(t *testing.T) {
	params := map[string]any{
		"namespace": "test-ns",
	}

	_, err := parseParams(params)
	if err == nil {
		t.Fatal("expected error for missing image")
	}
}

func TestParseParams_overridesResources(t *testing.T) {
	params := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
		"resources": map[string]any{
			"cpu":     8,
			"memory":  "16Gi",
			"storage": "100Gi",
		},
	}

	p, err := parseParams(params)
	if err != nil {
		t.Fatal(err)
	}

	if p.CPU != 8 {
		t.Errorf("cpu = %d, want 8", p.CPU)
	}
	if p.Memory != "16Gi" {
		t.Errorf("memory = %q, want 16Gi", p.Memory)
	}
	if p.DiskSize != "100Gi" {
		t.Errorf("diskSize = %q, want 100Gi", p.DiskSize)
	}
}

func TestParseParams_validatesSSHAccess(t *testing.T) {
	base := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
	}

	for _, valid := range []string{"", "NodePort", "LoadBalancer"} {
		params := maps.Clone(base)
		params["sshAccess"] = valid
		if _, err := parseParams(params); err != nil {
			t.Errorf("sshAccess=%q should be valid: %v", valid, err)
		}
	}

	params := maps.Clone(base)
	params["sshAccess"] = "Invalid"
	if _, err := parseParams(params); err == nil {
		t.Error("sshAccess=Invalid should fail")
	}
}

func TestParseParams_rejectsInvalidMemory(t *testing.T) {
	params := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
		"resources": map[string]any{
			"memory": "not-a-quantity",
		},
	}

	_, err := parseParams(params)
	if err == nil {
		t.Fatal("expected error for invalid memory")
	}
}

func TestEnrichExporterExport_injectsAllDrivers(t *testing.T) {
	prov := New("dev", nil)

	params := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
		"sshAccess": "NodePort",
	}

	result, err := prov.EnrichExporterExport(context.Background(), nil, nil, params, testExporter)
	if err != nil {
		t.Fatal(err)
	}

	assertDriverExists(t, result, "power", kubevirtPowerType)
	assertDriverExists(t, result, "console", kubevirtConsoleType)
	assertDriverExists(t, result, "storage", kubevirtStorageType)
}

func TestEnrichExporterExport_forwardPortsNotInjectedWithoutVTC(t *testing.T) {
	prov := New("dev", nil)

	params := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
		"forwardPorts": map[string]any{
			"adb":  6520,
			"http": 8080,
		},
	}

	result, err := prov.EnrichExporterExport(context.Background(), nil, nil, params, testExporter)
	if err != nil {
		t.Fatal(err)
	}

	if findDriver(result, "adb") != nil {
		t.Error("adb driver should not be injected without a resolvable VTC")
	}
	if findDriver(result, "http") != nil {
		t.Error("http driver should not be injected without a resolvable VTC")
	}
}

func TestEnrichExporterExport_skipsForwardPortWithExistingDriver(t *testing.T) {
	prov := New("dev", nil)

	existing := []virtualtargetv1alpha1.DriverConfig{
		{Name: "adb", Type: "jumpstarter_driver_adb.driver.AdbServer"},
	}

	params := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
		"forwardPorts": map[string]any{
			"adb": 6520,
		},
	}

	result, err := prov.EnrichExporterExport(context.Background(), nil, existing, params, testExporter)
	if err != nil {
		t.Fatal(err)
	}

	adbDrivers := 0
	for _, d := range result {
		if d.Name == "adb" {
			adbDrivers++
		}
	}
	if adbDrivers != 1 {
		t.Errorf("expected exactly 1 adb driver, got %d", adbDrivers)
	}

	adb := findDriver(result, "adb")
	if adb.Type != "jumpstarter_driver_adb.driver.AdbServer" {
		t.Errorf("adb driver type = %q, want user-declared type", adb.Type)
	}
}

func TestEnrichExporterExport_preservesExistingDrivers(t *testing.T) {
	prov := New("dev", nil)

	existing := []virtualtargetv1alpha1.DriverConfig{
		{Name: "custom", Type: "my.custom.Driver"},
	}

	params := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
	}

	result, err := prov.EnrichExporterExport(context.Background(), nil, existing, params, testExporter)
	if err != nil {
		t.Fatal(err)
	}

	if findDriver(result, "custom") == nil {
		t.Error("existing custom driver was lost")
	}
}

func TestEnrichExporterExport_doesNotDuplicateKubevirt(t *testing.T) {
	prov := New("dev", nil)

	existing := []virtualtargetv1alpha1.DriverConfig{
		{Name: "kubevirt", Type: kubevirtDriverType},
	}

	params := map[string]any{
		"namespace": "test-ns",
		"image":     "quay.io/test/image:latest",
	}

	result, err := prov.EnrichExporterExport(context.Background(), nil, existing, params, testExporter)
	if err != nil {
		t.Fatal(err)
	}

	if len(result) != 1 {
		t.Errorf("expected 1 driver (user-provided kubevirt only), got %d", len(result))
	}
}

func TestEnrichExporterExport_namespaceInConfig(t *testing.T) {
	prov := New("dev", nil)

	params := map[string]any{
		"namespace": "remote-ns",
		"image":     "quay.io/test/image:latest",
	}

	result, err := prov.EnrichExporterExport(context.Background(), nil, nil, params, testExporter)
	if err != nil {
		t.Fatal(err)
	}

	powerDriver := findDriver(result, "power")
	if powerDriver == nil {
		t.Fatal("power driver not found")
	}

	config := unmarshalConfig(t, powerDriver.Config)
	if got := config["namespace"]; got != "remote-ns" {
		t.Errorf("power namespace = %v, want remote-ns", got)
	}
	if got := config["vm_name"]; got != "test-vm" {
		t.Errorf("power vm_name = %v, want test-vm", got)
	}
}

func TestEnrichExporterExport_storageConfigUsesProvisionedDiskSettings(t *testing.T) {
	prov := New("dev", nil)
	params := map[string]any{
		"namespace": "remote-ns",
		"image":     "quay.io/test/image:latest",
		"storage": map[string]any{
			"type":        "dataVolume",
			"size":        "40Gi",
			"accessModes": []any{"ReadWriteMany"},
			"source": map[string]any{
				"http": map[string]any{"url": "https://example.com/disk.qcow2"},
			},
		},
	}

	result, err := prov.EnrichExporterExport(context.Background(), nil, nil, params, testExporter)
	if err != nil {
		t.Fatal(err)
	}

	storageDriver := findDriver(result, "storage")
	if storageDriver == nil {
		t.Fatal("storage driver not found")
	}
	config := unmarshalConfig(t, storageDriver.Config)
	if got := config["disk_size"]; got != "40Gi" {
		t.Errorf("storage disk_size = %v, want 40Gi", got)
	}
	accessModes, ok := config["access_modes"].([]any)
	if !ok || len(accessModes) != 1 || accessModes[0] != "ReadWriteMany" {
		t.Errorf("storage access_modes = %#v, want [ReadWriteMany]", config["access_modes"])
	}
}

func TestResolveServiceAddress_nodePortUsesNodeAddress(t *testing.T) {
	service := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: "test-vm-ssh", Namespace: "remote-ns"},
		Spec: corev1.ServiceSpec{
			Type:      corev1.ServiceTypeNodePort,
			ClusterIP: "10.96.0.42",
			Ports: []corev1.ServicePort{{
				Port:     22,
				NodePort: 30222,
			}},
		},
	}
	node := &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{Name: "worker-1"},
		Status: corev1.NodeStatus{Addresses: []corev1.NodeAddress{
			{Type: corev1.NodeInternalIP, Address: "192.0.2.20"},
			{Type: corev1.NodeExternalIP, Address: "198.51.100.20"},
		}},
	}
	prov, vtc := newRemoteTestProvisioner(t, service, node)

	host, port, err := prov.resolveServiceAddress(
		context.Background(), vtc, &KubevirtParams{Namespace: "remote-ns"}, "test-vm-ssh", 22000,
	)
	if err != nil {
		t.Fatal(err)
	}
	if host != "198.51.100.20" || port != 30222 {
		t.Errorf("address = %s:%d, want 198.51.100.20:30222", host, port)
	}
}

func TestResolveServiceAddress_pendingLoadBalancerReturnsError(t *testing.T) {
	service := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: "test-vm-ssh", Namespace: "remote-ns"},
		Spec: corev1.ServiceSpec{
			Type:      corev1.ServiceTypeLoadBalancer,
			ClusterIP: "10.96.0.42",
			Ports:     []corev1.ServicePort{{Port: 22}},
		},
	}
	prov, vtc := newRemoteTestProvisioner(t, service)

	if _, _, err := prov.resolveServiceAddress(
		context.Background(), vtc, &KubevirtParams{Namespace: "remote-ns"}, "test-vm-ssh", 22000,
	); err == nil {
		t.Fatal("expected error while LoadBalancer has no ingress address")
	}
}

func TestCleanup_retainPolicyPreservesRemoteResources(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := virtualtargetv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		ObjectMeta: metav1.ObjectMeta{Name: "kubevirt-class", Namespace: "test-ns"},
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner:   ProvisionerName,
			ReclaimPolicy: virtualtargetv1alpha1.ReclaimPolicyRetain,
		},
	}
	managementClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(vtc).Build()
	prov := New("dev", managementClient)
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "test-set", Namespace: "test-ns"},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			VirtualTargetClassName: "kubevirt-class",
		},
	}

	if err := prov.Cleanup(context.Background(), exporterSet, testExporter); err != nil {
		t.Fatalf("Cleanup() error = %v", err)
	}
}

// --- helpers ---

func newRemoteTestProvisioner(
	t *testing.T,
	objects ...client.Object,
) (*Provisioner, *virtualtargetv1alpha1.VirtualTargetClass) {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "credentials", Namespace: "test-ns"},
	}
	managementClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(secret).Build()
	remoteClient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(objects...).Build()
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		ObjectMeta: metav1.ObjectMeta{Name: "kubevirt-class", Namespace: "test-ns"},
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			CredentialsSecretRef: &corev1.LocalObjectReference{Name: "credentials"},
		},
	}

	var storedSecret corev1.Secret
	if err := managementClient.Get(context.Background(), types.NamespacedName{
		Name: "credentials", Namespace: "test-ns",
	}, &storedSecret); err != nil {
		t.Fatal(err)
	}
	prov := New("dev", managementClient)
	prov.remoteClients.set("test-ns/kubevirt-class/"+storedSecret.ResourceVersion, remoteClient)
	return prov, vtc
}

func findDriver(drivers []virtualtargetv1alpha1.DriverConfig, name string) *virtualtargetv1alpha1.DriverConfig {
	for i := range drivers {
		if drivers[i].Name == name {
			return &drivers[i]
		}
	}
	return nil
}

func assertDriverExists(t *testing.T, drivers []virtualtargetv1alpha1.DriverConfig, name, expectedType string) {
	t.Helper()
	d := findDriver(drivers, name)
	if d == nil {
		t.Errorf("driver %q not found", name)
		return
	}
	if d.Type != expectedType {
		t.Errorf("driver %q type = %q, want %q", name, d.Type, expectedType)
	}
}

func unmarshalConfig(t *testing.T, raw *apiextensionsv1.JSON) map[string]any {
	t.Helper()
	if raw == nil || raw.Raw == nil {
		t.Fatal("config is nil")
	}
	var config map[string]any
	if err := json.Unmarshal(raw.Raw, &config); err != nil {
		t.Fatalf("unmarshal config: %v", err)
	}
	return config
}
