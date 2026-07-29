/*
Copyright 2026. The Jumpstarter Authors.

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

package jumpstarter

import (
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// Stdlib unit test (no envtest): asserts Controller Deployment metrics bind for JEP-0013 Phase 2.
func TestControllerDeploymentMetricsBind(t *testing.T) {
	r := &JumpstarterReconciler{}
	js := &operatorv1alpha1.Jumpstarter{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "jumpstarter",
			Namespace: "jumpstarter-lab",
		},
		Spec: operatorv1alpha1.JumpstarterSpec{
			Controller: operatorv1alpha1.ControllerConfig{
				Image:           "example.com/controller:test",
				ImagePullPolicy: corev1.PullIfNotPresent,
				Replicas:        1,
			},
		},
	}

	dep := r.createControllerDeployment(js, "testhash")
	if dep == nil {
		t.Fatal("expected non-nil deployment")
	}
	if len(dep.Spec.Template.Spec.Containers) == 0 {
		t.Fatal("expected at least one container")
	}

	c := dep.Spec.Template.Spec.Containers[0]
	foundArg := false
	for _, arg := range c.Args {
		if arg == "-metrics-bind-address=:8080" {
			foundArg = true
			break
		}
	}
	if !foundArg {
		t.Fatalf("expected -metrics-bind-address=:8080 in args, got %#v", c.Args)
	}

	foundPort := false
	for _, p := range c.Ports {
		if p.Name == "metrics" {
			foundPort = true
			if p.ContainerPort != 8080 {
				t.Fatalf("metrics port = %d, want 8080", p.ContainerPort)
			}
			break
		}
	}
	if !foundPort {
		t.Fatal("expected container port named metrics")
	}
}
