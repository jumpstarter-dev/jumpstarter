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

// Package cuttlefish implements the cuttlefish.jumpstarter.dev
// provisioner for ExporterSets (JEP-0016). One Pod = one Exporter =
// one Host Orchestrator = one CVD: a native sidecar init container
// runs the Cuttlefish Host Orchestrator runtime and a main container
// runs `jmp run`. The control path is localhost HTTP — there is no
// launcher socket and no jumpstarter-exec staging. The provisioner
// never calls the Host Orchestrator at runtime; readiness is observed
// through HTTP probes on the sidecar.
package cuttlefish

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"strings"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
)

const (
	// ProvisionerName is the provisioner identifier for
	// Cuttlefish-based virtual targets.
	ProvisionerName = "cuttlefish.jumpstarter.dev"

	// DefaultExporterImage is the exporter container image.
	DefaultExporterImage = "quay.io/jumpstarter-dev/jumpstarter:latest"

	// DefaultCuttlefishRuntimeImage is the Cuttlefish Host Orchestrator
	// runtime container image.
	DefaultCuttlefishRuntimeImage = "quay.io/jumpstarter-dev/virtual/cuttlefish-runtime:latest"

	// runtimeContainerName is the native sidecar that runs the
	// Cuttlefish Host Orchestrator. Kept as a const so scheduling
	// and RenderPod stay in sync.
	// Named "cvd", not "cuttlefish-host": the sidecar is the device
	// runtime for exactly one CVD, never a multi-device host (JEP-0016
	// exporter = DUT invariant).
	runtimeContainerName = "cvd"

	// exporterContainerName must match the container the reconciler
	// targets when injecting the config volume.
	exporterContainerName = "exporter"

	// stateVolumeName is the emptyDir holding CVD runtime state
	// (images, instance state) under stateMountPath.
	stateVolumeName = "cuttlefish-state"
	stateMountPath  = "/var/lib/cuttlefish"

	// exporterConfigPath is the jmp run config path. Must match
	// exporterset.ExporterConfigMountPath + "/" + exporterConfigKey
	// (cannot import the parent package — test import cycle).
	exporterConfigPath = "/etc/jumpstarter/exporters/config.yaml"

	// exporterNonRootUID is the UID for the exporter main container.
	exporterNonRootUID int64 = 65532

	// Cuttlefish driver type for identification during enrichment.
	cuttlefishDriverType = "jumpstarter_driver_cuttlefish.driver.Cuttlefish"

	// statuszPath is the Host Orchestrator health endpoint used by
	// the sidecar's startup and readiness probes.
	statuszPath = "/_debug/statusz"

	// defaultHostOrchestratorPort is the Host Orchestrator HTTP port
	// when parameters.hostOrchestrator.port is not set.
	defaultHostOrchestratorPort int32 = 2080

	// defaultBootTimeout is the JEP example boot timeout in seconds;
	// the driver's own default (300) is tuned for laptops. Injected
	// into the driver config only when the template omits boot_timeout.
	defaultBootTimeout = 600

	// Startup probe: poll every 2s, allow up to 2 minutes for the
	// Host Orchestrator to come up.
	startupProbePeriodSeconds    int32 = 2
	startupProbeFailureThreshold int32 = 60

	// Parameter paths in mergedParameters.
	paramKeyHOPort       = "hostOrchestrator.port"
	paramKeyStorageSize  = "storage.size"
	paramKeyVsockEnabled = "vsock.enabled"
	paramKeyOperatorPort = "operator.port"
	paramKeyEnvConfig    = "envConfig"

	// vsockResourceName is the extended resource claimed on the sidecar
	// when parameters.vsock.enabled is true. The JEP names no resource;
	// this follows the KubeVirt-family device-plugin convention.
	vsockResourceName corev1.ResourceName = "devices.kubevirt.io/vhost-vsock"
)

// Provisioner implements the cuttlefish.jumpstarter.dev provisioner.
// It renders Pods with a Cuttlefish Host Orchestrator native sidecar
// and an exporter main container talking to it over localhost HTTP.
type Provisioner struct {
	// Version is the build-time version string (e.g. "v0.9.0", "dev").
	// Used to resolve :latest image tags to the correct version.
	Version string
}

// New creates a new Cuttlefish provisioner with the given build-time version.
func New(version string) *Provisioner {
	return &Provisioner{Version: version}
}

// Name returns the provisioner identifier.
func (p *Provisioner) Name() string {
	return ProvisionerName
}

// resolveImage replaces the :latest tag with the controller's own version tag.
// If the version is unknown ("dev"), dirty (contains "-g", indicating a
// non-release git describe like "0.8.1-324-g02cf8552"), or the image uses
// a non-latest tag (admin override), the image is returned unchanged.
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

// resolveImageSpec returns the image from an ImageSpec override, falling back to
// the default image passed through resolveImage. Also returns the pull policy.
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

// lookupParam walks a dot-separated path through nested
// map[string]interface{} values, returning (nil, false) on any miss.
func lookupParam(params map[string]interface{}, path string) (interface{}, bool) {
	var val interface{} = params
	for _, p := range splitDot(path) {
		m, ok := val.(map[string]interface{})
		if !ok {
			return nil, false
		}
		val, ok = m[p]
		if !ok {
			return nil, false
		}
	}
	return val, true
}

// hostOrchestratorPort returns parameters.hostOrchestrator.port,
// defaulting to defaultHostOrchestratorPort when absent. JSON-decoded
// numbers arrive as float64 and must be integral and in 1-65535.
func hostOrchestratorPort(params map[string]interface{}) (int32, error) {
	v, ok := lookupParam(params, paramKeyHOPort)
	if !ok {
		return defaultHostOrchestratorPort, nil
	}
	f, ok := v.(float64)
	if !ok || f != float64(int32(f)) || f < 1 || f > 65535 {
		return 0, fmt.Errorf("invalid parameters.hostOrchestrator.port %v: must be an integer in 1-65535", v)
	}
	return int32(f), nil
}

// storageSizeLimit returns parameters.storage.size as a Quantity, or
// nil when absent (unbounded emptyDir).
func storageSizeLimit(params map[string]interface{}) (*resource.Quantity, error) {
	v, ok := lookupParam(params, paramKeyStorageSize)
	if !ok {
		return nil, nil
	}
	s, ok := v.(string)
	if !ok {
		return nil, fmt.Errorf("invalid parameters.storage.size %v: must be a quantity string", v)
	}
	q, err := resource.ParseQuantity(s)
	if err != nil {
		return nil, fmt.Errorf("invalid parameters.storage.size %q: %w", s, err)
	}
	return &q, nil
}

// vsockEnabled returns parameters.vsock.enabled, defaulting to false
// when absent.
func vsockEnabled(params map[string]interface{}) (bool, error) {
	v, ok := lookupParam(params, paramKeyVsockEnabled)
	if !ok {
		return false, nil
	}
	b, ok := v.(bool)
	if !ok {
		return false, fmt.Errorf("invalid parameters.vsock.enabled %v: must be a boolean", v)
	}
	return b, nil
}

// RenderPod creates a Pod for a new Cuttlefish-based exporter instance
// using the native sidecar pattern (KEP-753):
//
//   - cuttlefish-host (native sidecar, restartPolicy: Always) runs the
//     Host Orchestrator; startup/readiness gate on its HTTP statusz
//     endpoint so the exporter starts against a live orchestrator.
//   - exporter (main container) runs `jmp run` — default kubectl logs
//     target; when it exits (exitOnLeaseEnd / ExitAndReplace),
//     Kubernetes terminates the sidecar and the Pod completes.
//     Pod restartPolicy is Never so a clean exporter exit is not
//     restarted in-place (ExporterSet replaces the instance instead).
//   - cuttlefish-state emptyDir holds CVD images and instance state.
//
// The caller (reconciler) is responsible for setting
// OwnerReferences on the Pod and injecting the config volume.
func (p *Provisioner) RenderPod(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	mergedParameters map[string]interface{},
	images *virtualtargetv1alpha1.ImageOverrides,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (*corev1.Pod, error) {
	// Parameter validation surfaces as RenderPod errors — the
	// reconciler turns them into events/conditions.
	hoPort, err := hostOrchestratorPort(mergedParameters)
	if err != nil {
		return nil, err
	}
	sizeLimit, err := storageSizeLimit(mergedParameters)
	if err != nil {
		return nil, err
	}
	vsock, err := vsockEnabled(mergedParameters)
	if err != nil {
		return nil, err
	}

	restartAlways := corev1.ContainerRestartPolicyAlways
	runAsExporter := exporterNonRootUID

	var exporterSpec, runtimeSpec *virtualtargetv1alpha1.ImageSpec
	if images != nil {
		exporterSpec = images.Exporter
		runtimeSpec = images.Runtime
	}

	exporterImage, exporterPullPolicy := p.resolveImageSpec(exporterSpec, DefaultExporterImage)
	runtimeImage, runtimePullPolicy := p.resolveImageSpec(runtimeSpec, DefaultCuttlefishRuntimeImage)

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

	statuszProbe := corev1.ProbeHandler{
		HTTPGet: &corev1.HTTPGetAction{
			Path: statuszPath,
			Port: intstr.FromInt32(hoPort),
		},
	}

	pod := &corev1.Pod{
		ObjectMeta: podMeta,
		Spec: corev1.PodSpec{
			// Never: ExitAndReplace relies on exporter (main) exit completing
			// the Pod. Always would restart jmp run in-place and skip recycle.
			RestartPolicy: corev1.RestartPolicyNever,
			InitContainers: []corev1.Container{
				{
					// Native sidecar: starts (and passes its startup probe)
					// before the main exporter so the Host Orchestrator is
					// reachable when jmp run begins. Torn down automatically
					// when the exporter (main) container exits.
					Name:            runtimeContainerName,
					Image:           runtimeImage,
					ImagePullPolicy: runtimePullPolicy,
					RestartPolicy:   &restartAlways,
					// Targeted grants only (JEP-0016 DD-5): NET_ADMIN for
					// the CVD TAP network, unconfined seccomp for the
					// crosvm sandbox. No root pin — deliberate deviation
					// from qemu's sidecar, mirrors the JEP YAML.
					SecurityContext: &corev1.SecurityContext{
						Capabilities: &corev1.Capabilities{
							Add: []corev1.Capability{"NET_ADMIN"},
						},
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeUnconfined,
						},
					},
					StartupProbe: &corev1.Probe{
						ProbeHandler:     statuszProbe,
						PeriodSeconds:    startupProbePeriodSeconds,
						FailureThreshold: startupProbeFailureThreshold,
					},
					ReadinessProbe: &corev1.Probe{
						ProbeHandler: statuszProbe,
					},
					VolumeMounts: []corev1.VolumeMount{
						{
							Name:      stateVolumeName,
							MountPath: stateMountPath,
						},
					},
				},
			},
			Containers: []corev1.Container{
				{
					Name:            exporterContainerName,
					Image:           exporterImage,
					ImagePullPolicy: exporterPullPolicy,
					Command: []string{
						"jmp", "run", "--exporter-config",
						exporterConfigPath,
					},
					// RuntimeDefault seccomp is a spec-mandated deviation
					// from the qemu exporter container (which omits it).
					SecurityContext: &corev1.SecurityContext{
						RunAsUser:    &runAsExporter,
						RunAsNonRoot: boolPtr(true),
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeRuntimeDefault,
						},
					},
				},
			},
			Volumes: []corev1.Volume{
				{
					Name: stateVolumeName,
					VolumeSource: corev1.VolumeSource{
						EmptyDir: &corev1.EmptyDirVolumeSource{
							// nil SizeLimit = unbounded when the
							// storage.size parameter is absent.
							SizeLimit: sizeLimit,
						},
					},
				},
			},
		},
	}

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
			// CVD resources belong on the runtime sidecar (where the
			// Host Orchestrator and crosvm run).
			for i := range pod.Spec.InitContainers {
				if pod.Spec.InitContainers[i].Name == runtimeContainerName {
					pod.Spec.InitContainers[i].Resources = *vtc.Spec.Scheduling.Resources.DeepCopy()
					break
				}
			}
		}
	}

	// vsock extended-resource claim, after the scheduling copy so it is
	// never clobbered. Limits only: extended-resource requests default
	// to limits.
	if vsock {
		for i := range pod.Spec.InitContainers {
			if pod.Spec.InitContainers[i].Name == runtimeContainerName {
				if pod.Spec.InitContainers[i].Resources.Limits == nil {
					pod.Spec.InitContainers[i].Resources.Limits = corev1.ResourceList{}
				}
				pod.Spec.InitContainers[i].Resources.Limits[vsockResourceName] = resource.MustParse("1")
				break
			}
		}
	}

	return pod, nil
}

// EnrichExporterExport injects Cuttlefish-specific driver configuration
// into the cuttlefish driver entry: host/port of the in-Pod Host
// Orchestrator, boot_timeout, and optional operator_port/env_config
// class defaults. Unlike qemu, nothing is force-overwritten and no
// wrapper drivers are appended — explicit template values always win
// and len(out) == len(in).
func (p *Provisioner) EnrichExporterExport(
	drivers []virtualtargetv1alpha1.DriverConfig,
	mergedParameters map[string]interface{},
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	result := make([]virtualtargetv1alpha1.DriverConfig, 0, len(drivers))

	for _, d := range drivers {
		if d.Type == cuttlefishDriverType {
			var err error
			d, err = enrichCuttlefishDriver(d, mergedParameters)
			if err != nil {
				return nil, err
			}
		}
		result = append(result, d)
	}

	return result, nil
}

// enrichCuttlefishDriver applies Cuttlefish-specific defaults to a
// driver config entry. Values are injected only when absent.
func enrichCuttlefishDriver(d virtualtargetv1alpha1.DriverConfig, params map[string]interface{}) (virtualtargetv1alpha1.DriverConfig, error) {
	config := map[string]interface{}{}
	if d.Config != nil && d.Config.Raw != nil {
		if err := json.Unmarshal(d.Config.Raw, &config); err != nil {
			return d, fmt.Errorf("unmarshal cuttlefish driver config: %w", err)
		}
	}

	hoPort, err := hostOrchestratorPort(params)
	if err != nil {
		return d, err
	}

	setIfAbsent(config, "host", "127.0.0.1")
	setIfAbsent(config, "port", float64(hoPort))
	setIfAbsent(config, "boot_timeout", float64(defaultBootTimeout))
	if v, ok := lookupParam(params, paramKeyOperatorPort); ok {
		setIfAbsent(config, "operator_port", v)
	}
	if v, ok := lookupParam(params, paramKeyEnvConfig); ok {
		setIfAbsent(config, "env_config", v)
		// A pool that pins a build boots it at instance start (DD-6):
		// the instance is the device, not a waiting host.
		setIfAbsent(config, "prewarm", true)
	}

	raw, _ := json.Marshal(config)
	d.Config = &apiextensionsv1.JSON{Raw: raw}
	return d, nil
}

// setIfAbsent sets config[key] = val unless the key is already present
// or val is nil.
func setIfAbsent(config map[string]interface{}, key string, val interface{}) {
	if val == nil {
		return
	}
	if _, exists := config[key]; exists {
		return
	}
	config[key] = val
}

func splitDot(s string) []string {
	result := make([]string, 0, 2)
	start := 0
	for i := range s {
		if s[i] == '.' {
			result = append(result, s[start:i])
			start = i + 1
		}
	}
	result = append(result, s[start:])
	return result
}

func mustJSON(v interface{}) *apiextensionsv1.JSON {
	raw, _ := json.Marshal(v)
	return &apiextensionsv1.JSON{Raw: raw}
}

func boolPtr(v bool) *bool {
	return &v
}

// Cleanup handles teardown of Cuttlefish-based exporter instances.
// For in-cluster Cuttlefish this is a no-op: deleting the Pod (via
// OwnerReference cascade) handles teardown, and InPlaceReuse resets
// are driver-side (reset_host), not a provisioner concern.
func (p *Provisioner) Cleanup(
	ctx context.Context,
	exporterSet *virtualtargetv1alpha1.ExporterSet,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	return nil
}
