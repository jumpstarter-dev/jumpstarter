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
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"math"
	"regexp"
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

	exporterConfigPath       = "/etc/jumpstarter/exporters/config.yaml"
	exporterNonRootUID int64 = 65532

	cuttlefishDriverType = "jumpstarter_driver_cuttlefish.driver.Cuttlefish"
	netsimDriverType     = "jumpstarter_driver_netsim.driver.Netsim"
	btPeerDriverType     = "jumpstarter_driver_bt_peer.driver.BtPeer"

	// All containers share the Pod network namespace, so the exporter reaches
	// Host Orchestrator and the simulators on loopback. The orchestration image
	// reserves 2080 for nginx inside a Pod, so Host Orchestrator listens on 2081.
	hostOrchestratorPort = 2081
	hostOrchestratorURL  = "http://127.0.0.1:2081"
	netsimPort           = 7681
	hciPort              = 7300

	defaultGPUMode    = "guest_swiftshader"
	defaultVMCPUs     = 4
	defaultVMMemoryMB = 8192
	defaultOverheadMB = 2048
	isolationLabel    = "cuttlefish.jumpstarter.dev/exporter-set"
	healthStatePath   = "/tmp/jumpstarter-cuttlefish-health.json"
	runtimeIDMount    = "/run/cuttlefish-runtime"
	runtimeIDPath     = runtimeIDMount + "/runtime-id"

	fetchPath       = "/home/vsoc-01/fetch"
	cvdStatePath    = "/var/tmp/cvd"
	androidTmpPath  = "/tmp/android"
	imageSourcePath = "/image-source"

	runtimeContainerName = "cuttlefish"
	gateContainerName    = "wait-for-cuttlefish"
	turnContainerName    = "cuttlefish-turn"

	// WebRTC display over the lease. Media is UDP addressed to the Pod's own
	// interfaces, so a browser can only reach it either through ingress to the
	// runtime Pod - which the isolation policy exists to forbid - or through a
	// relay the Pod can reach locally. coturn is that relay: it listens on
	// loopback inside the Pod, the client forwards its TCP port over the lease
	// like any other service, and the streamer sends media to a relay address in
	// its own network namespace. The relay range stays clear of the streamer's
	// own 15550-15599 UDP candidates.
	DefaultTurnImage  = "docker.io/coturn/coturn:4.7.0"
	turnPortDefault   = 3478
	webUIPortDefault  = 2090
	turnRealm         = "cuttlefish.jumpstarter.dev"
	turnUser          = "cvd"
	turnSecretDefault = "cuttlefish"
	turnRelayMin      = 49160
	turnRelayMax      = 49200
	webrtcNginxPath   = "/etc/nginx/sites-enabled/jumpstarter-webrtc.conf"
)

// healthPorts are the simulator listeners the liveness probe expects while a guest runs.
var healthPorts = []int{netsimPort, hciPort}

var turnSecretPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,64}$`)

type Provisioner struct {
	Version string
}

type storageConfig struct {
	imageClaim                    string
	fetchImages                   bool
	build                         string
	imageSize, stateSize, tmpSize resource.Quantity
	// budget is the ephemeral storage every container touching the volumes must reserve.
	budget resource.Quantity
}

// guestSpec is the effective guest size after template values override parameters.
type guestSpec struct {
	cpus, memoryMB int
}

type images struct {
	exporter, runtime         string
	exporterPull, runtimePull corev1.PullPolicy
}

// webrtcConfig describes the in-Pod TURN relay and the nginx vhost that
// advertises it to the browser.
type webrtcConfig struct {
	enabled          bool
	image, secret    string
	turnPort, uiPort int
}

// resolveWebRTCConfig reads the opt-in WebRTC display parameters. Disabled by
// default: it adds a container, and exporters driven only over adb do not need it.
func resolveWebRTCConfig(parameters map[string]interface{}) (webrtcConfig, error) {
	config := webrtcConfig{}
	if enabled, _ := parameters["webrtc_turn"].(bool); !enabled {
		return config, nil
	}
	config.enabled = true
	config.image = DefaultTurnImage
	if value, ok := parameters["turn_image"].(string); ok && value != "" {
		config.image = value
	}
	config.secret = turnSecretDefault
	if value, ok := parameters["turn_secret"].(string); ok && value != "" {
		config.secret = value
	}
	// The secret is inlined into an nginx `return` literal, where `$` starts a
	// variable, and into a coturn argument, so only a conservative alphabet is
	// accepted rather than escaping per context.
	if !turnSecretPattern.MatchString(config.secret) {
		return config, fmt.Errorf("turn_secret must match %s", turnSecretPattern)
	}
	var err error
	if config.turnPort, err = positiveInt(parameters, "turn_port", turnPortDefault); err != nil {
		return config, err
	}
	if config.uiPort, err = positiveInt(parameters, "webui_port", webUIPortDefault); err != nil {
		return config, err
	}
	for key, port := range map[string]int{"turn_port": config.turnPort, "webui_port": config.uiPort} {
		if port > 65535 || runtimePortReserved(port) {
			return config, fmt.Errorf("%s must be an integer port in 1..65535 that does not conflict with a runtime service", key)
		}
	}
	if config.turnPort == config.uiPort {
		return config, fmt.Errorf("turn_port and webui_port must differ")
	}
	return config, nil
}

// runtimePortReserved reports whether a port is taken by a service in the
// orchestration image or by the TURN relay range, so no configurable listener
// can be placed on top of one.
func runtimePortReserved(port int) bool {
	switch port {
	case 80, 443, 1080, 1443, 2080, hostOrchestratorPort, 2443, hciPort, 7301, 7302, 7303, netsimPort, 15037, 19531:
		return true
	}
	return (port >= 6520 && port <= 6620) || (port >= 15550 && port <= 15560) || (port >= turnRelayMin && port <= turnRelayMax)
}

// webrtcNginxCommand writes a second nginx vhost before the image's services
// start. It proxies Host Orchestrator like the stock vhost, but overrides
// /infra_config so the browser is handed the Pod-local TURN relay instead of the
// public STUN server compiled into the operator binary, and upgrades the
// signalling WebSocket that the stock vhost answers with 400 (the client then
// degrades to HTTP polling).
func webrtcNginxCommand(webrtc webrtcConfig, upstreamPort int) string {
	// json.Marshal cannot fail on this literal; the secret alphabet keeps the
	// output free of characters nginx would interpret inside the quoted literal.
	iceServers, _ := json.Marshal(map[string]interface{}{
		"message_type": "config",
		"ice_servers": []map[string]interface{}{{
			"urls":       []string{fmt.Sprintf("turn:127.0.0.1:%d?transport=tcp", webrtc.turnPort)},
			"username":   turnUser,
			"credential": webrtc.secret,
		}},
	})
	// Loopback only, like the relay: the lease is the sole way in, and binding
	// no [::] keeps nginx starting on Pods without IPv6.
	return fmt.Sprintf(`cat > %s <<'NGINX'
server {
    listen 127.0.0.1:%d;

    location = /infra_config {
        default_type application/json;
        return 200 '%s';
    }

    location / {
        location ~* ^/.*/(adb|connect)$ {
            proxy_pass http://127.0.0.1:%d;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "Upgrade";
            proxy_set_header Host $host;
        }

        proxy_pass http://127.0.0.1:%d;
        client_max_body_size 20G;
    }
}
NGINX
`, webrtcNginxPath, webrtc.uiPort, iceServers, upstreamPort, upstreamPort)
}

// turnContainer relays WebRTC media between the browser, which reaches it over
// the lease, and the streamer, which reaches its relay address Pod-locally.
func turnContainer(webrtc webrtcConfig) corev1.Container {
	restartAlways := corev1.ContainerRestartPolicyAlways
	return corev1.Container{
		Name:            turnContainerName,
		Image:           webrtc.image,
		ImagePullPolicy: corev1.PullIfNotPresent,
		RestartPolicy:   &restartAlways,
		Command: []string{
			"turnserver",
			"-n",
			"--log-file=stdout",
			"--listening-ip=127.0.0.1",
			fmt.Sprintf("--listening-port=%d", webrtc.turnPort),
			"--relay-ip=127.0.0.1",
			fmt.Sprintf("--min-port=%d", turnRelayMin),
			fmt.Sprintf("--max-port=%d", turnRelayMax),
			"--realm=" + turnRealm,
			fmt.Sprintf("--user=%s:%s", turnUser, webrtc.secret),
			"--lt-cred-mech",
			"--no-tls",
			"--no-dtls",
			"--no-cli",
			"--no-multicast-peers",
		},
	}
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

func (p *Provisioner) resolveImages(overrides *virtualtargetv1alpha1.ImageOverrides) images {
	var exporterSpec, runtimeSpec *virtualtargetv1alpha1.ImageSpec
	if overrides != nil {
		exporterSpec, runtimeSpec = overrides.Exporter, overrides.Runtime
	}
	img := images{}
	img.exporter, img.exporterPull = p.resolveImageSpec(exporterSpec, DefaultExporterImage)
	img.runtime, img.runtimePull = p.resolveImageSpec(runtimeSpec, DefaultRuntimeImage)
	return img
}

func resolveStorageConfig(parameters map[string]interface{}) (storageConfig, error) {
	config := storageConfig{
		imageSize: resource.MustParse("20Gi"),
		stateSize: resource.MustParse("20Gi"),
		tmpSize:   resource.MustParse("4Gi"),
	}
	config.imageClaim, _ = parameters["image_volume_claim"].(string)
	config.fetchImages, _ = parameters["fetch_images"].(bool)
	if config.imageClaim != "" && config.fetchImages {
		return config, fmt.Errorf("cuttlefish requires either image_volume_claim or fetch_images, not both")
	}
	if config.imageClaim == "" && !config.fetchImages {
		return config, fmt.Errorf("cuttlefish requires image_volume_claim or fetch_images=true")
	}
	if config.fetchImages {
		config.build = "aosp-android-latest-release/aosp_cf_x86_64_auto-userdebug"
		if value, ok := parameters["default_build"].(string); ok && value != "" {
			config.build = value
		}
	}

	if raw, exists := parameters["storage"]; exists {
		storage, ok := raw.(map[string]interface{})
		if !ok {
			return config, fmt.Errorf("parameters.storage must be an object")
		}
		for key, target := range map[string]*resource.Quantity{
			"imageSize": &config.imageSize, "stateSize": &config.stateSize, "tmpSize": &config.tmpSize,
		} {
			value, exists := storage[key]
			if !exists {
				continue
			}
			text, ok := value.(string)
			quantity, err := resource.ParseQuantity(text)
			if !ok || err != nil || quantity.Sign() <= 0 {
				return config, fmt.Errorf("parameters.storage.%s must be a positive storage quantity", key)
			}
			*target = quantity
		}
	}
	config.budget = config.imageSize.DeepCopy()
	config.budget.Add(config.stateSize)
	config.budget.Add(config.tmpSize)
	config.budget.Add(resource.MustParse("1Gi")) // Container layers and logs need space beyond the volume budgets.
	return config, nil
}

func resolveServiceAccount(parameters map[string]interface{}) (string, error) {
	if privileged, _ := parameters["runtime_privileged"].(bool); !privileged {
		return "", fmt.Errorf("cuttlefish requires runtime_privileged=true; unprivileged device access is not supported")
	}
	name, _ := parameters["service_account_name"].(string)
	if name == "" || name == "default" || len(validation.IsDNS1123Subdomain(name)) != 0 {
		return "", fmt.Errorf("service_account_name must name a dedicated workload service account")
	}
	return name, nil
}

func (p *Provisioner) RenderPod(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	mergedParameters map[string]interface{},
	overrides *virtualtargetv1alpha1.ImageOverrides,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (*corev1.Pod, error) {
	_ = ctx
	if exporterSet.Spec.RecycleStrategy == virtualtargetv1alpha1.RecycleStrategyInPlaceReuse {
		return nil, fmt.Errorf("managed Cuttlefish requires ExitAndReplace recycling")
	}
	storage, err := resolveStorageConfig(mergedParameters)
	if err != nil {
		return nil, err
	}
	serviceAccount, err := resolveServiceAccount(mergedParameters)
	if err != nil {
		return nil, err
	}
	webrtc, err := resolveWebRTCConfig(mergedParameters)
	if err != nil {
		return nil, err
	}
	// The reconciler persists the enriched drivers itself; rendering needs the
	// validation and the effective guest size for the runtime budget.
	_, guest, err := enrichDrivers(exporterSet.Spec.Template.Spec.Drivers, mergedParameters)
	if err != nil {
		return nil, err
	}
	resources, err := runtimeResources(vtc, guest, mergedParameters)
	if err != nil {
		return nil, err
	}
	if err := reserveStorage(&resources, storage.budget); err != nil {
		return nil, err
	}
	img := p.resolveImages(overrides)

	pod := &corev1.Pod{
		ObjectMeta: podMeta(exporterSet, exporter),
		Spec: corev1.PodSpec{
			// Never: ExitAndReplace relies on the exporter (main) exit completing the Pod.
			RestartPolicy:                corev1.RestartPolicyNever,
			ServiceAccountName:           serviceAccount,
			AutomountServiceAccountToken: boolPtr(false),
			InitContainers:               initContainers(img, storage, resources, webrtc),
			Containers:                   []corev1.Container{exporterContainer(img)},
			Volumes:                      volumes(storage),
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
	return pod, nil
}

func podMeta(exporterSet *virtualtargetv1alpha1.ExporterSet, exporter *jumpstarterdevv1alpha1.Exporter) metav1.ObjectMeta {
	meta := metav1.ObjectMeta{
		Namespace:   exporterSet.Namespace,
		Labels:      maps.Clone(exporterSet.Spec.Template.Metadata.Labels),
		Annotations: maps.Clone(exporterSet.Spec.Template.Metadata.Annotations),
	}
	if exporter != nil {
		meta.Name = exporter.Name
	} else {
		meta.GenerateName = exporterSet.Name + "-"
	}
	if meta.Labels == nil {
		meta.Labels = map[string]string{}
	}
	meta.Labels[isolationLabel] = string(exporterSet.UID)
	return meta
}

func exporterSecurityContext() *corev1.SecurityContext {
	uid := exporterNonRootUID
	return &corev1.SecurityContext{RunAsUser: &uid, RunAsNonRoot: boolPtr(true)}
}

// exporterContainer runs jmp behind the health wrapper, which records the
// runtime marker before the exporter registers and backs the liveness probe.
func exporterContainer(img images) corev1.Container {
	return corev1.Container{
		Name:            "exporter",
		Image:           img.exporter,
		ImagePullPolicy: img.exporterPull,
		Command: []string{"python3", "-m", "jumpstarter_driver_cuttlefish.health", "--run-exporter",
			healthStatePath, runtimeIDPath, hostOrchestratorURL, exporterConfigPath},
		Env:             []corev1.EnvVar{{Name: "HOME", Value: "/tmp"}},
		SecurityContext: exporterSecurityContext(),
		VolumeMounts:    []corev1.VolumeMount{{Name: "cvd-state", MountPath: runtimeIDMount, ReadOnly: true}},
		// With restartPolicy Never, a failed liveness check ends the exporter and lets ExitAndReplace recycle the Pod.
		LivenessProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{Exec: &corev1.ExecAction{
				Command: []string{"python3", "-m", "jumpstarter_driver_cuttlefish.health", healthStatePath},
			}},
			PeriodSeconds: 10, TimeoutSeconds: 10, FailureThreshold: 6,
		},
	}
}

// initContainers stages images, fixes ownership, starts the runtime as a
// native sidecar (plus the TURN relay when enabled) and gates the exporter on
// Host Orchestrator readiness.
func initContainers(img images, storage storageConfig, runtime corev1.ResourceRequirements, webrtc webrtcConfig) []corev1.Container {
	stateMounts := []corev1.VolumeMount{
		{Name: "cvd-images", MountPath: fetchPath},
		{Name: "cvd-state", MountPath: cvdStatePath},
		{Name: "android-tmp", MountPath: androidTmpPath},
	}
	deviceMounts := []corev1.VolumeMount{
		{Name: "kvm", MountPath: "/dev/kvm"},
		{Name: "vhost-net", MountPath: "/dev/vhost-net"},
		{Name: "tun", MountPath: "/dev/net/tun"},
	}
	restartAlways := corev1.ContainerRestartPolicyAlways
	root := int64(0)

	var containers []corev1.Container
	if storage.imageClaim != "" {
		containers = append(containers, corev1.Container{
			Name: "copy-images", Image: img.runtime, ImagePullPolicy: img.runtimePull,
			Command:   []string{"bash", "-ec", "cp -a --reflink=auto " + imageSourcePath + "/. " + fetchPath + "/"},
			Resources: storageReservation(storage.budget),
			VolumeMounts: []corev1.VolumeMount{
				{Name: "image-source", MountPath: imageSourcePath, ReadOnly: true},
				{Name: "cvd-images", MountPath: fetchPath},
			},
		})
	}
	if storage.fetchImages {
		containers = append(containers, corev1.Container{
			Name: "fetch-images", Image: img.runtime, ImagePullPolicy: img.runtimePull,
			Command:      []string{"cvd", "fetch", "--default_build=" + storage.build, "--target_directory=" + fetchPath},
			Resources:    storageReservation(storage.budget),
			VolumeMounts: []corev1.VolumeMount{{Name: "cvd-images", MountPath: fetchPath}},
		})
	}
	// The marker lets the exporter and probe detect a sidecar restart that lost runtime state.
	runtimeCommand := "cat /proc/sys/kernel/random/uuid > " + cvdStatePath + "/runtime-id\n" +
		"chmod 644 " + cvdStatePath + "/runtime-id\nexec /root/run_services.sh"
	if webrtc.enabled {
		// nginx reads sites-enabled at startup, so the vhost has to exist before
		// run_services.sh starts it.
		runtimeCommand = webrtcNginxCommand(webrtc, hostOrchestratorPort) + runtimeCommand
	}
	containers = append(containers,
		corev1.Container{
			Name: "fix-cuttlefish-permissions", Image: img.runtime, ImagePullPolicy: img.runtimePull,
			Command: []string{"bash", "-c", "mkdir -p " + cvdStatePath + " " + androidTmpPath +
				" && chown -R httpcvd:httpcvd " + cvdStatePath + " " + androidTmpPath + " " + fetchPath},
			VolumeMounts: stateMounts,
		},
		corev1.Container{
			Name: runtimeContainerName, Image: img.runtime, ImagePullPolicy: img.runtimePull,
			RestartPolicy:   &restartAlways,
			Command:         []string{"bash", "-ec", runtimeCommand},
			Resources:       runtime,
			SecurityContext: &corev1.SecurityContext{Privileged: boolPtr(true), RunAsUser: &root},
			VolumeMounts:    append(stateMounts, deviceMounts...),
		},
		corev1.Container{
			// Runs in the exporter image so the check shares the network namespace and Python runtime with jmp.
			Name: gateContainerName, Image: img.exporter, ImagePullPolicy: img.exporterPull,
			Command:         []string{"python3", "-m", "jumpstarter_driver_cuttlefish.health", "--wait", hostOrchestratorURL},
			SecurityContext: exporterSecurityContext(),
		},
	)
	if webrtc.enabled {
		containers = append(containers, turnContainer(webrtc))
	}
	return containers
}

func volumes(storage storageConfig) []corev1.Volume {
	emptyDir := func(name string, size *resource.Quantity) corev1.Volume {
		return corev1.Volume{Name: name, VolumeSource: corev1.VolumeSource{
			EmptyDir: &corev1.EmptyDirVolumeSource{SizeLimit: size},
		}}
	}
	result := []corev1.Volume{
		emptyDir("cvd-images", &storage.imageSize),
		emptyDir("cvd-state", &storage.stateSize),
		emptyDir("android-tmp", &storage.tmpSize),
		deviceVolume("kvm", "/dev/kvm"),
		deviceVolume("vhost-net", "/dev/vhost-net"),
		deviceVolume("tun", "/dev/net/tun"),
	}
	if storage.imageClaim != "" {
		// The claim is only ever read; each Pod copies it into its private image volume.
		result = append(result, corev1.Volume{Name: "image-source", VolumeSource: corev1.VolumeSource{
			PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: storage.imageClaim, ReadOnly: true},
		}})
	}
	return result
}

func (p *Provisioner) EnrichExporterExport(
	drivers []virtualtargetv1alpha1.DriverConfig,
	mergedParameters map[string]interface{},
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	enriched, _, err := enrichDrivers(drivers, mergedParameters)
	return enriched, err
}

// enrichDrivers pins every driver to the in-Pod runtime and returns the
// effective guest size the runtime container must budget for.
func enrichDrivers(drivers []virtualtargetv1alpha1.DriverConfig, parameters map[string]interface{}) ([]virtualtargetv1alpha1.DriverConfig, guestSpec, error) {
	var guest guestSpec
	found := 0
	result := make([]virtualtargetv1alpha1.DriverConfig, 0, len(drivers))
	for _, driver := range drivers {
		var err error
		switch driver.Type {
		case cuttlefishDriverType:
			found++
			driver, guest, err = enrichCuttlefishDriver(driver, parameters)
		case netsimDriverType:
			driver, err = enrichDriverConfig(driver, map[string]interface{}{"host": "127.0.0.1", "port": netsimPort}, "netsim")
		case btPeerDriverType:
			driver, err = enrichDriverConfig(driver, map[string]interface{}{"transport": fmt.Sprintf("tcp-client:127.0.0.1:%d", hciPort)}, "bt_peer")
		}
		if err != nil {
			return nil, guest, err
		}
		result = append(result, driver)
	}
	if found != 1 {
		return nil, guest, fmt.Errorf("cuttlefish requires exactly one Cuttlefish driver per Pod, got %d", found)
	}
	return result, guest, nil
}

func enrichCuttlefishDriver(driver virtualtargetv1alpha1.DriverConfig, parameters map[string]interface{}) (virtualtargetv1alpha1.DriverConfig, guestSpec, error) {
	var guest guestSpec
	var err error
	if guest.cpus, err = positiveInt(parameters, "vm_cpus", defaultVMCPUs); err != nil {
		return driver, guest, err
	}
	if guest.memoryMB, err = positiveInt(parameters, "vm_memory_mb", defaultVMMemoryMB); err != nil {
		return driver, guest, err
	}
	config, err := decodeConfig(driver, "Cuttlefish")
	if err != nil {
		return driver, guest, err
	}

	webrtc, err := resolveWebRTCConfig(parameters)
	if err != nil {
		return driver, guest, err
	}
	for _, key := range []string{"webui_port", "turn_port"} {
		if _, exists := config[key]; exists {
			return driver, guest, fmt.Errorf("%s is managed by the provisioner; set parameters.webrtc_turn=true", key)
		}
	}
	ports := append([]int(nil), healthPorts...)
	if webrtc.enabled {
		config["webui_port"] = webrtc.uiPort
		config["turn_port"] = webrtc.turnPort
		// A dead relay or vhost means no display, which the probe should catch
		// rather than leaving a lease holder staring at a blank page.
		ports = append(ports, webrtc.uiPort, webrtc.turnPort)
	}

	config["managed"] = true
	config["health_state_path"] = healthStatePath
	config["runtime_id_path"] = runtimeIDPath
	config["health_ports"] = ports
	// Any other endpoint would bypass the managed runtime in this Pod.
	for key, value := range map[string]interface{}{"scheme": "http", "host": "127.0.0.1", "port": hostOrchestratorPort, "instance_num": 1} {
		if err := pin(config, key, value); err != nil {
			return driver, guest, err
		}
	}
	setDefault(config, "group", "cvd")
	setDefault(config, "name", "1")
	setDefault(config, "boot_timeout", 300)

	envConfig, err := configObject(config, "env_config")
	if err != nil {
		return driver, guest, err
	}
	common, err := configObject(envConfig, "common")
	if err != nil {
		return driver, guest, err
	}
	setDefault(common, "host_package", fetchPath)
	envConfig["common"] = common
	// Standalone RootCanal does not propagate the userspace VSOCK flag.
	if err := pin(envConfig, "netsim_bt", true); err != nil {
		return driver, guest, fmt.Errorf("%w; standalone RootCanal is not supported", err)
	}

	instances, ok := envConfig["instances"].([]interface{})
	if raw, exists := envConfig["instances"]; exists && (!ok || len(instances) != 1) {
		return driver, guest, fmt.Errorf("env_config.instances must contain exactly one instance, got %v", raw)
	}
	if len(instances) == 0 {
		instances = []interface{}{map[string]interface{}{}}
	}
	instance, ok := instances[0].(map[string]interface{})
	if !ok || instance == nil {
		return driver, guest, fmt.Errorf("env_config.instances[0] must be an object")
	}
	disk, err := configObject(instance, "disk")
	if err != nil {
		return driver, guest, err
	}
	setDefault(disk, "default_build", fetchPath)
	instance["disk"] = disk
	graphics, err := configObject(instance, "graphics")
	if err != nil {
		return driver, guest, err
	}
	gpuMode := defaultGPUMode
	if configured, ok := parameters["gpu_mode"].(string); ok && configured != "" {
		gpuMode = configured
	}
	setDefault(graphics, "gpu_mode", gpuMode)
	instance["graphics"] = graphics

	vm, err := configObject(instance, "vm")
	if err != nil {
		return driver, guest, err
	}
	for _, other := range []string{"qemu", "gem5"} {
		if _, exists := vm[other]; exists {
			return driver, guest, fmt.Errorf("managed Cuttlefish requires crosvm with private userspace VSOCK, got vm.%s", other)
		}
	}
	crosvm, err := configObject(vm, "crosvm")
	if err != nil {
		return driver, guest, err
	}
	// The upstream schema wants the string "true" here; two Pods on one node share guest CIDs otherwise.
	if err := pin(crosvm, "vhost_user_vsock", "true"); err != nil {
		return driver, guest, err
	}
	vm["crosvm"] = crosvm
	// Template guest values take precedence over the class parameters.
	setDefault(vm, "cpus", guest.cpus)
	setDefault(vm, "memory_mb", guest.memoryMB)
	if guest.cpus, err = positiveInt(vm, "cpus", guest.cpus); err != nil {
		return driver, guest, err
	}
	if guest.memoryMB, err = positiveInt(vm, "memory_mb", guest.memoryMB); err != nil {
		return driver, guest, err
	}
	instance["vm"] = vm
	instances[0] = instance
	envConfig["instances"] = instances
	config["env_config"] = envConfig

	driver, err = encodeConfig(driver, config)
	return driver, guest, err
}

// pin sets config[key] to value and rejects a template value that differs.
// Values are compared through their JSON encoding so 2081 matches 2081.0.
func pin(config map[string]interface{}, key string, value interface{}) error {
	if current, exists := config[key]; exists && !jsonEqual(current, value) {
		return fmt.Errorf("managed Cuttlefish requires %s=%v, got %v", key, value, current)
	}
	config[key] = value
	return nil
}

func jsonEqual(a, b interface{}) bool {
	rawA, errA := json.Marshal(a)
	rawB, errB := json.Marshal(b)
	return errA == nil && errB == nil && bytes.Equal(rawA, rawB)
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

// runtimeResources starts from the class scheduling resources and guarantees
// the runtime container requests the guest memory plus runtime overhead.
func runtimeResources(vtc *virtualtargetv1alpha1.VirtualTargetClass, guest guestSpec, parameters map[string]interface{}) (corev1.ResourceRequirements, error) {
	resources := corev1.ResourceRequirements{}
	if vtc.Spec.Scheduling != nil && vtc.Spec.Scheduling.Resources != nil {
		resources = *vtc.Spec.Scheduling.Resources.DeepCopy()
	}
	overhead, err := positiveInt(parameters, "runtime_memory_overhead_mb", defaultOverheadMB)
	if err != nil {
		return resources, err
	}
	budget := *resource.NewQuantity(int64(guest.memoryMB+overhead)*1024*1024, resource.BinarySI)
	if resources.Requests == nil {
		resources.Requests = corev1.ResourceList{}
	}
	for _, values := range []corev1.ResourceList{resources.Requests, resources.Limits} {
		if value, exists := values[corev1.ResourceMemory]; exists && value.Cmp(budget) < 0 {
			return resources, fmt.Errorf("runtime memory must be at least %s for guest plus overhead", budget.String())
		}
	}
	if _, exists := resources.Requests[corev1.ResourceMemory]; !exists {
		resources.Requests[corev1.ResourceMemory] = budget
	}
	if limit, exists := resources.Limits[corev1.ResourceMemory]; exists && resources.Requests.Memory().Cmp(limit) > 0 {
		return resources, fmt.Errorf("runtime memory request exceeds limit")
	}
	if _, exists := resources.Requests[corev1.ResourceCPU]; !exists {
		if limit, exists := resources.Limits[corev1.ResourceCPU]; exists {
			resources.Requests[corev1.ResourceCPU] = limit.DeepCopy()
		} else {
			resources.Requests[corev1.ResourceCPU] = *resource.NewQuantity(int64(guest.cpus), resource.DecimalSI)
		}
	}
	if resources.Requests.Cpu().Sign() <= 0 {
		return resources, fmt.Errorf("runtime CPU request must be positive")
	}
	if limit, exists := resources.Limits[corev1.ResourceCPU]; exists && resources.Requests.Cpu().Cmp(limit) > 0 {
		return resources, fmt.Errorf("runtime CPU request exceeds limit")
	}
	return resources, nil
}

// storageReservation is the ephemeral storage for containers that only touch the volumes.
func storageReservation(budget resource.Quantity) corev1.ResourceRequirements {
	return corev1.ResourceRequirements{
		Requests: corev1.ResourceList{corev1.ResourceEphemeralStorage: budget.DeepCopy()},
		Limits:   corev1.ResourceList{corev1.ResourceEphemeralStorage: budget.DeepCopy()},
	}
}

// reserveStorage adds the ephemeral storage budget to class-provided resources.
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

func (p *Provisioner) RenderNetworkPolicy(es *virtualtargetv1alpha1.ExporterSet) *networkingv1.NetworkPolicy {
	return &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{Name: "cuttlefish-" + string(es.UID), Namespace: es.Namespace},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{MatchLabels: map[string]string{isolationLabel: string(es.UID)}},
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeIngress},
		},
	}
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
