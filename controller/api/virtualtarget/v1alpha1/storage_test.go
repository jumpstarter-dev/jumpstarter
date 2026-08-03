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

package v1alpha1

import (
	"testing"
)

func TestEffectiveStorageClassName(t *testing.T) {
	empty := ""
	override := "es-sc"

	tests := []struct {
		name string
		vtc  string
		es   *string
		want string
	}{
		{name: "both empty", want: ""},
		{name: "vtc only", vtc: "vtc-sc", want: "vtc-sc"},
		{name: "es override", vtc: "vtc-sc", es: &override, want: "es-sc"},
		{name: "es clears to emptyDir", vtc: "vtc-sc", es: &empty, want: ""},
		{name: "es only", es: &override, want: "es-sc"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			vtc := &VirtualTargetClass{Spec: VirtualTargetClassSpec{StorageClassName: tt.vtc}}
			es := &ExporterSet{Spec: ExporterSetSpec{StorageClassName: tt.es}}
			if got := EffectiveStorageClassName(vtc, es); got != tt.want {
				t.Errorf("EffectiveStorageClassName() = %q, want %q", got, tt.want)
			}
		})
	}
}
