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

// Package cuttlefish implements the cuttlefish.jumpstarter.dev provisioner.
// Each exporter Pod owns one Cuttlefish runtime and one CVD.
package cuttlefish

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"math"
	"slices"
	"strings"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/validation"
)

const (
	ProvisionerName = "cuttlefish.jumpstarter.dev"

	DefaultExporterImage = "quay.io/jumpstarter-dev/jumpstarter:latest"
	DefaultRuntimeImage  = "us-docker.pkg.dev/android-cuttlefish-artifacts/cuttlefish-orchestration/cuttlefish-orchestration:stable"
	DefaultRelayImage    = "docker.io/alpine/socat:latest"

	exporterConfigPath       = "/etc/jumpstarter/exporters/config.yaml"
	exporterNonRootUID int64 = 65532

	cuttlefishDriverType = "jumpstarter_driver_cuttlefish.driver.Cuttlefish"
	netsimDriverType     = "jumpstarter_driver_netsim.driver.Netsim"
	btPeerDriverType     = "jumpstarter_driver_bt_peer.driver.BtPeer"

	// The orchestration image reserves 2080 for nginx when running in a Pod;
	// Host Orchestrator consequently listens on 2081. Keep this configurable.
	hostOrchestratorPort = 2081
	netsimRelayPort      = 17681
	hciRelayPort         = 17300
	defaultGPUMode       = "guest_swiftshader"
	defaultVMCPUs        = 4
	defaultVMMemoryMB    = 8192
	isolationLabel       = "cuttlefish.jumpstarter.dev/exporter-set"
	healthStatePath      = "/tmp/jumpstarter-cuttlefish-health.json"
	runtimeIDPath        = "/run/cuttlefish-runtime/runtime-id"

	fetchPath      = "/home/vsoc-01/fetch"
	cvdStatePath   = "/var/tmp/cvd"
	androidTmpPath = "/tmp/android"
)

type Provisioner struct {
	Version string
}

type storageConfig struct {
	imageClaim                            string
	fetchImages                           bool
	imageSize, stateSize, tmpSize, budget resource.Quantity
	build                                 string
}

type runtimeConfig struct {
	relayImage          string
	netsimPort, hciPort int
	privileged          bool
	serviceAccount      string
}

func New(version string) *Provisioner {
	return &Provisioner{Version: version}
}

func (p *Provisioner) Name() string {
	return ProvisionerName
}

func (p *Provisioner) resolveImage(image string) string {
	if p.Version == "" || p.Version == "dev" || strings.Contains(p.Version, "-g") {
		return image
	}
	version := strings.TrimPrefix(p.Version, "v")
	if base, ok := strings.CutSuffix(image, ":latest"); ok {
		return base + ":" + version
	}
	return image
}

func (p *Provisioner) resolveImageSpec(spec *virtualtargetv1alpha1.ImageSpec, defaultImage string) (string, corev1.PullPolicy) {
	image := p.resolveImage(defaultImage)
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

func resolveStorageConfig(parameters map[string]interface{}) (storageConfig, error) {
	config := storageConfig{}
	config.imageClaim, _ = parameters["image_volume_claim"].(string)
	config.fetchImages, _ = parameters["fetch_images"].(bool)
	if config.imageClaim != "" && config.fetchImages {
		return config, fmt.Errorf("cuttlefish requires either image_volume_claim or fetch_images, not both")
	}
	if config.imageClaim == "" && !config.fetchImages {
		return config, fmt.Errorf("cuttlefish requires image_volume_claim or fetch_images=true")
	}
	if readOnly, _ := parameters["image_volume_read_only"].(bool); readOnly && config.imageClaim == "" {
		return config, fmt.Errorf("image_volume_read_only requires image_volume_claim")
	}

	var err error
	config.imageSize, config.stateSize, config.tmpSize, err = storageSizes(parameters)
	if err != nil {
		return config, err
	}
	config.budget = config.imageSize.DeepCopy()
	config.budget.Add(config.stateSize)
	config.budget.Add(config.tmpSize)
	config.budget.Add(resource.MustParse("1Gi")) // Container layers and logs need space beyond the volume budgets.
	if config.fetchImages {
		config.build = resolveDefaultBuild(parameters)
	}
	return config, nil
}

func resolveRuntimeConfig(parameters map[string]interface{}) (runtimeConfig, error) {
	config := runtimeConfig{relayImage: DefaultRelayImage}
	if value, ok := parameters["relay_image"].(string); ok && value != "" {
		config.relayImage = value
	}

	var err error
	config.netsimPort, config.hciPort, err = relayPorts(parameters)
	if err != nil {
		return config, err
	}
	configured := false
	config.privileged, configured = parameterBool(parameters, "runtime_privileged")
	if !configured || !config.privileged {
		return config, fmt.Errorf("cuttlefish requires runtime_privileged=true; unprivileged device access is not supported")
	}

	config.serviceAccount, _ = parameters["service_account_name"].(string)
	if config.serviceAccount == "" || config.serviceAccount == "default" || len(validation.IsDNS1123Subdomain(config.serviceAccount)) != 0 {
		return config, fmt.Errorf("service_account_name must name a dedicated workload service account")
	}
	return config, nil
}

func (p *Provisioner) RenderPod(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	mergedParameters map[string]interface{},
	images *virtualtargetv1alpha1.ImageOverrides,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (*corev1.Pod, error) {
	_ = ctx
	if exporterSet.Spec.RecycleStrategy == virtualtargetv1alpha1.RecycleStrategyInPlaceReuse {
		return nil, fmt.Errorf("managed Cuttlefish requires ExitAndReplace recycling")
	}

	var exporterSpec, runtimeSpec *virtualtargetv1alpha1.ImageSpec
	if images != nil {
		exporterSpec = images.Exporter
		runtimeSpec = images.Runtime
	}
	exporterImage, exporterPullPolicy := p.resolveImageSpec(exporterSpec, DefaultExporterImage)
	runtimeImage, runtimePullPolicy := p.resolveImageSpec(runtimeSpec, DefaultRuntimeImage)
	storage, err := resolveStorageConfig(mergedParameters)
	if err != nil {
		return nil, err
	}
	runtime, err := resolveRuntimeConfig(mergedParameters)
	if err != nil {
		return nil, err
	}
	drivers, err := p.EnrichExporterExport(exporterSet.Spec.Template.Spec.Drivers, mergedParameters)
	if err != nil {
		return nil, err
	}
	runtimeResources := corev1.ResourceRequirements{}
	if vtc.Spec.Scheduling != nil && vtc.Spec.Scheduling.Resources != nil {
		runtimeResources = *vtc.Spec.Scheduling.Resources.DeepCopy()
	}
	if err := reserveRuntimeResources(&runtimeResources, drivers, mergedParameters); err != nil {
		return nil, err
	}

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

	if podMeta.Labels == nil {
		podMeta.Labels = map[string]string{}
	}
	podMeta.Labels[isolationLabel] = string(exporterSet.UID)

	runtimeRestart := corev1.ContainerRestartPolicyAlways
	runAsRoot := int64(0)
	runAsExporter := exporterNonRootUID
	runAsNonRoot := true

	volumeMounts := []corev1.VolumeMount{
		{Name: "cvd-images", MountPath: fetchPath, ReadOnly: false},
		{Name: "cvd-state", MountPath: cvdStatePath},
		{Name: "android-tmp", MountPath: androidTmpPath},
	}
	deviceMounts := []corev1.VolumeMount{
		{Name: "kvm", MountPath: "/dev/kvm"},
		{Name: "vhost-net", MountPath: "/dev/vhost-net"},
		{Name: "tun", MountPath: "/dev/net/tun"},
	}

	exporterContainer := corev1.Container{
		Name:            "exporter",
		VolumeMounts:    []corev1.VolumeMount{{Name: "cvd-state", MountPath: "/run/cuttlefish-runtime", ReadOnly: true}},
		Image:           exporterImage,
		ImagePullPolicy: exporterPullPolicy,
		Command: []string{"python3", "-m", "jumpstarter_driver_cuttlefish.health", "--run-exporter",
			healthStatePath, runtimeIDPath, "http://127.0.0.1:2081", exporterConfigPath},
		Env: []corev1.EnvVar{{
			Name:  "HOME",
			Value: "/tmp",
		}},
		SecurityContext: &corev1.SecurityContext{
			RunAsUser:    &runAsExporter,
			RunAsNonRoot: &runAsNonRoot,
		},
	}
	if exporter != nil {
		exporterContainer.Env = append(exporterContainer.Env, corev1.EnvVar{
			Name:  "JUMPSTARTER_EXEC_LOG_FIELDS",
			Value: fmt.Sprintf("component=exporter,exporter=%s,namespace=%s", exporter.Name, exporter.Namespace),
		})
	}

	imageVolume := corev1.Volume{Name: "cvd-images", VolumeSource: corev1.VolumeSource{
		EmptyDir: &corev1.EmptyDirVolumeSource{SizeLimit: &storage.imageSize},
	}}
	permissionCommand := "mkdir -p /var/tmp/cvd /tmp/android && chown -R httpcvd:httpcvd /var/tmp/cvd /tmp/android /home/vsoc-01/fetch"

	initContainers := make([]corev1.Container, 0, 4)
	if storage.fetchImages {
		initContainers = append(initContainers, corev1.Container{
			Name:            "fetch-images",
			Image:           runtimeImage,
			ImagePullPolicy: runtimePullPolicy,
			Command:         []string{"cvd", "fetch", "--default_build=" + storage.build, "--target_directory=" + fetchPath},
			VolumeMounts:    []corev1.VolumeMount{{Name: "cvd-images", MountPath: fetchPath, ReadOnly: false}},
		})
	}
	initContainers = append(initContainers,
		corev1.Container{
			Name:            "fix-cuttlefish-permissions",
			Image:           runtimeImage,
			ImagePullPolicy: runtimePullPolicy,
			Command: []string{
				"bash", "-c", permissionCommand,
			},
			VolumeMounts: volumeMounts,
		},
		corev1.Container{
			Name:            "cuttlefish",
			Image:           runtimeImage,
			ImagePullPolicy: runtimePullPolicy,
			RestartPolicy:   &runtimeRestart,
			Command: []string{"bash", "-ec", `cat /proc/sys/kernel/random/uuid > /var/tmp/cvd/runtime-id
chmod 644 /var/tmp/cvd/runtime-id
exec /root/run_services.sh`},
			Resources:       runtimeResources,
			SecurityContext: &corev1.SecurityContext{Privileged: boolPtr(runtime.privileged), RunAsUser: &runAsRoot},
			VolumeMounts:    slices.Concat(volumeMounts, deviceMounts),
		},
		corev1.Container{
			Name:            "cuttlefish-relay",
			Image:           runtime.relayImage,
			ImagePullPolicy: corev1.PullIfNotPresent,
			RestartPolicy:   &runtimeRestart,
			Command: []string{
				"sh", "-c",
				fmt.Sprintf(
					`socat TCP-LISTEN:%d,bind=127.0.0.1,fork,reuseaddr TCP:127.0.0.1:7681 &
first=$!
socat TCP-LISTEN:%d,bind=127.0.0.1,fork,reuseaddr TCP:127.0.0.1:7300 &
second=$!
trap 'kill $first $second 2>/dev/null || true' EXIT
while kill -0 $first && kill -0 $second; do sleep 1; done
exit 1`,
					runtime.netsimPort, runtime.hciPort,
				),
			},
		},
	)

	pod := &corev1.Pod{
		ObjectMeta: podMeta,
		Spec: corev1.PodSpec{
			RestartPolicy:                corev1.RestartPolicyNever,
			ServiceAccountName:           runtime.serviceAccount,
			AutomountServiceAccountToken: boolPtr(false),
			InitContainers:               initContainers,
			Containers:                   []corev1.Container{exporterContainer},
			Volumes: []corev1.Volume{
				imageVolume,
				{Name: "cvd-state", VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{SizeLimit: &storage.stateSize}}},
				{Name: "android-tmp", VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{SizeLimit: &storage.tmpSize}}},
				deviceVolume("kvm", "/dev/kvm"),
				deviceVolume("vhost-net", "/dev/vhost-net"),
				deviceVolume("tun", "/dev/net/tun"),
			},
		},
	}

	if vtc.Spec.Scheduling != nil {
		if vtc.Spec.Scheduling.NodeSelector != nil {
			pod.Spec.NodeSelector = maps.Clone(vtc.Spec.Scheduling.NodeSelector)
		}
		if vtc.Spec.Scheduling.Tolerations != nil {
			pod.Spec.Tolerations = append([]corev1.Toleration(nil), vtc.Spec.Scheduling.Tolerations...)
		}
	}

	if storage.imageClaim != "" {
		pod.Spec.Volumes = append(pod.Spec.Volumes, corev1.Volume{Name: "image-source", VolumeSource: corev1.VolumeSource{
			PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: storage.imageClaim, ReadOnly: true},
		}})
		copyImages := corev1.Container{
			Name: "copy-images", Image: runtimeImage, ImagePullPolicy: runtimePullPolicy,
			Command: []string{"bash", "-ec", "cp -a --reflink=auto /image-source/. /home/vsoc-01/fetch/"},
			VolumeMounts: []corev1.VolumeMount{
				{Name: "image-source", MountPath: "/image-source", ReadOnly: true},
				{Name: "cvd-images", MountPath: fetchPath},
			},
		}
		pod.Spec.InitContainers = append([]corev1.Container{copyImages}, pod.Spec.InitContainers...)
	}
	for i := range pod.Spec.InitContainers {
		container := &pod.Spec.InitContainers[i]
		if container.Name == "cuttlefish" || container.Name == "fetch-images" || container.Name == "copy-images" {
			if err := reserveStorage(&container.Resources, storage.budget); err != nil {
				return nil, err
			}
		}
	}
	// Run in the exporter image so the check uses the same network namespace and Python runtime as jmp.
	healthURL := fmt.Sprintf("http://127.0.0.1:%d/_debug/statusz", parameterInt(mergedParameters, "host_orchestrator_port", hostOrchestratorPort))
	healthCheck := "import urllib.request; urllib.request.urlopen(" + fmt.Sprintf("%q", healthURL) + ", timeout=3).close()"
	pod.Spec.InitContainers = append(pod.Spec.InitContainers, corev1.Container{
		Name: "wait-for-cuttlefish", Image: exporterImage, ImagePullPolicy: exporterPullPolicy,
		SecurityContext: exporterContainer.SecurityContext.DeepCopy(),
		Command:         []string{"python3", "-c", "import time, urllib.request\nfor attempt in range(60):\n try:\n  " + healthCheck + "\n  break\n except Exception:\n  time.sleep(5)\nelse:\n raise SystemExit('Host Orchestrator did not become ready')"},
	})
	// With restartPolicy Never, a failed liveness check ends the exporter and lets ExitAndReplace recycle the Pod.
	pod.Spec.Containers[0].LivenessProbe = &corev1.Probe{
		ProbeHandler:  corev1.ProbeHandler{Exec: &corev1.ExecAction{Command: []string{"python3", "-m", "jumpstarter_driver_cuttlefish.health", healthStatePath}}},
		PeriodSeconds: 10, TimeoutSeconds: 10, FailureThreshold: 6,
	}

	return pod, nil
}

func (p *Provisioner) EnrichExporterExport(
	drivers []virtualtargetv1alpha1.DriverConfig,
	mergedParameters map[string]interface{},
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	netsimPort, hciPort, err := relayPorts(mergedParameters)
	if err != nil {
		return nil, err
	}
	count := 0
	for _, driver := range drivers {
		if driver.Type == cuttlefishDriverType {
			count++
		}
	}
	if count != 1 {
		return nil, fmt.Errorf("cuttlefish requires exactly one Cuttlefish driver per Pod, got %d", count)
	}
	result := make([]virtualtargetv1alpha1.DriverConfig, 0, len(drivers))
	for _, driver := range drivers {
		var err error
		switch driver.Type {
		case cuttlefishDriverType:
			driver, err = enrichCuttlefishDriver(driver, mergedParameters)
		case netsimDriverType:
			driver, err = enrichDriverConfig(driver, map[string]interface{}{
				"host": "127.0.0.1",
				"port": netsimPort,
			}, "netsim")
		case btPeerDriverType:
			driver, err = enrichDriverConfig(driver, map[string]interface{}{
				"transport": fmt.Sprintf("tcp-client:127.0.0.1:%d", hciPort),
			}, "bt_peer")
		}
		if err != nil {
			return nil, err
		}
		result = append(result, driver)
	}
	return result, nil
}

func enrichCuttlefishDriver(driver virtualtargetv1alpha1.DriverConfig, parameters map[string]interface{}) (virtualtargetv1alpha1.DriverConfig, error) {
	for _, item := range []struct {
		key      string
		fallback int
	}{{"vm_cpus", defaultVMCPUs}, {"vm_memory_mb", defaultVMMemoryMB}} {
		if _, err := positiveInt(parameters, item.key, item.fallback); err != nil {
			return driver, err
		}
	}
	config, err := decodeConfig(driver, "Cuttlefish")
	if err != nil {
		return driver, err
	}

	config["managed"] = true
	config["health_state_path"] = healthStatePath
	config["runtime_id_path"] = runtimeIDPath
	config["health_ports"] = []int{7681, 7300, parameterInt(parameters, "netsim_relay_port", netsimRelayPort), parameterInt(parameters, "hci_relay_port", hciRelayPort)}
	setDefault(config, "scheme", "http")
	setDefault(config, "host", "127.0.0.1")
	setDefault(config, "port", parameterInt(parameters, "host_orchestrator_port", hostOrchestratorPort))
	setDefault(config, "group", "cvd")
	setDefault(config, "name", "1")
	setDefault(config, "instance_num", 1)
	setDefault(config, "boot_timeout", 300)
	if err := validateManagedEndpoint(config); err != nil {
		return driver, err
	}

	envConfig, err := configObject(config, "env_config")
	if err != nil {
		return driver, err
	}
	common, err := configObject(envConfig, "common")
	if err != nil {
		return driver, err
	}
	setDefault(common, "host_package", fetchPath)
	envConfig["common"] = common
	instances, ok := envConfig["instances"].([]interface{})
	if raw, exists := envConfig["instances"]; exists && (!ok || len(instances) != 1) {
		return driver, fmt.Errorf("env_config.instances must contain exactly one instance, got %v", raw)
	}
	if len(instances) == 0 {
		instances = []interface{}{map[string]interface{}{}}
	}
	instance, ok := instances[0].(map[string]interface{})
	if !ok || instance == nil {
		return driver, fmt.Errorf("env_config.instances[0] must be an object")
	}
	disk, err := configObject(instance, "disk")
	if err != nil {
		return driver, err
	}
	setDefault(disk, "default_build", fetchPath)
	instance["disk"] = disk
	graphics, err := configObject(instance, "graphics")
	if err != nil {
		return driver, err
	}
	gpuMode := defaultGPUMode
	if configuredGPU, ok := parameters["gpu_mode"].(string); ok && configuredGPU != "" {
		gpuMode = configuredGPU
	}
	setDefault(graphics, "gpu_mode", gpuMode)
	instance["graphics"] = graphics
	vm, err := configObject(instance, "vm")
	if err != nil {
		return driver, err
	}
	if _, exists := vm["qemu"]; exists {
		return driver, fmt.Errorf("managed Cuttlefish requires crosvm with private userspace VSOCK")
	}
	if _, exists := vm["gem5"]; exists {
		return driver, fmt.Errorf("managed Cuttlefish requires crosvm with private userspace VSOCK")
	}
	crosvm, err := configObject(vm, "crosvm")
	if err != nil {
		return driver, err
	}
	if value, exists := crosvm["vhost_user_vsock"]; exists && value != "true" {
		return driver, fmt.Errorf("vm.crosvm.vhost_user_vsock must be the string true")
	}
	crosvm["vhost_user_vsock"] = "true"
	vm["crosvm"] = crosvm
	if value, exists := envConfig["netsim_bt"]; exists && value != true {
		return driver, fmt.Errorf("managed Cuttlefish requires netsim_bt=true; standalone RootCanal is not supported")
	}
	envConfig["netsim_bt"] = true
	setDefault(vm, "cpus", parameterInt(parameters, "vm_cpus", defaultVMCPUs))
	setDefault(vm, "memory_mb", parameterInt(parameters, "vm_memory_mb", defaultVMMemoryMB))
	if _, err := positiveInt(vm, "cpus", defaultVMCPUs); err != nil {
		return driver, err
	}
	if _, err := positiveInt(vm, "memory_mb", defaultVMMemoryMB); err != nil {
		return driver, err
	}
	instance["vm"] = vm
	instances[0] = instance
	envConfig["instances"] = instances
	config["env_config"] = envConfig

	return encodeConfig(driver, config)
}

func validateManagedEndpoint(config map[string]interface{}) error {
	for key, required := range map[string]interface{}{"scheme": "http", "host": "127.0.0.1"} {
		if config[key] != required {
			return fmt.Errorf("managed Cuttlefish requires %s=%v", key, required)
		}
	}
	if port, err := positiveInt(config, "port", hostOrchestratorPort); err != nil || port != hostOrchestratorPort {
		return fmt.Errorf("managed Cuttlefish requires port=%d", hostOrchestratorPort)
	}
	if instanceNum, err := positiveInt(config, "instance_num", 1); err != nil || instanceNum != 1 {
		return fmt.Errorf("managed Cuttlefish requires instance_num=1")
	}
	return nil
}

func configObject(parent map[string]interface{}, key string) (map[string]interface{}, error) {
	raw, exists := parent[key]
	if !exists {
		return map[string]interface{}{}, nil
	}
	value, ok := raw.(map[string]interface{})
	if !ok || value == nil {
		return nil, fmt.Errorf("%s must be an object", key)
	}
	return value, nil
}

func enrichDriverConfig(driver virtualtargetv1alpha1.DriverConfig, defaults map[string]interface{}, name string) (virtualtargetv1alpha1.DriverConfig, error) {
	config, err := decodeConfig(driver, name)
	if err != nil {
		return driver, err
	}
	for key, value := range defaults {
		setDefault(config, key, value)
	}
	return encodeConfig(driver, config)
}

func decodeConfig(driver virtualtargetv1alpha1.DriverConfig, name string) (map[string]interface{}, error) {
	config := map[string]interface{}{}
	if driver.Config != nil && driver.Config.Raw != nil {
		if err := json.Unmarshal(driver.Config.Raw, &config); err != nil {
			return nil, fmt.Errorf("unmarshal %s driver config: %w", name, err)
		}
	}
	if config == nil {
		return nil, fmt.Errorf("%s config must be an object", name)
	}
	return config, nil
}

func encodeConfig(driver virtualtargetv1alpha1.DriverConfig, config map[string]interface{}) (virtualtargetv1alpha1.DriverConfig, error) {
	raw, err := json.Marshal(config)
	if err != nil {
		return driver, fmt.Errorf("marshal driver config: %w", err)
	}
	driver.Config = &apiextensionsv1.JSON{Raw: raw}
	return driver, nil
}

func resolveDefaultBuild(parameters map[string]interface{}) string {
	if value, ok := parameters["default_build"].(string); ok && value != "" {
		return value
	}
	return "aosp-android-latest-release/aosp_cf_x86_64_auto-userdebug"
}

func relayPorts(parameters map[string]interface{}) (int, int, error) {
	ports := []int{netsimRelayPort, hciRelayPort}
	reserved := map[int]bool{
		80: true, 443: true, 1080: true, 1443: true, 2080: true, 2081: true, 2443: true,
		7300: true, 7301: true, 7302: true, 7303: true, 7681: true, 15037: true, 19531: true,
	}
	if port, err := positiveInt(parameters, "host_orchestrator_port", hostOrchestratorPort); err != nil || port != hostOrchestratorPort {
		return 0, 0, fmt.Errorf("host_orchestrator_port must be %d for the orchestration image", hostOrchestratorPort)
	}
	for i, key := range []string{"netsim_relay_port", "hci_relay_port"} {
		port, err := positiveInt(parameters, key, ports[i])
		if err != nil || port > 65535 || reserved[port] || (port >= 6520 && port <= 6620) || (port >= 15550 && port <= 15560) {
			return 0, 0, fmt.Errorf("%s must be an integer port in 1..65535 that does not conflict with a runtime service", key)
		}
		ports[i] = port
		reserved[port] = true
	}
	return ports[0], ports[1], nil
}

func positiveInt(values map[string]interface{}, key string, fallback int) (int, error) {
	raw, exists := values[key]
	if !exists {
		return fallback, nil
	}
	var value float64
	switch v := raw.(type) {
	case int:
		value = float64(v)
	case int32:
		value = float64(v)
	case int64:
		value = float64(v)
	case float64:
		value = v
	default:
		return 0, fmt.Errorf("%s must be a positive integer", key)
	}
	if math.IsNaN(value) || math.IsInf(value, 0) || value < 1 || value > math.MaxInt32 || math.Trunc(value) != value {
		return 0, fmt.Errorf("%s must be a positive integer", key)
	}
	return int(value), nil
}

func reserveRuntimeResources(resources *corev1.ResourceRequirements, drivers []virtualtargetv1alpha1.DriverConfig, parameters map[string]interface{}) error {
	var vm map[string]interface{}
	for _, driver := range drivers {
		if driver.Type != cuttlefishDriverType {
			continue
		}
		config, err := decodeConfig(driver, "Cuttlefish")
		if err != nil {
			return err
		}
		vm = config["env_config"].(map[string]interface{})["instances"].([]interface{})[0].(map[string]interface{})["vm"].(map[string]interface{})
	}
	memory, err := positiveInt(vm, "memory_mb", defaultVMMemoryMB)
	if err != nil {
		return err
	}
	cpus, err := positiveInt(vm, "cpus", defaultVMCPUs)
	if err != nil {
		return err
	}
	overhead, err := positiveInt(parameters, "runtime_memory_overhead_mb", 2048)
	if err != nil {
		return err
	}
	budget := *resource.NewQuantity((int64(memory)+int64(overhead))*1024*1024, resource.BinarySI)
	if resources.Requests == nil {
		resources.Requests = corev1.ResourceList{}
	}
	for _, values := range []corev1.ResourceList{resources.Requests, resources.Limits} {
		if value, exists := values[corev1.ResourceMemory]; exists && value.Cmp(budget) < 0 {
			return fmt.Errorf("runtime memory must be at least %s for guest plus overhead", budget.String())
		}
	}
	if _, exists := resources.Requests[corev1.ResourceMemory]; !exists {
		resources.Requests[corev1.ResourceMemory] = budget
	}
	if limit, exists := resources.Limits[corev1.ResourceMemory]; exists && resources.Requests.Memory().Cmp(limit) > 0 {
		return fmt.Errorf("runtime memory request exceeds limit")
	}
	if _, exists := resources.Requests[corev1.ResourceCPU]; !exists {
		if limit, exists := resources.Limits[corev1.ResourceCPU]; exists {
			resources.Requests[corev1.ResourceCPU] = limit.DeepCopy()
		} else {
			resources.Requests[corev1.ResourceCPU] = *resource.NewQuantity(int64(cpus), resource.DecimalSI)
		}
	}

	if resources.Requests.Cpu().Sign() <= 0 {
		return fmt.Errorf("runtime CPU request must be positive")
	}
	if limit, exists := resources.Limits[corev1.ResourceCPU]; exists && resources.Requests.Cpu().Cmp(limit) > 0 {
		return fmt.Errorf("runtime CPU request exceeds limit")
	}
	return nil
}

func (p *Provisioner) RenderNetworkPolicy(es *virtualtargetv1alpha1.ExporterSet) *networkingv1.NetworkPolicy {
	return &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{Name: "cuttlefish-" + string(es.UID), Namespace: es.Namespace},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{MatchLabels: map[string]string{isolationLabel: string(es.UID)}},
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeIngress},
		},
	}
}

func parameterInt(parameters map[string]interface{}, key string, fallback int) int {
	switch value := parameters[key].(type) {
	case int:
		return value
	case int32:
		return int(value)
	case int64:
		return int(value)
	case float64:
		return int(value)
	default:
		return fallback
	}
}

func parameterBool(parameters map[string]interface{}, key string) (bool, bool) {
	value, ok := parameters[key].(bool)
	return value, ok
}

func setDefault(config map[string]interface{}, key string, value interface{}) {
	if _, exists := config[key]; !exists {
		config[key] = value
	}
}

func deviceVolume(name, path string) corev1.Volume {
	typeCharDevice := corev1.HostPathCharDev
	return corev1.Volume{
		Name: name,
		VolumeSource: corev1.VolumeSource{HostPath: &corev1.HostPathVolumeSource{
			Path: path,
			Type: &typeCharDevice,
		}},
	}
}

func boolPtr(value bool) *bool {
	return &value
}

func (p *Provisioner) Cleanup(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	// Cuttlefish state and fetched images are Pod-scoped. Kubernetes removes
	// the Pod-owned emptyDir volumes, while a claimed image tree is external
	// and must outlive the exporter, so there is nothing for the provisioner
	// to clean up here.
	return nil
}

func storageSizes(parameters map[string]interface{}) (resource.Quantity, resource.Quantity, resource.Quantity, error) {
	sizes := []resource.Quantity{resource.MustParse("20Gi"), resource.MustParse("20Gi"), resource.MustParse("4Gi")}
	raw, exists := parameters["storage"]
	if !exists {
		return sizes[0], sizes[1], sizes[2], nil
	}
	storage, ok := raw.(map[string]interface{})
	if !ok {
		return sizes[0], sizes[1], sizes[2], fmt.Errorf("parameters.storage must be an object")
	}
	for i, key := range []string{"imageSize", "stateSize", "tmpSize"} {
		if value, exists := storage[key]; exists {
			valueString, ok := value.(string)
			quantity, err := resource.ParseQuantity(valueString)
			if !ok || err != nil || quantity.Sign() <= 0 {
				return sizes[0], sizes[1], sizes[2], fmt.Errorf("parameters.storage.%s must be a positive storage quantity", key)
			}
			sizes[i] = quantity
		}
	}
	return sizes[0], sizes[1], sizes[2], nil
}

func reserveStorage(resources *corev1.ResourceRequirements, budget resource.Quantity) error {
	if resources.Requests == nil {
		resources.Requests = corev1.ResourceList{}
	}
	if resources.Limits == nil {
		resources.Limits = corev1.ResourceList{}
	}
	for _, values := range []corev1.ResourceList{resources.Requests, resources.Limits} {
		if value, exists := values[corev1.ResourceEphemeralStorage]; exists && value.Cmp(budget) < 0 {
			return fmt.Errorf("ephemeral-storage must be at least %s for Cuttlefish volume budgets and overhead", budget.String())
		}
	}
	if _, exists := resources.Requests[corev1.ResourceEphemeralStorage]; !exists {
		resources.Requests[corev1.ResourceEphemeralStorage] = budget.DeepCopy()
	}
	if _, exists := resources.Limits[corev1.ResourceEphemeralStorage]; !exists {
		resources.Limits[corev1.ResourceEphemeralStorage] = resources.Requests[corev1.ResourceEphemeralStorage].DeepCopy()
	}
	request := resources.Requests[corev1.ResourceEphemeralStorage]
	if request.Cmp(resources.Limits[corev1.ResourceEphemeralStorage]) > 0 {
		return fmt.Errorf("ephemeral-storage request exceeds limit")
	}
	return nil
}
