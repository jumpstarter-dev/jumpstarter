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
	"slices"
	"testing"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestExporterPodRecycleStrategy(t *testing.T) {
	for _, tt := range []struct {
		name     string
		strategy virtualtargetv1alpha1.RecycleStrategy
		restart  corev1.RestartPolicy
	}{
		{"default", "", corev1.RestartPolicyNever},
		{"replace", virtualtargetv1alpha1.RecycleStrategyExitAndReplace, corev1.RestartPolicyNever},
		{"reuse", virtualtargetv1alpha1.RecycleStrategyInPlaceReuse, corev1.RestartPolicyAlways},
	} {
		t.Run(tt.name, func(t *testing.T) {
			es := &virtualtargetv1alpha1.ExporterSet{
				ObjectMeta: metav1.ObjectMeta{Name: "test-set", Namespace: testExporter.Namespace},
				Spec:       virtualtargetv1alpha1.ExporterSetSpec{RecycleStrategy: tt.strategy},
			}
			vtc := &virtualtargetv1alpha1.VirtualTargetClass{
				Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
					CredentialsSecretRef: &corev1.LocalObjectReference{Name: "remote-credentials"},
				},
			}
			pod := New("dev", nil).buildExporterPod(es, vtc, nil, testExporter)
			if pod.Spec.RestartPolicy != tt.restart {
				t.Errorf("pod restart policy = %q, want %q", pod.Spec.RestartPolicy, tt.restart)
			}
			// The exporter must determine when the Pod completes. A native sidecar
			// would restart even with pod-level Never; a keepalive would prevent completion.
			if len(pod.Spec.Containers) != 1 || pod.Spec.Containers[0].Name != "exporter" {
				t.Fatalf("exporter must be the sole main container, got %#v", pod.Spec.Containers)
			}
			exporter := pod.Spec.Containers[0]
			if exporter.RestartPolicy != nil {
				t.Fatal("exporter must follow the pod restart policy")
			}
			if !slices.Equal(exporter.Command, []string{"jmp", "run", "--exporter-config", configMountPath + "/config.yaml"}) {
				t.Fatalf("exporter command = %#v", exporter.Command)
			}
			if !slices.Contains(exporter.VolumeMounts, corev1.VolumeMount{
				Name: credentialsVolumeName, MountPath: credentialsMountPath, ReadOnly: true,
			}) {
				t.Fatal("main exporter is missing its remote kubeconfig mount")
			}
			for _, init := range pod.Spec.InitContainers {
				if init.Name == "exporter" || init.RestartPolicy != nil {
					t.Fatalf("only one-shot initialization may precede the exporter: %#v", init)
				}
			}
		})
	}
}
