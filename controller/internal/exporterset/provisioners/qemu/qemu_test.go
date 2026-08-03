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

package qemu

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const mutatedValue = "mutated"

func TestProvisioner_Name(t *testing.T) {
	if got := New("dev").Name(); got != ProvisionerName {
		t.Errorf("Name() = %q, want %q", got, ProvisionerName)
	}
}

func TestRenderPod_copiesMetadataAndAppliesDefaults(t *testing.T) {
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "demo-set",
			Namespace: "default",
		},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			Template: virtualtargetv1alpha1.ExporterSetTemplate{
				Metadata: virtualtargetv1alpha1.EmbeddedObjectMeta{
					Labels: map[string]string{
						"app": "demo",
					},
					Annotations: map[string]string{
						"example.com/owner": "team-a",
					},
				},
			},
		},
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner: ProvisionerName,
		},
	}

	pod, err := New("dev").RenderPod(context.Background(), exporterSet, vtc, nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	if pod.GenerateName != "demo-set-" {
		t.Errorf("GenerateName = %q, want %q", pod.GenerateName, "demo-set-")
	}
	if pod.Namespace != "default" {
		t.Errorf("Namespace = %q, want %q", pod.Namespace, "default")
	}
	if got := pod.Labels["app"]; got != "demo" {
		t.Errorf("Labels[app] = %q, want %q", got, "demo")
	}
	if got := pod.Annotations["example.com/owner"]; got != "team-a" {
		t.Errorf("Annotations[example.com/owner] = %q, want %q", got, "team-a")
	}

	// Mutations on the pod must not affect the ExporterSet template.
	pod.Labels["app"] = mutatedValue
	pod.Annotations["example.com/owner"] = mutatedValue
	if got := exporterSet.Spec.Template.Metadata.Labels["app"]; got != "demo" {
		t.Errorf("ExporterSet labels mutated: got %q", got)
	}
	if got := exporterSet.Spec.Template.Metadata.Annotations["example.com/owner"]; got != "team-a" {
		t.Errorf("ExporterSet annotations mutated: got %q", got)
	}

	// Shared emptyDir + guest disk emptyDir (default size). Config volume is
	// injected by the reconciler.
	if len(pod.Spec.Volumes) != 2 {
		t.Fatalf("expected 2 volumes (shared + disk), got %d", len(pod.Spec.Volumes))
	}
	if pod.Spec.Volumes[0].Name != sharedVolumeName || pod.Spec.Volumes[0].EmptyDir == nil {
		t.Fatalf("expected shared emptyDir volume at index 0, got %#v", pod.Spec.Volumes[0])
	}
	wantLimit := resource.MustParse(sharedVolumeSizeLimit)
	if pod.Spec.Volumes[0].EmptyDir.SizeLimit == nil ||
		!pod.Spec.Volumes[0].EmptyDir.SizeLimit.Equal(wantLimit) {
		t.Errorf("shared SizeLimit = %v, want %v", pod.Spec.Volumes[0].EmptyDir.SizeLimit, wantLimit)
	}
	if pod.Spec.Volumes[1].Name != "disk" || pod.Spec.Volumes[1].EmptyDir == nil {
		t.Fatalf("expected disk emptyDir volume at index 1, got %#v", pod.Spec.Volumes[1])
	}
	wantDisk := resource.MustParse("10Gi")
	if pod.Spec.Volumes[1].EmptyDir.SizeLimit == nil ||
		!pod.Spec.Volumes[1].EmptyDir.SizeLimit.Equal(wantDisk) {
		t.Errorf("disk SizeLimit = %v, want %v", pod.Spec.Volumes[1].EmptyDir.SizeLimit, wantDisk)
	}

	ephemeral := pod.Spec.Containers[0].Resources.Requests[corev1.ResourceEphemeralStorage]
	if !ephemeral.Equal(wantDisk) {
		t.Errorf("runtime ephemeral-storage request = %v, want %v", ephemeral, wantDisk)
	}
	exporterEphemeral := pod.Spec.InitContainers[1].Resources.Requests[corev1.ResourceEphemeralStorage]
	if !exporterEphemeral.Equal(wantDisk) {
		t.Errorf("exporter ephemeral-storage request = %v, want %v", exporterEphemeral, wantDisk)
	}

	hasDiskMount := false
	for _, m := range pod.Spec.Containers[0].VolumeMounts {
		if m.Name == "disk" && m.MountPath == "/disk" {
			hasDiskMount = true
		}
	}
	if !hasDiskMount {
		t.Error("target-runtime missing /disk mount")
	}

	if len(pod.Spec.InitContainers) != 2 {
		t.Fatalf("unexpected init containers: %#v", pod.Spec.InitContainers)
	}
	copyInit := pod.Spec.InitContainers[0]
	if copyInit.Name != "copy-jumpstarter-exec" {
		t.Errorf("InitContainers[0].Name = %q, want copy-jumpstarter-exec", copyInit.Name)
	}
	if copyInit.RestartPolicy != nil {
		t.Errorf("copy-jumpstarter-exec RestartPolicy = %v, want nil (one-shot init)", copyInit.RestartPolicy)
	}
	exporterInit := pod.Spec.InitContainers[1]
	if exporterInit.Name != "exporter" {
		t.Errorf("InitContainers[1].Name = %q, want exporter", exporterInit.Name)
	}
	if exporterInit.RestartPolicy == nil || *exporterInit.RestartPolicy != corev1.ContainerRestartPolicyAlways {
		t.Errorf("exporter RestartPolicy = %v, want Always", exporterInit.RestartPolicy)
	}
	if len(pod.Spec.Containers) != 1 || pod.Spec.Containers[0].Name != "target-runtime" {
		t.Errorf("unexpected containers: %#v", pod.Spec.Containers)
	}
}

func TestRenderPod_clonesSchedulingFromVTC(t *testing.T) {
	cpu := resource.MustParse("500m")
	mem := resource.MustParse("512Mi")
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner: ProvisionerName,
			Scheduling: &virtualtargetv1alpha1.SchedulingSpec{
				NodeSelector: map[string]string{
					"node-role.kubernetes.io/worker": "",
				},
				Tolerations: []corev1.Toleration{
					{Key: "dedicated", Operator: corev1.TolerationOpEqual, Value: "virtual"},
				},
				Resources: &corev1.ResourceRequirements{
					Requests: corev1.ResourceList{
						corev1.ResourceCPU:    cpu,
						corev1.ResourceMemory: mem,
					},
				},
			},
		},
	}
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "default"},
	}

	pod, err := New("dev").RenderPod(context.Background(), exporterSet, vtc, nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	if got := pod.Spec.NodeSelector["node-role.kubernetes.io/worker"]; got != "" {
		t.Errorf("NodeSelector value = %q, want empty string", got)
	}
	pod.Spec.NodeSelector["node-role.kubernetes.io/worker"] = mutatedValue
	if _, ok := vtc.Spec.Scheduling.NodeSelector["node-role.kubernetes.io/worker"]; !ok {
		t.Fatal("VTC NodeSelector key unexpectedly removed")
	}
	if got := vtc.Spec.Scheduling.NodeSelector["node-role.kubernetes.io/worker"]; got != "" {
		t.Errorf("VTC NodeSelector mutated: got %q", got)
	}

	if len(pod.Spec.Tolerations) != 1 {
		t.Fatalf("Tolerations len = %d, want 1", len(pod.Spec.Tolerations))
	}
	pod.Spec.Tolerations[0].Value = mutatedValue
	if got := vtc.Spec.Scheduling.Tolerations[0].Value; got != "virtual" {
		t.Errorf("VTC Tolerations mutated: got %q", got)
	}

	gotCPU := pod.Spec.Containers[0].Resources.Requests[corev1.ResourceCPU]
	if !gotCPU.Equal(cpu) {
		t.Errorf("CPU request = %v, want %v", gotCPU, cpu)
	}
	pod.Spec.Containers[0].Resources.Requests[corev1.ResourceCPU] = resource.MustParse("1")
	if got := vtc.Spec.Scheduling.Resources.Requests[corev1.ResourceCPU]; !got.Equal(cpu) {
		t.Errorf("VTC Resources mutated: got %v", got)
	}
}

func TestRenderPod_injectsJumpstarterExecLogFields(t *testing.T) {
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "jumpstarter-lab"},
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{Provisioner: ProvisionerName},
	}
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "demo-set-abc12",
			Namespace: "jumpstarter-lab",
		},
	}

	pod, err := New("dev").RenderPod(context.Background(), exporterSet, vtc, nil, nil, exporter)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	if pod.Name != exporter.Name {
		t.Errorf("Pod.Name = %q, want %q (must match Exporter name)", pod.Name, exporter.Name)
	}
	if pod.GenerateName != "" {
		t.Errorf("Pod.GenerateName = %q, want empty when exporter is provided", pod.GenerateName)
	}

	env := pod.Spec.Containers[0].Env
	var got string
	for _, e := range env {
		if e.Name == "JUMPSTARTER_EXEC_LOG_FIELDS" {
			got = e.Value
			break
		}
	}
	want := "component=exporter,exporter=demo-set-abc12,namespace=jumpstarter-lab"
	if got != want {
		t.Errorf("JUMPSTARTER_EXEC_LOG_FIELDS = %q, want %q", got, want)
	}
}

func TestResolveImage_devPreservesLatest(t *testing.T) {
	p := New("dev")
	got := p.resolveImage(DefaultExporterImage)
	if got != DefaultExporterImage {
		t.Errorf("resolveImage() = %q, want :latest preserved for dev", got)
	}
}

func TestResolveImage_emptyVersionPreservesLatest(t *testing.T) {
	p := New("")
	got := p.resolveImage(DefaultExporterImage)
	if got != DefaultExporterImage {
		t.Errorf("resolveImage() = %q, want :latest preserved for empty version", got)
	}
}

func TestResolveImage_taggedVersionResolvesLatest(t *testing.T) {
	p := New("v0.9.0")
	got := p.resolveImage(DefaultExporterImage)
	want := "quay.io/jumpstarter-dev/jumpstarter:0.9.0"
	if got != want {
		t.Errorf("resolveImage() = %q, want %q", got, want)
	}
}

func TestResolveImage_rcVersionResolvesLatest(t *testing.T) {
	p := New("v0.9.0-rc.1")
	got := p.resolveImage(DefaultExporterImage)
	want := "quay.io/jumpstarter-dev/jumpstarter:0.9.0-rc.1"
	if got != want {
		t.Errorf("resolveImage() = %q, want %q", got, want)
	}
}

func TestResolveImage_adminOverridePassesThrough(t *testing.T) {
	p := New("v0.9.0")
	got := p.resolveImage("quay.io/custom/image:v2.0")
	if got != "quay.io/custom/image:v2.0" {
		t.Errorf("resolveImage() = %q, want admin override unchanged", got)
	}
}

func TestRenderPod_usesResolvedImages(t *testing.T) {
	p := New("v1.2.3")
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "default"},
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{Provisioner: ProvisionerName},
	}

	pod, err := p.RenderPod(context.Background(), exporterSet, vtc, nil, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	wantExporter := "quay.io/jumpstarter-dev/jumpstarter:1.2.3"
	wantRuntime := "quay.io/jumpstarter-dev/virtual/qemu-runtime:1.2.3"

	if pod.Spec.InitContainers[0].Image != wantExporter {
		t.Errorf("copy-jumpstarter-exec image = %q, want %q", pod.Spec.InitContainers[0].Image, wantExporter)
	}
	if pod.Spec.InitContainers[1].Image != wantExporter {
		t.Errorf("exporter image = %q, want %q", pod.Spec.InitContainers[1].Image, wantExporter)
	}
	if pod.Spec.Containers[0].Image != wantRuntime {
		t.Errorf("target-runtime image = %q, want %q", pod.Spec.Containers[0].Image, wantRuntime)
	}
}

func TestResolveImage_dirtyGitVersionPreservesLatest(t *testing.T) {
	p := New("v0.8.1-324-g02cf8552")
	got := p.resolveImage(DefaultExporterImage)
	if got != DefaultExporterImage {
		t.Errorf("resolveImage() = %q, want :latest preserved for dirty git version", got)
	}
}

func TestRenderPod_imageOverrideFromSpec(t *testing.T) {
	p := New("v1.2.3")
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "default"},
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{Provisioner: ProvisionerName},
	}
	images := &virtualtargetv1alpha1.ImageOverrides{
		Exporter: &virtualtargetv1alpha1.ImageSpec{
			Image:           "my-registry.example.com/jumpstarter:custom",
			ImagePullPolicy: corev1.PullAlways,
		},
		Runtime: &virtualtargetv1alpha1.ImageSpec{
			Image: "my-registry.example.com/qemu-runtime:custom",
		},
	}

	pod, err := p.RenderPod(context.Background(), exporterSet, vtc, nil, images, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	wantExporter := "my-registry.example.com/jumpstarter:custom"
	wantRuntime := "my-registry.example.com/qemu-runtime:custom"

	if pod.Spec.InitContainers[0].Image != wantExporter {
		t.Errorf("copy-jumpstarter-exec image = %q, want %q", pod.Spec.InitContainers[0].Image, wantExporter)
	}
	if pod.Spec.InitContainers[0].ImagePullPolicy != corev1.PullAlways {
		t.Errorf("copy-jumpstarter-exec pullPolicy = %q, want Always", pod.Spec.InitContainers[0].ImagePullPolicy)
	}
	if pod.Spec.InitContainers[1].Image != wantExporter {
		t.Errorf("exporter image = %q, want %q", pod.Spec.InitContainers[1].Image, wantExporter)
	}
	if pod.Spec.Containers[0].Image != wantRuntime {
		t.Errorf("target-runtime image = %q, want %q", pod.Spec.Containers[0].Image, wantRuntime)
	}
	if pod.Spec.Containers[0].ImagePullPolicy != corev1.PullIfNotPresent {
		t.Errorf("target-runtime pullPolicy = %q, want IfNotPresent (default)", pod.Spec.Containers[0].ImagePullPolicy)
	}
}

func TestRenderPod_partialImageOverride(t *testing.T) {
	p := New("v1.2.3")
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "default"},
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{Provisioner: ProvisionerName},
	}
	images := &virtualtargetv1alpha1.ImageOverrides{
		Runtime: &virtualtargetv1alpha1.ImageSpec{
			Image: "my-registry.example.com/qemu-runtime:custom",
		},
	}

	pod, err := p.RenderPod(context.Background(), exporterSet, vtc, nil, images, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	wantExporter := "quay.io/jumpstarter-dev/jumpstarter:1.2.3"
	wantRuntime := "my-registry.example.com/qemu-runtime:custom"

	if pod.Spec.InitContainers[0].Image != wantExporter {
		t.Errorf("copy-jumpstarter-exec image = %q, want %q", pod.Spec.InitContainers[0].Image, wantExporter)
	}
	if pod.Spec.Containers[0].Image != wantRuntime {
		t.Errorf("target-runtime image = %q, want %q", pod.Spec.Containers[0].Image, wantRuntime)
	}
}

func TestRenderPod_diskPVCWhenStorageClassSet(t *testing.T) {
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "default"},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			StorageClassName: ptr("fast-ssd"),
		},
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner:      ProvisionerName,
			StorageClassName: "ignored-because-override",
		},
	}
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-exporter", Namespace: "default"},
	}
	params := map[string]interface{}{
		"resources": map[string]interface{}{
			"storage": "20Gi",
		},
	}

	pod, err := New("dev").RenderPod(context.Background(), exporterSet, vtc, params, nil, exporter)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	var diskVol *corev1.Volume
	for i := range pod.Spec.Volumes {
		if pod.Spec.Volumes[i].Name == "disk" {
			diskVol = &pod.Spec.Volumes[i]
			break
		}
	}
	if diskVol == nil || diskVol.PersistentVolumeClaim == nil {
		t.Fatalf("expected disk PVC volume, got %#v", diskVol)
	}
	if diskVol.PersistentVolumeClaim.ClaimName != "disk-demo-exporter" {
		t.Errorf("ClaimName = %q, want disk-demo-exporter", diskVol.PersistentVolumeClaim.ClaimName)
	}
	if _, ok := pod.Spec.Containers[0].Resources.Requests[corev1.ResourceEphemeralStorage]; ok {
		t.Error("PVC mode should not set ephemeral-storage for guest disk")
	}
}

func TestRenderPod_diskEmptyDirUsesParamSize(t *testing.T) {
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "default"},
	}
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{Provisioner: ProvisionerName},
	}
	params := map[string]interface{}{
		"resources": map[string]interface{}{
			"storage": "7Gi",
		},
	}

	pod, err := New("dev").RenderPod(context.Background(), exporterSet, vtc, params, nil, nil)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
	}

	want := resource.MustParse("7Gi")
	diskVol := pod.Spec.Volumes[1]
	if diskVol.EmptyDir == nil || diskVol.EmptyDir.SizeLimit == nil || !diskVol.EmptyDir.SizeLimit.Equal(want) {
		t.Errorf("disk SizeLimit = %v, want %v", diskVol.EmptyDir, want)
	}
}

func ptr(s string) *string { return &s }
