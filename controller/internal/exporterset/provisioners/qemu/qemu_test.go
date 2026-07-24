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
	if got := New().Name(); got != ProvisionerName {
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

	pod, err := New().RenderPod(context.Background(), exporterSet, vtc, nil, nil)
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

	if len(pod.Spec.Volumes) != 1 || pod.Spec.Volumes[0].EmptyDir == nil {
		t.Fatalf("expected shared emptyDir volume, got %#v", pod.Spec.Volumes)
	}
	wantLimit := resource.MustParse(sharedVolumeSizeLimit)
	if pod.Spec.Volumes[0].EmptyDir.SizeLimit == nil ||
		!pod.Spec.Volumes[0].EmptyDir.SizeLimit.Equal(wantLimit) {
		t.Errorf("SizeLimit = %v, want %v", pod.Spec.Volumes[0].EmptyDir.SizeLimit, wantLimit)
	}

	if len(pod.Spec.InitContainers) != 1 || pod.Spec.InitContainers[0].Name != "exporter" {
		t.Errorf("unexpected init containers: %#v", pod.Spec.InitContainers)
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

	pod, err := New().RenderPod(context.Background(), exporterSet, vtc, nil, nil)
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

	pod, err := New().RenderPod(context.Background(), exporterSet, vtc, nil, exporter)
	if err != nil {
		t.Fatalf("RenderPod() error = %v", err)
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
