package cuttlefish

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
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
	pod := renderTestPod(t, map[string]interface{}{"fetch_images": true})
	names := make([]string, 0, len(pod.Spec.InitContainers))
	for _, container := range pod.Spec.InitContainers {
		names = append(names, container.Name)
	}
	want := []string{"fetch-images", "fix-cuttlefish-permissions", runtimeContainerName, gateContainerName}
	if fmt.Sprint(names) != fmt.Sprint(want) {
		t.Fatalf("init containers = %v, want %v", names, want)
	}
	runtime := initContainer(t, pod, runtimeContainerName)
	if runtime.RestartPolicy == nil || *runtime.RestartPolicy != corev1.ContainerRestartPolicyAlways {
		t.Fatal("runtime must be a native sidecar")
	}
	if runtime.SecurityContext == nil || runtime.SecurityContext.Privileged == nil || !*runtime.SecurityContext.Privileged {
		t.Fatal("Cuttlefish runtime must be privileged")
	}
	if len(pod.Spec.Containers) != 1 || pod.Spec.Containers[0].Name != "exporter" {
		t.Fatalf("containers = %#v", pod.Spec.Containers)
	}
	exporter := pod.Spec.Containers[0]
	if !hasEnv(exporter.Env, "HOME", "/tmp") {
		t.Errorf("exporter HOME = %#v, want /tmp", exporter.Env)
	}
	if exporter.Command[6] != hostOrchestratorURL {
		t.Errorf("exporter endpoint = %q", exporter.Command[6])
	}
	if pod.Spec.RestartPolicy != corev1.RestartPolicyNever {
		t.Error("Pod must not restart the exporter in place")
	}
	if !hasVolume(pod.Spec.Volumes, "kvm", "/dev/kvm") || !hasVolume(pod.Spec.Volumes, "tun", "/dev/net/tun") {
		t.Fatalf("device volumes missing: %#v", pod.Spec.Volumes)
	}
}

func TestRenderPod_rejectsFetchingIntoClaim(t *testing.T) {
	_, err := New("dev").RenderPod(context.Background(), testExporterSet(), &virtualtargetv1alpha1.VirtualTargetClass{}, map[string]interface{}{
		"fetch_images":         true,
		"image_volume_claim":   "cuttlefish-images",
		"runtime_privileged":   true,
		"service_account_name": "cuttlefish-runtime",
	}, nil, nil)
	if err == nil {
		t.Fatal("RenderPod() succeeded; want an error for fetch_images with image_volume_claim")
	}
}

func TestRenderPod_privateImageCopy(t *testing.T) {
	pod := renderTestPod(t, map[string]interface{}{"image_volume_claim": "images"})
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

func TestRenderPod_healthGate(t *testing.T) {
	pod := renderTestPod(t, map[string]interface{}{"fetch_images": true})
	gate := pod.Spec.InitContainers[len(pod.Spec.InitContainers)-1]
	if gate.Name != gateContainerName || gate.Command[3] != "--wait" || gate.Command[4] != hostOrchestratorURL {
		t.Fatalf("missing API startup gate: %#v", gate.Command)
	}
	if gate.Image != pod.Spec.Containers[0].Image {
		t.Fatal("gate must run in the exporter image")
	}
	probe := pod.Spec.Containers[0].LivenessProbe
	if probe == nil || probe.Exec.Command[2] != "jumpstarter_driver_cuttlefish.health" || probe.Exec.Command[3] != healthStatePath {
		t.Fatal("missing runtime failure detection")
	}
	if !hasMount(pod.Spec.Containers[0].VolumeMounts, "cvd-state", runtimeIDMount) {
		t.Fatal("exporter cannot read the runtime marker")
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
	if total.Cmp(resource.MustParse("15Gi")) != 0 {
		t.Fatalf("volume total = %s", total.String())
	}
	for _, name := range []string{"fetch-images", runtimeContainerName} {
		container := initContainer(t, pod, name)
		request := container.Resources.Requests[corev1.ResourceEphemeralStorage]
		limit := container.Resources.Limits[corev1.ResourceEphemeralStorage]
		if request.Cmp(total) != 0 || limit.Cmp(total) != 0 {
			t.Fatalf("%s storage does not cover volumes: %v", name, container.Resources)
		}
	}
}

func TestStorageValidation(t *testing.T) {
	for _, value := range []interface{}{"", "0", "-1Gi", "invalid", 42} {
		_, err := resolveStorageConfig(map[string]interface{}{"fetch_images": true, "storage": map[string]interface{}{"imageSize": value}})
		if err == nil {
			t.Fatalf("accepted invalid size %v", value)
		}
	}
	if _, err := resolveStorageConfig(map[string]interface{}{"fetch_images": true, "storage": "invalid"}); err == nil {
		t.Fatal("accepted non-object storage")
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
	if cuttlefish["host"] != "127.0.0.1" || cuttlefish["port"] != float64(hostOrchestratorPort) || cuttlefish["scheme"] != "http" {
		t.Errorf("Cuttlefish endpoint = %#v", cuttlefish)
	}
	if fmt.Sprint(cuttlefish["health_ports"]) != fmt.Sprint([]interface{}{float64(netsimPort), float64(hciPort)}) {
		t.Errorf("health_ports = %v", cuttlefish["health_ports"])
	}
	envConfig := cuttlefish["env_config"].(map[string]interface{})
	instance := envConfig["instances"].([]interface{})[0].(map[string]interface{})
	graphics := instance["graphics"].(map[string]interface{})
	if graphics["gpu_mode"] != "none" {
		t.Errorf("gpu_mode = %v", graphics["gpu_mode"])
	}
	vm := instance["vm"].(map[string]interface{})
	if vm["cpus"] != float64(defaultVMCPUs) || vm["memory_mb"] != float64(defaultVMMemoryMB) {
		t.Errorf("vm config = %#v", vm)
	}

	netsim := configFor(t, result[1])
	if netsim["host"] != "127.0.0.1" || netsim["port"] != float64(netsimPort) {
		t.Errorf("netsim config = %#v", netsim)
	}
	btPeer := configFor(t, result[2])
	if btPeer["transport"] != fmt.Sprintf("tcp-client:127.0.0.1:%d", hciPort) {
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
	// Restating the pinned values is fine, including as JSON numbers.
	driver := virtualtargetv1alpha1.DriverConfig{Name: "cuttlefish", Type: cuttlefishDriverType, Config: mustJSON(map[string]interface{}{
		"host": "127.0.0.1", "port": 2081.0, "instance_num": 1, "scheme": "http",
	})}
	if _, err := New("dev").EnrichExporterExport([]virtualtargetv1alpha1.DriverConfig{driver}, nil); err != nil {
		t.Fatal(err)
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
	for _, params := range []map[string]interface{}{{"vm_memory_mb": 12.5}, {"vm_cpus": "4"}, {"vm_cpus": 0}} {
		if _, err := New("dev").EnrichExporterExport(drivers, params); err == nil {
			t.Fatalf("accepted guest parameters %v", params)
		}
	}
	enriched, err := New("dev").EnrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}
	config := configFor(t, enriched[0])
	envConfig := config["env_config"].(map[string]interface{})
	vm := envConfig["instances"].([]interface{})[0].(map[string]interface{})["vm"].(map[string]interface{})
	if config["managed"] != true || envConfig["netsim_bt"] != true || vm["crosvm"].(map[string]interface{})["vhost_user_vsock"] != "true" {
		t.Fatalf("missing managed isolation: %v", config)
	}
}

func TestGuestSpecPrefersTemplateValues(t *testing.T) {
	driver := virtualtargetv1alpha1.DriverConfig{Name: "cuttlefish", Type: cuttlefishDriverType, Config: mustJSON(map[string]interface{}{
		"env_config": map[string]interface{}{"instances": []interface{}{map[string]interface{}{"vm": map[string]interface{}{"cpus": 2}}}},
	})}
	_, guest, err := enrichDrivers([]virtualtargetv1alpha1.DriverConfig{driver}, map[string]interface{}{"vm_cpus": 8, "vm_memory_mb": 4096})
	if err != nil {
		t.Fatal(err)
	}
	if guest != (guestSpec{cpus: 2, memoryMB: 4096}) {
		t.Fatalf("guest = %+v", guest)
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
			runtime := initContainer(t, pod, runtimeContainerName)
			if runtime.Resources.Requests.Memory().Cmp(resource.MustParse(tc.want)) != 0 {
				t.Fatalf("memory = %s, want %s", runtime.Resources.Requests.Memory(), tc.want)
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
		{"fetch_images": true, "service_account_name": "cuttlefish-runtime"},
		{"fetch_images": true, "runtime_privileged": true},
		{"fetch_images": true, "runtime_privileged": true, "service_account_name": "default"},
		{"fetch_images": true, "runtime_privileged": true, "service_account_name": "Invalid_Name"},
	} {
		if _, err := New("dev").RenderPod(context.Background(), es, &virtualtargetv1alpha1.VirtualTargetClass{}, params, nil, nil); err == nil {
			t.Fatalf("accepted %v", params)
		}
	}
	es.Spec.RecycleStrategy = virtualtargetv1alpha1.RecycleStrategyInPlaceReuse
	if _, err := New("dev").RenderPod(context.Background(), es, &virtualtargetv1alpha1.VirtualTargetClass{}, map[string]interface{}{
		"fetch_images": true, "runtime_privileged": true, "service_account_name": "cuttlefish-runtime",
	}, nil, nil); err == nil {
		t.Fatal("accepted InPlaceReuse")
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

func initContainer(t *testing.T, pod *corev1.Pod, name string) corev1.Container {
	t.Helper()
	for _, container := range pod.Spec.InitContainers {
		if container.Name == name {
			return container
		}
	}
	t.Fatalf("init container %q missing", name)
	return corev1.Container{}
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

func hasMount(mounts []corev1.VolumeMount, name, path string) bool {
	for _, mount := range mounts {
		if mount.Name == name && mount.MountPath == path {
			return true
		}
	}
	return false
}

func hasEnv(env []corev1.EnvVar, name, value string) bool {
	for _, variable := range env {
		if variable.Name == name && variable.Value == value {
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

func TestRenderPod_execBackend(t *testing.T) {
	pod := renderTestPod(t, map[string]interface{}{"fetch_images": true, "backend": "exec"})
	endpoint := "exec://httpcvd@" + launcherSocketPath

	copyExec := pod.Spec.InitContainers[0]
	if copyExec.Name != "copy-jumpstarter-exec" || copyExec.Command[1] != jmpExecBinaryPath ||
		copyExec.Command[2] != sharedMountPath+"/jumpstarter-exec" || copyExec.Image != pod.Spec.Containers[0].Image {
		t.Fatalf("jumpstarter-exec must be staged from the exporter image: %#v", copyExec)
	}
	runtime := initContainer(t, pod, runtimeContainerName)
	script := runtime.Command[2]
	for _, want := range []string{"runtime-id", "service cuttlefish-host-resources start\n", "service cuttlefish-operator start\n", "\ncd " + launcherWorkDir + "\n"} {
		if !strings.Contains(script, want) {
			t.Fatalf("runtime script lacks %q: %q", want, script)
		}
	}
	if strings.Contains(script, "run_services") || strings.Contains(script, "host_orchestrator") || strings.Contains(script, "nginx") {
		t.Fatalf("exec mode must not start Host Orchestrator or nginx: %q", script)
	}
	if !strings.HasSuffix(script, "exec "+sharedMountPath+"/jumpstarter-exec serve --socket "+launcherSocketPath) {
		t.Fatalf("launcher must be PID 1: %q", script)
	}
	if !hasMount(runtime.VolumeMounts, sharedVolumeName, sharedMountPath) {
		t.Fatalf("runtime lacks the shared volume: %#v", runtime.VolumeMounts)
	}
	gate := initContainer(t, pod, gateContainerName)
	if gate.Command[3] != "--wait" || gate.Command[4] != endpoint || !hasMount(gate.VolumeMounts, sharedVolumeName, sharedMountPath) {
		t.Fatalf("startup gate must wait on the launcher: %#v", gate)
	}

	exporter := pod.Spec.Containers[0]
	if exporter.Command[6] != endpoint {
		t.Fatalf("exporter endpoint = %q, want %q", exporter.Command[6], endpoint)
	}
	if !hasEnv(exporter.Env, "JUMPSTARTER_LAUNCHER_SOCKET", launcherSocketPath) || !hasMount(exporter.VolumeMounts, sharedVolumeName, sharedMountPath) {
		t.Fatalf("exporter must reach the launcher for shutdown: %#v", exporter)
	}

	total := resource.MustParse("1Gi")
	sharedFound := false
	for _, volume := range pod.Spec.Volumes {
		if volume.Name == sharedVolumeName {
			sharedFound = true
			if volume.EmptyDir == nil || volume.EmptyDir.SizeLimit == nil || volume.EmptyDir.SizeLimit.String() != sharedVolumeSizeLimit {
				t.Fatalf("shared volume must be a bounded emptyDir: %#v", volume)
			}
		}
		if volume.EmptyDir != nil {
			total.Add(*volume.EmptyDir.SizeLimit)
		}
	}
	if !sharedFound {
		t.Fatal("shared volume missing")
	}
	if request := runtime.Resources.Requests[corev1.ResourceEphemeralStorage]; request.Cmp(total) != 0 {
		t.Fatalf("storage budget %s does not include the shared volume (%s)", request.String(), total.String())
	}
}

func TestRenderPod_execBackendLogContext(t *testing.T) {
	exporter := &jumpstarterdevv1alpha1.Exporter{ObjectMeta: metav1.ObjectMeta{Name: "cf-1", Namespace: "lab"}}
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), &virtualtargetv1alpha1.VirtualTargetClass{}, map[string]interface{}{
		"fetch_images": true, "backend": "exec", "runtime_privileged": true, "service_account_name": "cuttlefish-runtime",
	}, nil, exporter)
	if err != nil {
		t.Fatal(err)
	}
	if pod.Name != "cf-1" {
		t.Fatalf("pod name = %q", pod.Name)
	}
	runtime := initContainer(t, pod, runtimeContainerName)
	if !hasEnv(runtime.Env, "JUMPSTARTER_EXEC_LOG_FIELDS", "component=exporter,exporter=cf-1,namespace=lab") {
		t.Fatalf("launcher log context missing: %#v", runtime.Env)
	}
}

func TestRenderPod_httpBackendHasNoLauncher(t *testing.T) {
	for _, params := range []map[string]interface{}{{"fetch_images": true}, {"fetch_images": true, "backend": "http"}} {
		pod := renderTestPod(t, params)
		for _, volume := range pod.Spec.Volumes {
			if volume.Name == sharedVolumeName {
				t.Fatal("http backend must not add the shared volume")
			}
		}
		exporter := pod.Spec.Containers[0]
		if exporter.Command[6] != hostOrchestratorURL || hasEnv(exporter.Env, "JUMPSTARTER_LAUNCHER_SOCKET", launcherSocketPath) {
			t.Fatalf("http backend must talk to Host Orchestrator only: %#v", exporter)
		}
		if pod.Spec.InitContainers[0].Name == "copy-jumpstarter-exec" {
			t.Fatal("http backend must not stage jumpstarter-exec")
		}
		if !strings.HasSuffix(initContainer(t, pod, runtimeContainerName).Command[2], "exec /root/run_services.sh") {
			t.Fatal("http backend must run the image services")
		}
	}
}

func TestBackendValidation(t *testing.T) {
	for _, params := range []map[string]interface{}{
		{"backend": "grpc"}, {"backend": 1}, {"backend": ""},
		{"backend": "exec", "cvd_user": ""}, {"backend": "exec", "cvd_user": "Bad User"}, {"backend": "exec", "cvd_user": 101},
	} {
		params["fetch_images"] = true
		params["runtime_privileged"] = true
		params["service_account_name"] = "cuttlefish-runtime"
		if _, err := New("dev").RenderPod(context.Background(), testExporterSet(), &virtualtargetv1alpha1.VirtualTargetClass{}, params, nil, nil); err == nil {
			t.Errorf("accepted %v", params)
		}
	}
}

func TestEnrichExporterExportBackends(t *testing.T) {
	driver := virtualtargetv1alpha1.DriverConfig{Name: "cuttlefish", Type: cuttlefishDriverType}
	enrich := func(d virtualtargetv1alpha1.DriverConfig, params map[string]interface{}) (map[string]interface{}, error) {
		result, err := New("dev").EnrichExporterExport([]virtualtargetv1alpha1.DriverConfig{d}, params)
		if err != nil {
			return nil, err
		}
		return configFor(t, result[0]), nil
	}

	config, err := enrich(driver, map[string]interface{}{"backend": "exec"})
	if err != nil {
		t.Fatal(err)
	}
	if config["launcher_socket"] != launcherSocketPath || config["cvd_user"] != defaultCvdUser || config["managed"] != true {
		t.Fatalf("exec backend config = %#v", config)
	}
	if config["host"] != "127.0.0.1" || config["scheme"] != "http" {
		t.Fatalf("exec backend must keep the in-Pod endpoint for WebRTC: %#v", config)
	}
	config, err = enrich(driver, map[string]interface{}{"backend": "exec", "cvd_user": "root"})
	if err != nil || config["cvd_user"] != "root" {
		t.Fatalf("cvd_user override not applied: %v %v", config["cvd_user"], err)
	}
	config, err = enrich(driver, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, exists := config["launcher_socket"]; exists {
		t.Fatal("http backend must not inject launcher_socket")
	}

	preset := virtualtargetv1alpha1.DriverConfig{Name: "cuttlefish", Type: cuttlefishDriverType, Config: mustJSON(map[string]interface{}{"launcher_socket": "/tmp/x.sock"})}
	if _, err := enrich(preset, nil); err == nil {
		t.Fatal("template-provided launcher_socket must be rejected outside the exec backend")
	}
	config, err = enrich(preset, map[string]interface{}{"backend": "exec"})
	if err != nil || config["launcher_socket"] != launcherSocketPath {
		t.Fatalf("exec backend must force the provisioner's launcher socket: %v %v", config["launcher_socket"], err)
	}
}
