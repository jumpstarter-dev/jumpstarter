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

package cuttlefish

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const mutatedValue = "mutated"

func testExporterSet() *virtualtargetv1alpha1.ExporterSet {
	return &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cuttlefish-ci",
			Namespace: "jumpstarter",
		},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			Template: virtualtargetv1alpha1.ExporterSetTemplate{
				Metadata: virtualtargetv1alpha1.EmbeddedObjectMeta{
					Labels: map[string]string{
						"device": "cuttlefish",
						"os":     "android",
					},
					Annotations: map[string]string{
						"example.com/owner": "team-android",
					},
				},
			},
		},
	}
}

func testVTC() *virtualtargetv1alpha1.VirtualTargetClass {
	return &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner: ProvisionerName,
		},
	}
}

func testVTCWithScheduling() *virtualtargetv1alpha1.VirtualTargetClass {
	vtc := testVTC()
	vtc.Spec.Scheduling = &virtualtargetv1alpha1.SchedulingSpec{
		NodeSelector: map[string]string{
			"jumpstarter.dev/kvm": "true",
		},
		Tolerations: []corev1.Toleration{
			{Key: "dedicated", Operator: corev1.TolerationOpEqual, Value: "virtual"},
		},
		Resources: &corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("4"),
				corev1.ResourceMemory: resource.MustParse("8Gi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceName("devices.kubevirt.io/kvm"):       resource.MustParse("1"),
				corev1.ResourceName("devices.kubevirt.io/tun"):       resource.MustParse("1"),
				corev1.ResourceName("devices.kubevirt.io/vhost-net"): resource.MustParse("1"),
			},
		},
	}
	return vtc
}

func testExporter() *jumpstarterdevv1alpha1.Exporter {
	return &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "cuttlefish-ci-aaa",
			Namespace: "jumpstarter",
		},
	}
}

// params unmarshals a JSON literal so parameter values have the same
// dynamic types (float64 numbers) as deepMergeParameters output.
func params(t *testing.T, jsonStr string) map[string]interface{} {
	t.Helper()
	var m map[string]interface{}
	if err := json.Unmarshal([]byte(jsonStr), &m); err != nil {
		t.Fatalf("unmarshal params: %v", err)
	}
	return m
}

// assertSidecar fatals unless the pod has exactly one init container
// named cuttlefish-host and returns a pointer to it.
func assertSidecar(t *testing.T, pod *corev1.Pod) *corev1.Container {
	t.Helper()
	if len(pod.Spec.InitContainers) != 1 {
		t.Fatalf("expected 1 init container, got %d: %#v", len(pod.Spec.InitContainers), pod.Spec.InitContainers)
	}
	if pod.Spec.InitContainers[0].Name != runtimeContainerName {
		t.Fatalf("InitContainers[0].Name = %q, want %q", pod.Spec.InitContainers[0].Name, runtimeContainerName)
	}
	return &pod.Spec.InitContainers[0]
}

// assertExporterContainer fatals unless the pod has exactly one main
// container named exporter and returns a pointer to it.
func assertExporterContainer(t *testing.T, pod *corev1.Pod) *corev1.Container {
	t.Helper()
	if len(pod.Spec.Containers) != 1 {
		t.Fatalf("expected 1 container, got %d: %#v", len(pod.Spec.Containers), pod.Spec.Containers)
	}
	if pod.Spec.Containers[0].Name != exporterContainerName {
		t.Fatalf("Containers[0].Name = %q, want %q", pod.Spec.Containers[0].Name, exporterContainerName)
	}
	return &pod.Spec.Containers[0]
}

func assertHTTPProbe(t *testing.T, name string, probe *corev1.Probe, port int32, period, failureThreshold int32) {
	t.Helper()
	if probe == nil {
		t.Fatalf("%s probe is nil", name)
	}
	if probe.HTTPGet == nil {
		t.Fatalf("%s probe HTTPGet is nil", name)
	}
	if probe.HTTPGet.Path != statuszPath {
		t.Errorf("%s probe path = %q, want %q", name, probe.HTTPGet.Path, statuszPath)
	}
	if got := probe.HTTPGet.Port.IntValue(); got != int(port) {
		t.Errorf("%s probe port = %d, want %d", name, got, port)
	}
	if probe.PeriodSeconds != period {
		t.Errorf("%s probe PeriodSeconds = %d, want %d", name, probe.PeriodSeconds, period)
	}
	if probe.FailureThreshold != failureThreshold {
		t.Errorf("%s probe FailureThreshold = %d, want %d", name, probe.FailureThreshold, failureThreshold)
	}
}

func TestName(t *testing.T) {
	if got := New("dev").Name(); got != "cuttlefish.jumpstarter.dev" {
		t.Errorf("Name() = %q, want cuttlefish.jumpstarter.dev", got)
	}
}

func TestRenderPodMetadata(t *testing.T) {
	exporterSet := testExporterSet()
	pod, err := New("dev").RenderPod(context.Background(), exporterSet, testVTC(), nil, nil, testExporter())
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	if pod.Namespace != "jumpstarter" {
		t.Errorf("Namespace = %q, want jumpstarter", pod.Namespace)
	}
	if got := pod.Labels["device"]; got != "cuttlefish" {
		t.Errorf("Labels[device] = %q, want cuttlefish", got)
	}
	if got := pod.Labels["os"]; got != "android" {
		t.Errorf("Labels[os] = %q, want android", got)
	}
	if got := pod.Annotations["example.com/owner"]; got != "team-android" {
		t.Errorf("Annotations[example.com/owner] = %q, want team-android", got)
	}
	if pod.Name != "cuttlefish-ci-aaa" {
		t.Errorf("Name = %q, want cuttlefish-ci-aaa (must match Exporter name)", pod.Name)
	}
	if pod.GenerateName != "" {
		t.Errorf("GenerateName = %q, want empty when exporter is provided", pod.GenerateName)
	}

	// Mutations on the pod must not affect the ExporterSet template.
	pod.Labels["device"] = mutatedValue
	pod.Annotations["example.com/owner"] = mutatedValue
	if got := exporterSet.Spec.Template.Metadata.Labels["device"]; got != "cuttlefish" {
		t.Errorf("ExporterSet labels mutated: got %q", got)
	}
	if got := exporterSet.Spec.Template.Metadata.Annotations["example.com/owner"]; got != "team-android" {
		t.Errorf("ExporterSet annotations mutated: got %q", got)
	}
}

func TestRenderPodGenerateNameWithoutExporter(t *testing.T) {
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}
	if pod.GenerateName != "cuttlefish-ci-" {
		t.Errorf("GenerateName = %q, want cuttlefish-ci-", pod.GenerateName)
	}
	if pod.Name != "" {
		t.Errorf("Name = %q, want empty without exporter", pod.Name)
	}
}

func TestRenderPodShape(t *testing.T) {
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	if pod.Spec.RestartPolicy != corev1.RestartPolicyNever {
		t.Errorf("RestartPolicy = %q, want Never (ExitAndReplace)", pod.Spec.RestartPolicy)
	}

	sidecar := assertSidecar(t, pod)
	if sidecar.RestartPolicy == nil || *sidecar.RestartPolicy != corev1.ContainerRestartPolicyAlways {
		t.Errorf("cuttlefish-host RestartPolicy = %v, want Always (native sidecar)", sidecar.RestartPolicy)
	}
	if sidecar.Image != DefaultCuttlefishRuntimeImage {
		t.Errorf("cuttlefish-host image = %q, want %q", sidecar.Image, DefaultCuttlefishRuntimeImage)
	}

	exporter := assertExporterContainer(t, pod)
	if exporter.Image != DefaultExporterImage {
		t.Errorf("exporter image = %q, want %q", exporter.Image, DefaultExporterImage)
	}
	wantCommand := []string{"jmp", "run", "--exporter-config", "/etc/jumpstarter/exporters/config.yaml"}
	if len(exporter.Command) != len(wantCommand) {
		t.Fatalf("exporter Command = %v, want %v", exporter.Command, wantCommand)
	}
	for i := range wantCommand {
		if exporter.Command[i] != wantCommand[i] {
			t.Fatalf("exporter Command = %v, want %v", exporter.Command, wantCommand)
		}
	}

	// Only the state volume — config volume is injected by the reconciler.
	if len(pod.Spec.Volumes) != 1 {
		t.Fatalf("expected 1 volume (cuttlefish-state), got %d", len(pod.Spec.Volumes))
	}
	if pod.Spec.Volumes[0].Name != stateVolumeName {
		t.Errorf("Volumes[0].Name = %q, want %q", pod.Spec.Volumes[0].Name, stateVolumeName)
	}
	if pod.Spec.Volumes[0].EmptyDir == nil {
		t.Fatal("expected cuttlefish-state emptyDir volume at index 0")
	}
}

func TestRenderPodNoQemuMachinery(t *testing.T) {
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), nil, nil, testExporter())
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	all := append([]corev1.Container{}, pod.Spec.InitContainers...)
	all = append(all, pod.Spec.Containers...)
	for _, c := range all {
		if c.Name == "copy-jumpstarter-exec" {
			t.Error("unexpected copy-jumpstarter-exec container (no jumpstarter-exec staging for cuttlefish)")
		}
		for _, e := range c.Env {
			if e.Name == "JUMPSTARTER_LAUNCHER_SOCKET" || e.Name == "JUMPSTARTER_EXEC_LOG_FIELDS" {
				t.Errorf("container %s has unexpected env %s", c.Name, e.Name)
			}
		}
	}

	sidecar := assertSidecar(t, pod)
	foundState := false
	for _, m := range sidecar.VolumeMounts {
		if m.Name == stateVolumeName && m.MountPath == stateMountPath {
			foundState = true
		}
	}
	if !foundState {
		t.Errorf("cuttlefish-host missing VolumeMount %s -> %s; got %#v", stateVolumeName, stateMountPath, sidecar.VolumeMounts)
	}

	exporter := assertExporterContainer(t, pod)
	if len(exporter.VolumeMounts) != 0 {
		t.Errorf("exporter VolumeMounts = %#v, want none (reconciler injects config volume)", exporter.VolumeMounts)
	}
	if len(exporter.Env) != 0 {
		t.Errorf("exporter Env = %#v, want none", exporter.Env)
	}
}

func TestRenderPodSecurityContexts(t *testing.T) {
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	sidecar := assertSidecar(t, pod)
	sc := sidecar.SecurityContext
	if sc == nil {
		t.Fatal("cuttlefish-host SecurityContext is nil")
	}
	if sc.Capabilities == nil || len(sc.Capabilities.Add) != 1 || sc.Capabilities.Add[0] != "NET_ADMIN" {
		t.Errorf("cuttlefish-host Capabilities = %#v, want Add [NET_ADMIN]", sc.Capabilities)
	}
	if sc.SeccompProfile == nil || sc.SeccompProfile.Type != corev1.SeccompProfileTypeUnconfined {
		t.Errorf("cuttlefish-host SeccompProfile = %#v, want Unconfined", sc.SeccompProfile)
	}
	if sc.Privileged != nil {
		t.Errorf("cuttlefish-host Privileged = %v, want nil", *sc.Privileged)
	}
	if sc.RunAsUser != nil {
		t.Errorf("cuttlefish-host RunAsUser = %v, want nil (no root pin, per JEP YAML)", *sc.RunAsUser)
	}

	exporter := assertExporterContainer(t, pod)
	esc := exporter.SecurityContext
	if esc == nil {
		t.Fatal("exporter SecurityContext is nil")
	}
	if esc.RunAsUser == nil || *esc.RunAsUser != exporterNonRootUID {
		t.Errorf("exporter RunAsUser = %v, want %d", esc.RunAsUser, exporterNonRootUID)
	}
	if esc.RunAsNonRoot == nil || !*esc.RunAsNonRoot {
		t.Errorf("exporter RunAsNonRoot = %v, want true", esc.RunAsNonRoot)
	}
	if esc.SeccompProfile == nil || esc.SeccompProfile.Type != corev1.SeccompProfileTypeRuntimeDefault {
		t.Errorf("exporter SeccompProfile = %#v, want RuntimeDefault", esc.SeccompProfile)
	}
	if esc.Capabilities != nil && len(esc.Capabilities.Add) != 0 {
		t.Errorf("exporter has added capabilities: %#v", esc.Capabilities.Add)
	}

	if pod.Spec.HostNetwork {
		t.Error("pod uses HostNetwork, want false")
	}
	for _, v := range pod.Spec.Volumes {
		if v.HostPath != nil {
			t.Errorf("pod has hostPath volume %q", v.Name)
		}
	}
}

func TestRenderPodProbes(t *testing.T) {
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	sidecar := assertSidecar(t, pod)
	assertHTTPProbe(t, "startup", sidecar.StartupProbe, defaultHostOrchestratorPort, startupProbePeriodSeconds, startupProbeFailureThreshold)
	if sidecar.ReadinessProbe == nil || sidecar.ReadinessProbe.HTTPGet == nil {
		t.Fatal("cuttlefish-host ReadinessProbe HTTPGet is nil")
	}
	if sidecar.ReadinessProbe.HTTPGet.Path != statuszPath {
		t.Errorf("readiness probe path = %q, want %q", sidecar.ReadinessProbe.HTTPGet.Path, statuszPath)
	}
	if got := sidecar.ReadinessProbe.HTTPGet.Port.IntValue(); got != int(defaultHostOrchestratorPort) {
		t.Errorf("readiness probe port = %d, want %d", got, defaultHostOrchestratorPort)
	}

	exporter := assertExporterContainer(t, pod)
	if exporter.StartupProbe != nil || exporter.ReadinessProbe != nil || exporter.LivenessProbe != nil {
		t.Error("exporter container must have no probes")
	}
}

func TestRenderPodProbePortFollowsParameter(t *testing.T) {
	p := params(t, `{"hostOrchestrator": {"port": 3080}}`)
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), p, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	sidecar := assertSidecar(t, pod)
	if got := sidecar.StartupProbe.HTTPGet.Port.IntValue(); got != 3080 {
		t.Errorf("startup probe port = %d, want 3080", got)
	}
	if got := sidecar.ReadinessProbe.HTTPGet.Port.IntValue(); got != 3080 {
		t.Errorf("readiness probe port = %d, want 3080", got)
	}
}

func TestRenderPodInvalidHOPort(t *testing.T) {
	cases := []struct {
		name string
		port interface{}
	}{
		{"zero", float64(0)},
		{"too-large", float64(65536)},
		{"fractional", float64(2080.5)},
		{"string", "2080"},
	}
	for _, tc := range cases {
		p := map[string]interface{}{
			"hostOrchestrator": map[string]interface{}{"port": tc.port},
		}
		_, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), p, nil, nil)
		if err == nil {
			t.Errorf("%s: RenderPod() error = nil, want error", tc.name)
			continue
		}
		if !strings.Contains(err.Error(), "hostOrchestrator.port") {
			t.Errorf("%s: error %q does not name parameters.hostOrchestrator.port", tc.name, err)
		}
	}
}

func TestRenderPodStorageSizeLimit(t *testing.T) {
	p := params(t, `{"storage": {"size": "40Gi"}}`)
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), p, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}
	want := resource.MustParse("40Gi")
	got := pod.Spec.Volumes[0].EmptyDir.SizeLimit
	if got == nil || !got.Equal(want) {
		t.Errorf("cuttlefish-state SizeLimit = %v, want %v", got, want)
	}
}

func TestRenderPodStorageSizeAbsent(t *testing.T) {
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}
	if got := pod.Spec.Volumes[0].EmptyDir.SizeLimit; got != nil {
		t.Errorf("cuttlefish-state SizeLimit = %v, want nil when storage.size absent", got)
	}
}

func TestRenderPodInvalidStorageSize(t *testing.T) {
	p := params(t, `{"storage": {"size": "not-a-quantity"}}`)
	_, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), p, nil, nil)
	if err == nil {
		t.Fatal("RenderPod() error = nil, want error for invalid storage.size")
	}
	if !strings.Contains(err.Error(), "storage.size") {
		t.Errorf("error %q does not name parameters.storage.size", err)
	}
}

func TestRenderPodVsockClaim(t *testing.T) {
	one := resource.MustParse("1")

	// (a) enabled with Scheduling.Resources set.
	p := params(t, `{"vsock": {"enabled": true}}`)
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTCWithScheduling(), p, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}
	sidecar := assertSidecar(t, pod)
	got, ok := sidecar.Resources.Limits[vsockResourceName]
	if !ok || !got.Equal(one) {
		t.Errorf("sidecar Limits[%s] = %v (present=%v), want 1", vsockResourceName, got, ok)
	}

	// (b) enabled with Scheduling nil — nil-map guard.
	pod, err = New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), p, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}
	sidecar = assertSidecar(t, pod)
	got, ok = sidecar.Resources.Limits[vsockResourceName]
	if !ok || !got.Equal(one) {
		t.Errorf("sidecar Limits[%s] = %v (present=%v), want 1 with nil Scheduling", vsockResourceName, got, ok)
	}

	// (c) disabled and absent — no vhost-vsock key anywhere.
	for _, pp := range []map[string]interface{}{
		params(t, `{"vsock": {"enabled": false}}`),
		nil,
	} {
		pod, err = New("dev").RenderPod(context.Background(), testExporterSet(), testVTCWithScheduling(), pp, nil, nil)
		if err != nil {
			t.Fatalf("RenderPod() error = %v", err)
		}
		for _, c := range append(append([]corev1.Container{}, pod.Spec.InitContainers...), pod.Spec.Containers...) {
			if _, ok := c.Resources.Limits[vsockResourceName]; ok {
				t.Errorf("container %s has unexpected %s limit", c.Name, vsockResourceName)
			}
			if _, ok := c.Resources.Requests[vsockResourceName]; ok {
				t.Errorf("container %s has unexpected %s request", c.Name, vsockResourceName)
			}
		}
	}

	// (d) non-bool vsock.enabled is a validation error.
	p = params(t, `{"vsock": {"enabled": "yes"}}`)
	_, err = New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), p, nil, nil)
	if err == nil {
		t.Fatal("RenderPod() error = nil, want error for non-bool vsock.enabled")
	}
	if !strings.Contains(err.Error(), "vsock.enabled") {
		t.Errorf("error %q does not name parameters.vsock.enabled", err)
	}
}

func TestRenderPodScheduling(t *testing.T) {
	vtc := testVTCWithScheduling()
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), vtc, nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	if got := pod.Spec.NodeSelector["jumpstarter.dev/kvm"]; got != "true" {
		t.Errorf("NodeSelector[jumpstarter.dev/kvm] = %q, want true", got)
	}
	if len(pod.Spec.Tolerations) != 1 || pod.Spec.Tolerations[0].Value != "virtual" {
		t.Errorf("Tolerations = %#v, want the VTC toleration", pod.Spec.Tolerations)
	}

	sidecar := assertSidecar(t, pod)
	wantCPU := resource.MustParse("4")
	gotCPU := sidecar.Resources.Requests[corev1.ResourceCPU]
	if !gotCPU.Equal(wantCPU) {
		t.Errorf("sidecar CPU request = %v, want %v", gotCPU, wantCPU)
	}
	one := resource.MustParse("1")
	for _, name := range []string{"devices.kubevirt.io/kvm", "devices.kubevirt.io/tun", "devices.kubevirt.io/vhost-net"} {
		got, ok := sidecar.Resources.Limits[corev1.ResourceName(name)]
		if !ok || !got.Equal(one) {
			t.Errorf("sidecar Limits[%s] = %v (present=%v), want 1", name, got, ok)
		}
	}

	exporter := assertExporterContainer(t, pod)
	if len(exporter.Resources.Requests) != 0 || len(exporter.Resources.Limits) != 0 {
		t.Errorf("exporter Resources = %#v, want empty (CVD resources go to sidecar)", exporter.Resources)
	}
}

func TestRenderPodDefensiveCopies(t *testing.T) {
	exporterSet := testExporterSet()
	vtc := testVTCWithScheduling()
	pod, err := New("dev").RenderPod(context.Background(), exporterSet, vtc, nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	pod.Labels["device"] = mutatedValue
	pod.Annotations["example.com/owner"] = mutatedValue
	pod.Spec.NodeSelector["jumpstarter.dev/kvm"] = mutatedValue
	pod.Spec.Tolerations[0].Value = mutatedValue
	sidecar := assertSidecar(t, pod)
	sidecar.Resources.Limits[corev1.ResourceName("devices.kubevirt.io/kvm")] = resource.MustParse("7")

	if got := exporterSet.Spec.Template.Metadata.Labels["device"]; got != "cuttlefish" {
		t.Errorf("ExporterSet labels mutated: got %q", got)
	}
	if got := exporterSet.Spec.Template.Metadata.Annotations["example.com/owner"]; got != "team-android" {
		t.Errorf("ExporterSet annotations mutated: got %q", got)
	}
	if got := vtc.Spec.Scheduling.NodeSelector["jumpstarter.dev/kvm"]; got != "true" {
		t.Errorf("VTC NodeSelector mutated: got %q", got)
	}
	if got := vtc.Spec.Scheduling.Tolerations[0].Value; got != "virtual" {
		t.Errorf("VTC Tolerations mutated: got %q", got)
	}
	one := resource.MustParse("1")
	if got := vtc.Spec.Scheduling.Resources.Limits[corev1.ResourceName("devices.kubevirt.io/kvm")]; !got.Equal(one) {
		t.Errorf("VTC Resources mutated: got %v", got)
	}
}

func TestRenderPodImageOverrides(t *testing.T) {
	// nil images: both defaults with PullIfNotPresent.
	pod, err := New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}
	sidecar := assertSidecar(t, pod)
	exporter := assertExporterContainer(t, pod)
	if sidecar.Image != DefaultCuttlefishRuntimeImage || sidecar.ImagePullPolicy != corev1.PullIfNotPresent {
		t.Errorf("sidecar image/pull = %q/%q, want default/IfNotPresent", sidecar.Image, sidecar.ImagePullPolicy)
	}
	if exporter.Image != DefaultExporterImage || exporter.ImagePullPolicy != corev1.PullIfNotPresent {
		t.Errorf("exporter image/pull = %q/%q, want default/IfNotPresent", exporter.Image, exporter.ImagePullPolicy)
	}

	// Overrides applied independently.
	images := &virtualtargetv1alpha1.ImageOverrides{
		Runtime: &virtualtargetv1alpha1.ImageSpec{
			Image:           "custom/cf:1",
			ImagePullPolicy: corev1.PullAlways,
		},
		Exporter: &virtualtargetv1alpha1.ImageSpec{
			Image: "custom/jmp:1",
		},
	}
	pod, err = New("dev").RenderPod(context.Background(), testExporterSet(), testVTC(), nil, images, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}
	sidecar = assertSidecar(t, pod)
	exporter = assertExporterContainer(t, pod)
	if sidecar.Image != "custom/cf:1" {
		t.Errorf("sidecar image = %q, want custom/cf:1", sidecar.Image)
	}
	if sidecar.ImagePullPolicy != corev1.PullAlways {
		t.Errorf("sidecar pullPolicy = %q, want Always", sidecar.ImagePullPolicy)
	}
	if exporter.Image != "custom/jmp:1" {
		t.Errorf("exporter image = %q, want custom/jmp:1", exporter.Image)
	}
	if exporter.ImagePullPolicy != corev1.PullIfNotPresent {
		t.Errorf("exporter pullPolicy = %q, want IfNotPresent (default)", exporter.ImagePullPolicy)
	}
}

func TestResolveImage(t *testing.T) {
	cases := []struct {
		version, image, want string
	}{
		{"", DefaultCuttlefishRuntimeImage, DefaultCuttlefishRuntimeImage},
		{"dev", DefaultCuttlefishRuntimeImage, DefaultCuttlefishRuntimeImage},
		{"0.8.1-324-g02cf8552", DefaultCuttlefishRuntimeImage, DefaultCuttlefishRuntimeImage},
		{"v0.9.0", DefaultCuttlefishRuntimeImage, "quay.io/jumpstarter-dev/virtual/cuttlefish-runtime:0.9.0"},
		{"v0.9.0-rc.1", DefaultExporterImage, "quay.io/jumpstarter-dev/jumpstarter:0.9.0-rc.1"},
		{"v0.9.0", "quay.io/custom/image:v2.0", "quay.io/custom/image:v2.0"},
	}
	for _, tc := range cases {
		got := New(tc.version).resolveImage(tc.image)
		if got != tc.want {
			t.Errorf("New(%q).resolveImage(%q) = %q, want %q", tc.version, tc.image, got, tc.want)
		}
	}
}

func TestCleanupNoop(t *testing.T) {
	if err := New("dev").Cleanup(context.Background(), testExporterSet(), testExporter()); err != nil {
		t.Errorf("Cleanup() error = %v, want nil", err)
	}
}
