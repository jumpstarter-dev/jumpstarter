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

package qemudriver

import (
	"testing"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
)

// Pins the hardware-style root names to the actual QEMU child names.
// Tests that iterate PresetRootChildren are self-referential and would
// not catch a wrong child (e.g. serial -> vm.serial, which has no such
// child); these literal refs do.
func TestAliasPresetRoots_mapsRootsToQemuChildren(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "vm", Type: QemuDriverType},
	}

	result := AliasPresetRoots(drivers, nil)

	refs := make(map[string]string, len(result))
	for _, d := range result {
		refs[d.Name] = d.Ref
	}

	wantRefs := map[string]string{
		"power":   "vm.power",
		"storage": "vm.flasher",
		"serial":  "vm.console",
	}
	for root, wantRef := range wantRefs {
		got, ok := refs[root]
		if !ok {
			t.Fatalf("missing root alias %q", root)
		}
		if got != wantRef {
			t.Errorf("%s.ref = %q, want %q", root, got, wantRef)
		}
	}
}
