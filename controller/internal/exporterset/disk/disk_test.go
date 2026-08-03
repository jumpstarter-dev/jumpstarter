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

package disk

import (
	"testing"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
)

func TestPVCName(t *testing.T) {
	if got := PVCName("exp-1"); got != "disk-exp-1" {
		t.Errorf("PVCName() = %q, want disk-exp-1", got)
	}
}

func TestSizeFromParameters(t *testing.T) {
	qty, err := SizeFromParameters(nil)
	if err != nil {
		t.Fatalf("nil params: %v", err)
	}
	if !qty.Equal(resource.MustParse(DefaultSize)) {
		t.Errorf("default = %v, want %s", qty, DefaultSize)
	}

	qty, err = SizeFromParameters(map[string]interface{}{
		"resources": map[string]interface{}{"storage": "15Gi"},
	})
	if err != nil {
		t.Fatalf("15Gi: %v", err)
	}
	if !qty.Equal(resource.MustParse("15Gi")) {
		t.Errorf("got %v, want 15Gi", qty)
	}

	_, err = SizeFromParameters(map[string]interface{}{
		"resources": map[string]interface{}{"storage": 10.0},
	})
	if err == nil {
		t.Fatal("expected error for numeric storage")
	}
}

func TestSetEphemeralStorage(t *testing.T) {
	size := resource.MustParse("10Gi")
	res := corev1.ResourceRequirements{
		Requests: corev1.ResourceList{
			corev1.ResourceCPU: resource.MustParse("1"),
		},
	}
	SetEphemeralStorage(&res, size)
	if !res.Requests[corev1.ResourceEphemeralStorage].Equal(size) {
		t.Errorf("request = %v, want %v", res.Requests[corev1.ResourceEphemeralStorage], size)
	}
	if !res.Limits[corev1.ResourceEphemeralStorage].Equal(size) {
		t.Errorf("limit = %v, want %v", res.Limits[corev1.ResourceEphemeralStorage], size)
	}
	if !res.Requests[corev1.ResourceCPU].Equal(resource.MustParse("1")) {
		t.Error("cpu request should be preserved")
	}

	// Does not overwrite existing ephemeral-storage.
	custom := resource.MustParse("1Gi")
	res.Requests[corev1.ResourceEphemeralStorage] = custom
	SetEphemeralStorage(&res, size)
	if !res.Requests[corev1.ResourceEphemeralStorage].Equal(custom) {
		t.Errorf("should preserve explicit ephemeral-storage, got %v", res.Requests[corev1.ResourceEphemeralStorage])
	}
}
