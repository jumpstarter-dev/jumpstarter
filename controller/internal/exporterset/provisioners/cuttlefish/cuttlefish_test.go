package cuttlefish

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestProvisionerName(t *testing.T) {
	if got := New("dev").Name(); got != ProvisionerName {
		t.Fatalf("Name() = %q, want %q", got, ProvisionerName)
	}
}

func TestRenderPod(t *testing.T) {
	exporterSet := testExporterSet()
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{Provisioner: ProvisionerName},
	}

	pod, err := New("dev").RenderPod(context.Background(), exporterSet, vtc, map[string]interface{}{
		"fetch_images":         true,
		"runtime_privileged":   true,
		"service_account_name": "cuttlefish-runtime",
	}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(pod.Spec.InitContainers) != 5 {
		t.Fatalf("init container count = %d, want 5", len(pod.Spec.InitContainers))
	}
	if pod.Spec.InitContainers[0].Name != "fetch-images" {
		t.Errorf("first init container = %q", pod.Spec.InitContainers[0].Name)
	}
	if pod.Spec.InitContainers[2].Name != "cuttlefish" || pod.Spec.InitContainers[2].RestartPolicy == nil {
		t.Errorf("runtime sidecar = %#v", pod.Spec.InitContainers[2])
	}
	if pod.Spec.InitContainers[2].SecurityContext == nil ||
		pod.Spec.InitContainers[2].SecurityContext.Privileged == nil ||
		!*pod.Spec.InitContainers[2].SecurityContext.Privileged {
		t.Fatal("Cuttlefish runtime must be privileged")
	}
	if pod.Spec.InitContainers[3].Name != "cuttlefish-relay" {
		t.Errorf("relay container = %q", pod.Spec.InitContainers[3].Name)
	}
	if len(pod.Spec.Containers) != 1 || pod.Spec.Containers[0].Name != "exporter" {
		t.Fatalf("containers = %#v", pod.Spec.Containers)
	}
	if pod.Spec.Containers[0].Env[0].Name != "HOME" || pod.Spec.Containers[0].Env[0].Value != "/tmp" {
		t.Errorf("exporter HOME = %#v, want /tmp", pod.Spec.Containers[0].Env[0])
	}
	if !hasVolume(pod.Spec.Volumes, "kvm", "/dev/kvm") || !hasVolume(pod.Spec.Volumes, "tun", "/dev/net/tun") {
		t.Fatalf("device volumes missing: %#v", pod.Spec.Volumes)
	}
}

func TestRenderPod_rejectsFetchingIntoClaim(t *testing.T) {
	exporterSet := testExporterSet()
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{}

	_, err := New("dev").RenderPod(context.Background(), exporterSet, vtc, map[string]interface{}{
		"fetch_images":         true,
		"image_volume_claim":   "cuttlefish-images",
		"runtime_privileged":   true,
		"service_account_name": "cuttlefish-runtime",
	}, nil, nil)
	if err == nil {
		t.Fatal("RenderPod() succeeded; want an error for fetch_images with image_volume_claim")
	}
}

func renderTestPod(t *testing.T, params map[string]interface{}) *corev1.Pod {
	t.Helper()
	params["runtime_privileged"] = true
	params["service_account_name"] = "cuttlefish-runtime"
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), &virtualtargetv1alpha1.VirtualTargetClass{}, params, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	return pod
}

func TestRenderPod_privateImageCopy(t *testing.T) {
	for _, readOnly := range []bool{true, false} {
		pod := renderTestPod(t, map[string]interface{}{"image_volume_claim": "images", "image_volume_read_only": readOnly})
		for _, volume := range pod.Spec.Volumes {
			if volume.PersistentVolumeClaim != nil && !volume.PersistentVolumeClaim.ReadOnly {
				t.Fatal("source claim is writable")
			}
			if volume.Name == "cvd-images" && volume.EmptyDir == nil {
				t.Fatal("missing private image volume")
			}
		}
		copy := pod.Spec.InitContainers[0]
		if copy.Name != "copy-images" || !copy.VolumeMounts[0].ReadOnly || copy.VolumeMounts[1].ReadOnly {
			t.Fatal("invalid copy mounts")
		}
		for _, container := range pod.Spec.InitContainers[1:] {
			for _, mount := range container.VolumeMounts {
				if mount.Name == "image-source" {
					t.Fatalf("%s can access shared source", container.Name)
				}
			}
		}
	}
}

func TestRenderPod_healthAndRelayIsolation(t *testing.T) {
	pod := renderTestPod(t, map[string]interface{}{"fetch_images": true})
	gate := pod.Spec.InitContainers[len(pod.Spec.InitContainers)-1]
	if gate.Name != "wait-for-cuttlefish" || !strings.Contains(gate.Command[2], "127.0.0.1:2081/_debug/statusz") {
		t.Fatal("missing API startup gate")
	}
	probe := pod.Spec.Containers[0].LivenessProbe
	if probe == nil || probe.Exec.Command[2] != "jumpstarter_driver_cuttlefish.health" {
		t.Fatal("missing runtime failure detection")
	}
	for _, container := range pod.Spec.InitContainers {
		if container.Name == "cuttlefish-relay" && strings.Count(container.Command[2], "bind=127.0.0.1") != 2 {
			t.Fatal("relay exposed outside Pod")
		}
	}
}

func TestRenderPod_storageBudgets(t *testing.T) {
	pod := renderTestPod(t, map[string]interface{}{"fetch_images": true, "storage": map[string]interface{}{"imageSize": "8Gi", "stateSize": "4Gi", "tmpSize": "2Gi"}})
	total := resource.MustParse("1Gi")
	for _, volume := range pod.Spec.Volumes {
		if volume.EmptyDir != nil {
			if volume.EmptyDir.SizeLimit == nil {
				t.Fatalf("%s has no budget", volume.Name)
			}
			total.Add(*volume.EmptyDir.SizeLimit)
		}
	}
	for _, container := range pod.Spec.InitContainers {
		if container.Name == "fetch-images" || container.Name == "cuttlefish" {
			request := container.Resources.Requests[corev1.ResourceEphemeralStorage]
			limit := container.Resources.Limits[corev1.ResourceEphemeralStorage]
			if request.Cmp(total) != 0 || limit.Cmp(total) != 0 {
				t.Fatalf("%s storage does not cover volumes: %v", container.Name, container.Resources)
			}
		}
	}
}

func TestStorageValidation(t *testing.T) {
	for _, value := range []interface{}{"", "0", "-1Gi", "invalid", 42} {
		_, _, _, err := storageSizes(map[string]interface{}{"storage": map[string]interface{}{"imageSize": value}})
		if err == nil {
			t.Fatalf("accepted invalid size %v", value)
		}
	}
	budget := resource.MustParse("10Gi")
	for _, resources := range []corev1.ResourceRequirements{
		{Requests: corev1.ResourceList{corev1.ResourceEphemeralStorage: resource.MustParse("1Gi")}},
		{Limits: corev1.ResourceList{corev1.ResourceEphemeralStorage: resource.MustParse("1Gi")}},
		{Requests: corev1.ResourceList{corev1.ResourceEphemeralStorage: resource.MustParse("20Gi")}, Limits: corev1.ResourceList{corev1.ResourceEphemeralStorage: budget}},
	} {
		if reserveStorage(&resources, budget) == nil {
			t.Fatal("accepted insufficient or inconsistent storage")
		}
	}
}

func TestEnrichExporterExport(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
		{Name: "netsim", Type: netsimDriverType},
		{Name: "bt_peer", Type: btPeerDriverType},
	}
	result, err := New("dev").EnrichExporterExport(drivers, map[string]interface{}{
		"default_build": "aosp/test",
		"gpu_mode":      "none",
	})
	if err != nil {
		t.Fatal(err)
	}

	cuttlefish := configFor(t, result[0])
	if cuttlefish["host"] != "127.0.0.1" || cuttlefish["port"] != float64(hostOrchestratorPort) {
		t.Errorf("Cuttlefish endpoint = %#v", cuttlefish)
	}
	envConfig := cuttlefish["env_config"].(map[string]interface{})
	instances := envConfig["instances"].([]interface{})
	instance := instances[0].(map[string]interface{})
	graphics := instance["graphics"].(map[string]interface{})
	if graphics["gpu_mode"] != "none" {
		t.Errorf("gpu_mode = %v", graphics["gpu_mode"])
	}
	vm := instance["vm"].(map[string]interface{})
	if vm["cpus"] != float64(defaultVMCPUs) || vm["memory_mb"] != float64(defaultVMMemoryMB) {
		t.Errorf("vm config = %#v", vm)
	}

	netsim := configFor(t, result[1])
	if netsim["host"] != "127.0.0.1" || netsim["port"] != float64(netsimRelayPort) {
		t.Errorf("netsim config = %#v", netsim)
	}
	if _, exists := netsim["transport"]; exists {
		t.Error("netsim driver does not accept transport")
	}
	btPeer := configFor(t, result[2])
	if btPeer["transport"] != fmt.Sprintf("tcp-client:127.0.0.1:%d", hciRelayPort) {
		t.Errorf("bt_peer config = %#v", btPeer)
	}
}

func TestEnrichExporterExportDefaultsPodSafeGraphicsAndVM(t *testing.T) {
	result, err := New("dev").EnrichExporterExport([]virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
	}, nil)
	if err != nil {
		t.Fatal(err)
	}

	config := configFor(t, result[0])
	envConfig := config["env_config"].(map[string]interface{})
	instance := envConfig["instances"].([]interface{})[0].(map[string]interface{})
	graphics := instance["graphics"].(map[string]interface{})
	if graphics["gpu_mode"] != defaultGPUMode {
		t.Errorf("gpu_mode = %v, want %q", graphics["gpu_mode"], defaultGPUMode)
	}
}

func TestEnrichExporterExportRejectsExternalEndpoints(t *testing.T) {
	for _, config := range []map[string]interface{}{{"host": "custom-host"}, {"port": 9999}, {"instance_num": 2}, {"scheme": "https"}} {
		driver := virtualtargetv1alpha1.DriverConfig{Name: "cuttlefish", Type: cuttlefishDriverType, Config: mustJSON(config)}
		if _, err := New("dev").EnrichExporterExport([]virtualtargetv1alpha1.DriverConfig{driver}, nil); err == nil {
			t.Errorf("accepted external endpoint %v", config)
		}
	}
}

func configFor(t *testing.T, driver virtualtargetv1alpha1.DriverConfig) map[string]interface{} {
	t.Helper()
	var config map[string]interface{}
	if err := json.Unmarshal(driver.Config.Raw, &config); err != nil {
		t.Fatal(err)
	}
	return config
}

func hasVolume(volumes []corev1.Volume, name, path string) bool {
	for _, volume := range volumes {
		if volume.Name == name && volume.HostPath != nil && volume.HostPath.Path == path {
			return true
		}
	}
	return false
}

func mustJSON(value interface{}) *apiextensionsv1.JSON {
	raw, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return &apiextensionsv1.JSON{Raw: raw}
}

func testExporterSet() *virtualtargetv1alpha1.ExporterSet {
	return &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "cuttlefish", Namespace: "default", UID: "test-uid"},
		Spec:       virtualtargetv1alpha1.ExporterSetSpec{Template: virtualtargetv1alpha1.ExporterSetTemplate{Spec: virtualtargetv1alpha1.ExporterTemplateSpec{Drivers: []virtualtargetv1alpha1.DriverConfig{{Name: "cuttlefish", Type: cuttlefishDriverType}}}}},
	}
}

func TestRelayPortValidation(t *testing.T) {
	for _, params := range []map[string]interface{}{
		{"hci_relay_port": 17681}, {"netsim_relay_port": 7681}, {"hci_relay_port": 7300},
		{"netsim_relay_port": 0}, {"netsim_relay_port": -1}, {"netsim_relay_port": 65536},
		{"netsim_relay_port": 1234.5}, {"netsim_relay_port": "1234"}, {"netsim_relay_port": true},
		{"netsim_relay_port": 80}, {"netsim_relay_port": 19531}, {"hci_relay_port": 6521},
		{"host_orchestrator_port": 9999}, {"netsim_relay_port": 2081}, {"netsim_relay_port": 15550},
	} {
		if _, _, err := relayPorts(params); err == nil {
			t.Errorf("accepted %v", params)
		}
	}
	if n, h, err := relayPorts(map[string]interface{}{"netsim_relay_port": float64(27681), "hci_relay_port": 27300}); err != nil || n != 27681 || h != 27300 {
		t.Fatalf("valid ports: %d %d %v", n, h, err)
	}
}

func TestManagedContract(t *testing.T) {
	for _, config := range []map[string]interface{}{
		{"env_config": map[string]interface{}{"instances": []interface{}{map[string]interface{}{}, map[string]interface{}{}}}},
		{"env_config": map[string]interface{}{"instances": "invalid"}},
		{"env_config": map[string]interface{}{"instances": []interface{}{}}},
		{"env_config": map[string]interface{}{"instances": []interface{}{nil}}},
		{"env_config": "invalid"},
		{"env_config": map[string]interface{}{"netsim_bt": false}},
		{"env_config": map[string]interface{}{"instances": []interface{}{map[string]interface{}{"vm": map[string]interface{}{"memory_mb": -1}}}}},
		{"env_config": map[string]interface{}{"instances": []interface{}{map[string]interface{}{"vm": map[string]interface{}{"crosvm": map[string]interface{}{"vhost_user_vsock": "false"}}}}}},
		{"env_config": map[string]interface{}{"instances": []interface{}{map[string]interface{}{"vm": map[string]interface{}{"qemu": map[string]interface{}{}}}}}},
		{"env_config": map[string]interface{}{"instances": []interface{}{map[string]interface{}{"vm": map[string]interface{}{"gem5": map[string]interface{}{}}}}}},
		{"env_config": map[string]interface{}{"instances": []interface{}{map[string]interface{}{"vm": map[string]interface{}{"crosvm": map[string]interface{}{"vhost_user_vsock": true}}}}}},
	} {
		driver := virtualtargetv1alpha1.DriverConfig{Name: "cuttlefish", Type: cuttlefishDriverType, Config: mustJSON(config)}
		if _, err := New("dev").EnrichExporterExport([]virtualtargetv1alpha1.DriverConfig{driver}, nil); err == nil {
			t.Errorf("accepted %v", config)
		}
	}
	drivers := testExporterSet().Spec.Template.Spec.Drivers
	if _, err := New("dev").EnrichExporterExport(nil, nil); err == nil {
		t.Fatal("accepted missing Cuttlefish driver")
	}
	if _, err := New("dev").EnrichExporterExport(append(drivers, drivers[0]), nil); err == nil {
		t.Fatal("accepted multiple Cuttlefish drivers")
	}
	if _, err := New("dev").EnrichExporterExport(drivers, map[string]interface{}{"vm_memory_mb": 12.5}); err == nil {
		t.Fatal("accepted fractional VM memory")
	}
	enriched, err := New("dev").EnrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}
	config := configFor(t, enriched[0])
	vm := config["env_config"].(map[string]interface{})["instances"].([]interface{})[0].(map[string]interface{})["vm"].(map[string]interface{})
	if config["managed"] != true || vm["crosvm"].(map[string]interface{})["vhost_user_vsock"] != "true" {
		t.Fatalf("missing managed isolation: %v", config)
	}
}

func TestRuntimeMemory(t *testing.T) {
	for _, tc := range []struct {
		name, memory, request, limit, want string
		invalid                            bool
	}{
		{name: "default", want: "10Gi"},
		{name: "small limit", limit: "1Gi", invalid: true},
		{name: "small request", request: "8Gi", invalid: true},
		{name: "override", memory: "16384", want: "18Gi"},
		{name: "override limit", memory: "16384", limit: "10Gi", invalid: true},
		{name: "request exceeds limit", request: "12Gi", limit: "10Gi", invalid: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			es := testExporterSet()
			if tc.memory != "" {
				es.Spec.Template.Spec.Drivers[0].Config = &apiextensionsv1.JSON{Raw: []byte(`{"env_config":{"instances":[{"vm":{"memory_mb":` + tc.memory + `}}]}}`)}
			}
			resources := &corev1.ResourceRequirements{Requests: corev1.ResourceList{}, Limits: corev1.ResourceList{}}
			if tc.request != "" {
				resources.Requests[corev1.ResourceMemory] = resource.MustParse(tc.request)
			}
			if tc.limit != "" {
				resources.Limits[corev1.ResourceMemory] = resource.MustParse(tc.limit)
			}
			vtc := &virtualtargetv1alpha1.VirtualTargetClass{Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{Scheduling: &virtualtargetv1alpha1.SchedulingSpec{Resources: resources}}}
			pod, err := New("dev").RenderPod(context.Background(), es, vtc, map[string]interface{}{"runtime_privileged": true, "service_account_name": "cuttlefish-runtime", "fetch_images": true}, nil, nil)
			if tc.invalid {
				if err == nil {
					t.Fatal("accepted invalid resources")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			for _, c := range pod.Spec.InitContainers {
				if c.Name == "cuttlefish" && c.Resources.Requests.Memory().Cmp(resource.MustParse(tc.want)) != 0 {
					t.Fatalf("memory = %s, want %s", c.Resources.Requests.Memory(), tc.want)
				}
			}
		})
	}
}

func TestPodIsolation(t *testing.T) {
	es := testExporterSet()
	pod := renderTestPod(t, map[string]interface{}{"fetch_images": true})
	policy := New("dev").RenderNetworkPolicy(es)
	if policy.Spec.PodSelector.MatchLabels[isolationLabel] != pod.Labels[isolationLabel] || len(policy.Spec.Ingress) != 0 || len(policy.Spec.PolicyTypes) != 1 || policy.Spec.PolicyTypes[0] != "Ingress" {
		t.Fatalf("incorrect isolation policy: %#v", policy.Spec)
	}
	if pod.Spec.ServiceAccountName != "cuttlefish-runtime" || pod.Spec.AutomountServiceAccountToken == nil || *pod.Spec.AutomountServiceAccountToken {
		t.Fatal("workload service account not isolated")
	}
	for _, volume := range pod.Spec.Volumes {
		if volume.HostPath != nil && volume.HostPath.Path == "/dev/vhost-vsock" {
			t.Fatal("kernel VSOCK device mounted")
		}
	}
	for _, params := range []map[string]interface{}{
		{"fetch_images": true, "service_account_name": "cuttlefish-runtime", "runtime_privileged": false},
		{"fetch_images": true, "runtime_privileged": true},
		{"fetch_images": true, "runtime_privileged": true, "service_account_name": "default"},
		{"fetch_images": true, "runtime_privileged": true, "service_account_name": "Invalid_Name"},
	} {
		if _, err := New("dev").RenderPod(context.Background(), es, &virtualtargetv1alpha1.VirtualTargetClass{}, params, nil, nil); err == nil {
			t.Fatalf("accepted %v", params)
		}
	}
}

func TestRelaySupervisorExitsWhenEitherRelayFails(t *testing.T) {
	pod := renderTestPod(t, map[string]interface{}{"fetch_images": true})
	var command []string
	for _, c := range pod.Spec.InitContainers {
		if c.Name == "cuttlefish-relay" {
			command = c.Command
		}
	}
	for _, failingPort := range []int{netsimRelayPort, hciRelayPort} {
		t.Run(fmt.Sprint(failingPort), func(t *testing.T) {
			dir := t.TempDir()
			script := fmt.Sprintf("#!/bin/sh\ncase \"$1\" in TCP-LISTEN:%d,*) exit 1;; esac\nexec sleep 30\n", failingPort)
			if err := os.WriteFile(filepath.Join(dir, "socat"), []byte(script), 0755); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, command[0], command[1:]...)
			cmd.Env = append(os.Environ(), "PATH="+dir+":"+os.Getenv("PATH"))
			cmd.WaitDelay = time.Second
			err := cmd.Run()
			if err == nil || ctx.Err() != nil {
				t.Fatalf("supervisor did not promptly fail: %v, %v", err, ctx.Err())
			}
		})
	}
}
