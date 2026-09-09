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

	// Runtime backends. "http" drives Host Orchestrator inside the Pod. "exec"
	// runs cvd in the runtime container through jumpstarter-exec, the
	// launcher-socket pattern the QEMU provisioner uses and the in-Pod
	// equivalent of Podcvd's `podman exec ... cvd`. Exec mode does not start
	// Host Orchestrator; an optional loopback-only nginx proxy serves WebRTC.
	backendHTTP = "http"
	backendExec = "exec"

	// Shared emptyDir carrying jumpstarter-exec, the launcher socket and the
	// env_config handed to `cvd load`. Only used by the exec backend.
	sharedVolumeName      = "shared"
	sharedMountPath       = "/shared"
	sharedVolumeSizeLimit = "100Mi"
	jmpExecBinaryPath     = "/jumpstarter/bin/jumpstarter-exec"
	launcherSocketPath    = sharedMountPath + "/launcher.sock"
	// Children inherit the launcher's working directory, and cvd aborts when it
	// cannot read it as cvd_user; the image's /root WORKDIR is 0700.
	launcherWorkDir = "/"
	// cvd keeps its instance database per uid. httpcvd owns the state
	// directories, so guest processes stay non-root inside the privileged container.
	defaultCvdUser = "httpcvd"

	// WebRTC display over the lease. The TURN listener is loopback-only, but
	// its UDP relay binds the Pod IP so the streamer's Pod-IP ICE candidates
	// can exchange packets with it. The relay range stays clear of the
	// streamer's own 15550-15599 UDP candidates.
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

var userNamePattern = regexp.MustCompile(`^[a-z_][a-z0-9_-]{0,31}$`)

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

func resolveGuestSpec(parameters map[string]any) (guestSpec, error) {
	var guest guestSpec
	var err error
	if guest.cpus, err = positiveInt(parameters, "vm_cpus", defaultVMCPUs); err != nil {
		return guest, err
	}
	if guest.memoryMB, err = positiveInt(parameters, "vm_memory_mb", defaultVMMemoryMB); err != nil {
		return guest, err
	}
	return guest, nil
}

type runtimeConfig struct {
	backend, cvdUser string
}

func (r runtimeConfig) exec() bool {
	return r.backend == backendExec
}

// endpoint is how the exporter and probes reach the runtime.
func (r runtimeConfig) endpoint() string {
	if r.exec() {
		return fmt.Sprintf("exec://%s@%s", r.cvdUser, launcherSocketPath)
	}
	return hostOrchestratorURL
}

func resolveBackend(parameters map[string]any) (runtimeConfig, error) {
	config := runtimeConfig{backend: backendHTTP, cvdUser: defaultCvdUser}
	if raw, exists := parameters["backend"]; exists {
		value, ok := raw.(string)
		if !ok || (value != backendHTTP && value != backendExec) {
			return config, fmt.Errorf("backend must be %q or %q", backendHTTP, backendExec)
		}
		config.backend = value
	}
	if raw, exists := parameters["cvd_user"]; exists {
		if !config.exec() {
			return config, fmt.Errorf("cvd_user requires parameters.backend=%q", backendExec)
		}
		value, ok := raw.(string)
		if !ok || !userNamePattern.MatchString(value) || value == "root" {
			return config, fmt.Errorf("cvd_user must be a non-root container user name")
		}
		config.cvdUser = value
	}
	return config, nil
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
func resolveWebRTCConfig(parameters map[string]any) (webrtcConfig, error) {
	config := webrtcConfig{}
	rawEnabled, exists := parameters["webrtc_turn"]
	if !exists {
		return config, nil
	}
	enabled, ok := rawEnabled.(bool)
	if !ok {
		return config, fmt.Errorf("webrtc_turn must be a boolean")
	}
	if !enabled {
		return config, nil
	}
	config.enabled = true
	config.image = DefaultTurnImage
	if raw, exists := parameters["turn_image"]; exists {
		value, ok := raw.(string)
		if !ok || value == "" {
			return config, fmt.Errorf("turn_image must be a non-empty string")
		}
		config.image = value
	}
	config.secret = turnSecretDefault
	if raw, exists := parameters["turn_secret"]; exists {
		value, ok := raw.(string)
		if !ok {
			return config, fmt.Errorf("turn_secret must be a string")
		}
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

// applyWebRTCConfig validates the provisioner-owned display settings, pins the
// effective ports into the driver config, and returns the listeners that must
// participate in the runtime health check.
func applyWebRTCConfig(config map[string]any, parameters map[string]any) ([]int, error) {
	webrtc, err := resolveWebRTCConfig(parameters)
	if err != nil {
		return nil, err
	}
	for _, key := range []string{"webui_port", "turn_port"} {
		if _, exists := config[key]; exists {
			return nil, fmt.Errorf("%s is managed by the provisioner; set parameters.webrtc_turn=true", key)
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
	return ports, nil
}

// runtimePortReserved reports whether a port is taken by a service in the
// orchestration image or by the TURN relay range, so no configurable listener
// can be placed on top of one.
func runtimePortReserved(port int) bool {
	switch port {
	case 80, 443, 1080, 1443, 2080, hostOrchestratorPort, 2443, hciPort, 7301, 7302, 7303, netsimPort, 15037, 19531:
		return true
	}
	return (port >= 6520 && port <= 6620) || (port >= 15550 && port <= 15599) || (port >= turnRelayMin && port <= turnRelayMax)
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
	iceServers, _ := json.Marshal(map[string]any{
		"message_type": "config",
		"ice_servers": []map[string]any{{
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

    # The local forward is a browser endpoint for an unauthenticated control
    # API. Reject DNS rebinding and requests initiated by another page.
    if ($host !~ ^(127\.0\.0\.1|localhost)$) { return 403; }
    if ($http_sec_fetch_site ~* ^(cross-site|same-site)$) { return 403; }
    set $origin_ok 0;
    if ($http_origin = "") { set $origin_ok 1; }
    if ($http_origin = "http://$http_host") { set $origin_ok 1; }
    if ($origin_ok = 0) { return 403; }

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
// the lease, and the streamer, which reaches its relay address at the Pod IP.
func turnContainer(webrtc webrtcConfig) corev1.Container {
	restartAlways := corev1.ContainerRestartPolicyAlways
	return corev1.Container{
		Name:            turnContainerName,
		Image:           webrtc.image,
		ImagePullPolicy: corev1.PullIfNotPresent,
		RestartPolicy:   &restartAlways,
		Env: []corev1.EnvVar{{Name: "POD_IP", ValueFrom: &corev1.EnvVarSource{
			FieldRef: &corev1.ObjectFieldSelector{FieldPath: "status.podIP"},
		}}},
		Command: []string{
			"turnserver",
			"-n",
			"--log-file=stdout",
			"--listening-ip=127.0.0.1",
			fmt.Sprintf("--listening-port=%d", webrtc.turnPort),
			"--relay-ip=$(POD_IP)",
			fmt.Sprintf("--min-port=%d", turnRelayMin),
			fmt.Sprintf("--max-port=%d", turnRelayMax),
			"--realm=" + turnRealm,
			fmt.Sprintf("--user=%s:%s", turnUser, webrtc.secret),
			"--lt-cred-mech",
			"--no-tls",
			"--no-dtls",
			"--no-cli",
			"--cli-password=" + webrtc.secret,
			// Limit TURN peers to the streamer's Pod-IP and loopback candidates.
			// The browser still reaches only the loopback listener over the lease.
			"--allow-loopback-peers",
			"--no-multicast-peers",
			"--denied-peer-ip=0.0.0.0-255.255.255.255",
			"--denied-peer-ip=::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
			"--allowed-peer-ip=127.0.0.1",
			"--allowed-peer-ip=$(POD_IP)",
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

func resolveStorageConfig(parameters map[string]any) (storageConfig, error) {
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
		storage, ok := raw.(map[string]any)
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

func resolveServiceAccount(parameters map[string]any) (string, error) {
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
	mergedParameters map[string]any,
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
	rt, err := resolveBackend(mergedParameters)
	if err != nil {
		return nil, err
	}
	if rt.exec() {
		storage.budget.Add(resource.MustParse(sharedVolumeSizeLimit))
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
			AutomountServiceAccountToken: new(false),
			InitContainers:               initContainers(img, storage, resources, rt, webrtc, exporter),
			Containers:                   []corev1.Container{exporterContainer(img, rt)},
			Volumes:                      volumes(storage, rt),
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
	if err := configureDeviceResources(pod, mergedParameters, resources); err != nil {
		return nil, err
	}
	return pod, nil
}

// configureDeviceResources leaves each mapped device to the requested plugin,
// avoiding a hostPath bind that exposes the host device inode to runtime chown.
func configureDeviceResources(pod *corev1.Pod, parameters map[string]any, resources corev1.ResourceRequirements) error {
	raw, exists := parameters["device_resources"]
	if !exists {
		return nil
	}
	devices, ok := raw.(map[string]any)
	if !ok {
		return fmt.Errorf("device_resources must map device names to extended resource names")
	}
	for device, rawResource := range devices {
		if device != "kvm" && device != "vhost-net" && device != "tun" {
			return fmt.Errorf("unsupported device_resources device %q", device)
		}
		name, ok := rawResource.(string)
		if !ok || !strings.Contains(name, "/") || len(validation.IsQualifiedName(name)) != 0 {
			return fmt.Errorf("device_resources[%q] must name an extended resource", device)
		}
		limit := resources.Limits[corev1.ResourceName(name)]
		if limit.Sign() <= 0 {
			return fmt.Errorf("device_resources[%q] requires a positive scheduling.resources.limits[%q]", device, name)
		}
		pod.Spec.Volumes = slices.DeleteFunc(pod.Spec.Volumes, func(volume corev1.Volume) bool {
			return volume.Name == device
		})
		for i := range pod.Spec.InitContainers {
			container := &pod.Spec.InitContainers[i]
			container.VolumeMounts = slices.DeleteFunc(container.VolumeMounts, func(mount corev1.VolumeMount) bool {
				return mount.Name == device
			})
		}
	}
	return nil
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
	return &corev1.SecurityContext{RunAsUser: &uid, RunAsNonRoot: new(true)}
}

// exporterContainer runs jmp behind the health wrapper, which records the
// runtime marker before the exporter registers and backs the liveness probe.
func exporterContainer(img images, rt runtimeConfig) corev1.Container {
	container := corev1.Container{
		Name:            "exporter",
		Image:           img.exporter,
		ImagePullPolicy: img.exporterPull,
		Command: []string{"python3", "-m", "jumpstarter_driver_cuttlefish.health", "--run-exporter",
			healthStatePath, runtimeIDPath, rt.endpoint(), exporterConfigPath},
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
	if rt.exec() {
		// The exporter's lease-end path calls `jumpstarter-exec shutdown` on this socket.
		container.VolumeMounts = append(container.VolumeMounts, sharedMount())
		container.Env = append(container.Env, corev1.EnvVar{Name: "JUMPSTARTER_LAUNCHER_SOCKET", Value: launcherSocketPath})
	}
	return container
}

func sharedMount() corev1.VolumeMount {
	return corev1.VolumeMount{Name: sharedVolumeName, MountPath: sharedMountPath}
}

// runtimeCommand is PID 1 of the runtime container. Both variants write the
// per-start marker the exporter and probe use to detect a sidecar restart.
func runtimeCommand(rt runtimeConfig) string {
	marker := "cat /proc/sys/kernel/random/uuid > " + cvdStatePath + "/runtime-id\n" +
		"chmod 644 " + cvdStatePath + "/runtime-id\n"
	if !rt.exec() {
		return marker + "exec /root/run_services.sh"
	}
	// Host Orchestrator and nginx are not started: cvd is driven over the
	// launcher, which leaves no unauthenticated control listener in the Pod.
	// The operator stays for WebRTC. Start system services as root, then run
	// the launcher as the same non-root user that owns the CVD state. The
	// launcher accepts arbitrary commands from the exporter over its socket.
	return marker +
		"service cuttlefish-host-resources start\n" +
		"service cuttlefish-operator start\n" +
		"cd " + launcherWorkDir + "\n" +
		"exec runuser -u " + rt.cvdUser + " -- " + sharedMountPath + "/jumpstarter-exec serve --socket " + launcherSocketPath
}

// initContainers stages images, fixes ownership, starts the runtime as a native
// sidecar (plus the TURN relay when enabled), and gates the exporter on runtime readiness.
func initContainers(img images, storage storageConfig, runtime corev1.ResourceRequirements, rt runtimeConfig, webrtc webrtcConfig, exporter *jumpstarterdevv1alpha1.Exporter) []corev1.Container {
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
	if rt.exec() {
		containers = append(containers, corev1.Container{
			Name: "copy-jumpstarter-exec", Image: img.exporter, ImagePullPolicy: img.exporterPull,
			Command:      []string{"cp", jmpExecBinaryPath, sharedMountPath + "/jumpstarter-exec"},
			VolumeMounts: []corev1.VolumeMount{sharedMount()},
		})
	}
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
	runtimeMounts := append(stateMounts, deviceMounts...)
	var runtimeEnv []corev1.EnvVar
	var gateMounts []corev1.VolumeMount
	if rt.exec() {
		runtimeMounts = append(runtimeMounts, sharedMount())
		gateMounts = []corev1.VolumeMount{sharedMount()}
		if exporter != nil {
			// JEP-0013 persistent log context on every jumpstarter-exec serve line.
			runtimeEnv = []corev1.EnvVar{{
				Name:  "JUMPSTARTER_EXEC_LOG_FIELDS",
				Value: fmt.Sprintf("component=exporter,exporter=%s,namespace=%s", exporter.Name, exporter.Namespace),
			}}
		}
	}
	runtimeScript := runtimeCommand(rt)
	if webrtc.enabled {
		upstreamPort := hostOrchestratorPort
		startProxy := ""
		if rt.exec() {
			upstreamPort = 1080
			// Do not load the image's public listeners or Host Orchestrator proxy.
			startProxy = "printf 'events {}\\nhttp { include " + webrtcNginxPath + "; }\\n' > /tmp/jumpstarter-nginx.conf\n" +
				"nginx -c /tmp/jumpstarter-nginx.conf\n"
		}
		runtimeScript = webrtcNginxCommand(webrtc, upstreamPort) + startProxy + runtimeScript
	}
	permissionsCommand := "mkdir -p " + cvdStatePath + " " + androidTmpPath +
		" && chown -R " + rt.cvdUser + ":" + rt.cvdUser + " " + cvdStatePath + " " + androidTmpPath + " " + fetchPath
	permissionsMounts := stateMounts
	if rt.exec() {
		// The exporter writes env_config here; the launcher creates its socket.
		// Sticky mode keeps the exporter from replacing the launcher socket.
		permissionsCommand += " && chown " + rt.cvdUser + ":" + rt.cvdUser + " " + sharedMountPath +
			" && chmod 1777 " + sharedMountPath
		permissionsMounts = append(append([]corev1.VolumeMount(nil), stateMounts...), sharedMount())
	}
	containers = append(containers,
		corev1.Container{
			Name: "fix-cuttlefish-permissions", Image: img.runtime, ImagePullPolicy: img.runtimePull,
			Command: []string{"bash", "-c", permissionsCommand},
			// chown needs UID 0 regardless of the runtime image's default user.
			SecurityContext: &corev1.SecurityContext{RunAsUser: &root},
			VolumeMounts:    permissionsMounts,
		},
		corev1.Container{
			Name: runtimeContainerName, Image: img.runtime, ImagePullPolicy: img.runtimePull,
			RestartPolicy:   &restartAlways,
			Command:         []string{"bash", "-ec", runtimeScript},
			Env:             runtimeEnv,
			Resources:       runtime,
			SecurityContext: &corev1.SecurityContext{Privileged: new(true), RunAsUser: &root},
			VolumeMounts:    runtimeMounts,
		},
	)
	if webrtc.enabled {
		containers = append(containers, turnContainer(webrtc))
	}
	containers = append(containers, corev1.Container{
		// Runs in the exporter image so the check shares the network namespace and Python runtime with jmp.
		Name: gateContainerName, Image: img.exporter, ImagePullPolicy: img.exporterPull,
		Command:         []string{"python3", "-m", "jumpstarter_driver_cuttlefish.health", "--wait", rt.endpoint()},
		SecurityContext: exporterSecurityContext(),
		VolumeMounts:    gateMounts,
	})
	return containers
}

func volumes(storage storageConfig, rt runtimeConfig) []corev1.Volume {
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
	if rt.exec() {
		size := resource.MustParse(sharedVolumeSizeLimit)
		result = append(result, emptyDir(sharedVolumeName, &size))
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
	mergedParameters map[string]any,
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	enriched, _, err := enrichDrivers(drivers, mergedParameters)
	return enriched, err
}

// enrichDrivers pins every driver to the in-Pod runtime and returns the
// effective guest size the runtime container must budget for.
func enrichDrivers(drivers []virtualtargetv1alpha1.DriverConfig, parameters map[string]any) ([]virtualtargetv1alpha1.DriverConfig, guestSpec, error) {
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
			driver, err = pinDriverConfig(driver, map[string]any{"host": "127.0.0.1", "port": netsimPort}, "netsim")
		case btPeerDriverType:
			driver, err = pinDriverConfig(driver, map[string]any{"transport": fmt.Sprintf("tcp-client:127.0.0.1:%d", hciPort)}, "bt_peer")
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

func enrichCuttlefishDriver(driver virtualtargetv1alpha1.DriverConfig, parameters map[string]any) (virtualtargetv1alpha1.DriverConfig, guestSpec, error) {
	guest, err := resolveGuestSpec(parameters)
	if err != nil {
		return driver, guest, err
	}
	config, err := decodeConfig(driver, "Cuttlefish")
	if err != nil {
		return driver, guest, err
	}
	rt, err := resolveBackend(parameters)
	if err != nil {
		return driver, guest, err
	}
	if _, exists := config["launcher_socket"]; exists && !rt.exec() {
		return driver, guest, fmt.Errorf("launcher_socket is managed by the provisioner; set parameters.backend=%q", backendExec)
	}
	if rt.exec() {
		config["launcher_socket"] = launcherSocketPath
		config["cvd_user"] = rt.cvdUser
	}

	ports, err := applyWebRTCConfig(config, parameters)
	if err != nil {
		return driver, guest, err
	}

	config["managed"] = true
	config["health_state_path"] = healthStatePath
	config["runtime_id_path"] = runtimeIDPath
	config["health_ports"] = ports
	// Any other endpoint would bypass the managed runtime in this Pod.
	for key, value := range map[string]any{"scheme": "http", "host": "127.0.0.1", "port": hostOrchestratorPort, "instance_num": 1} {
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

	instances, ok := envConfig["instances"].([]any)
	if raw, exists := envConfig["instances"]; exists && (!ok || len(instances) != 1) {
		return driver, guest, fmt.Errorf("env_config.instances must contain exactly one instance, got %v", raw)
	}
	if len(instances) == 0 {
		instances = []any{map[string]any{}}
	}
	instance, ok := instances[0].(map[string]any)
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
func pin(config map[string]any, key string, value any) error {
	if current, exists := config[key]; exists && !jsonEqual(current, value) {
		return fmt.Errorf("managed Cuttlefish requires %s=%v, got %v", key, value, current)
	}
	config[key] = value
	return nil
}

func jsonEqual(a, b any) bool {
	rawA, errA := json.Marshal(a)
	rawB, errB := json.Marshal(b)
	return errA == nil && errB == nil && bytes.Equal(rawA, rawB)
}

func configObject(parent map[string]any, key string) (map[string]any, error) {
	raw, exists := parent[key]
	if !exists {
		return map[string]any{}, nil
	}
	value, ok := raw.(map[string]any)
	if !ok || value == nil {
		return nil, fmt.Errorf("%s must be an object", key)
	}
	return value, nil
}

// pinDriverConfig pins a sidecar driver to the simulator endpoints this Pod
// runs; a template value that differs points the driver outside the Pod and is
// rejected, while a matching one is preserved. Keys are applied in sorted order
// so a conflicting template yields a stable error.
func pinDriverConfig(driver virtualtargetv1alpha1.DriverConfig, managed map[string]any, name string) (virtualtargetv1alpha1.DriverConfig, error) {
	config, err := decodeConfig(driver, name)
	if err != nil {
		return driver, err
	}
	for _, key := range slices.Sorted(maps.Keys(managed)) {
		if err := pin(config, key, managed[key]); err != nil {
			return driver, fmt.Errorf("%s driver: %w", name, err)
		}
	}
	return encodeConfig(driver, config)
}

func decodeConfig(driver virtualtargetv1alpha1.DriverConfig, name string) (map[string]any, error) {
	config := map[string]any{}
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

func encodeConfig(driver virtualtargetv1alpha1.DriverConfig, config map[string]any) (virtualtargetv1alpha1.DriverConfig, error) {
	raw, err := json.Marshal(config)
	if err != nil {
		return driver, fmt.Errorf("marshal driver config: %w", err)
	}
	driver.Config = &apiextensionsv1.JSON{Raw: raw}
	return driver, nil
}

func positiveInt(values map[string]any, key string, fallback int) (int, error) {
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
func runtimeResources(vtc *virtualtargetv1alpha1.VirtualTargetClass, guest guestSpec, parameters map[string]any) (corev1.ResourceRequirements, error) {
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

func setDefault(config map[string]any, key string, value any) {
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
