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

package main

import (
	"strings"
	"testing"
)

func TestSelectProvisionerCuttlefish(t *testing.T) {
	prov, err := selectProvisioner("cuttlefish.jumpstarter.dev")
	if err != nil {
		t.Fatalf("selectProvisioner() error = %v", err)
	}
	if got := prov.Name(); got != "cuttlefish.jumpstarter.dev" {
		t.Errorf("Name() = %q, want cuttlefish.jumpstarter.dev", got)
	}
}

func TestSelectProvisionerQemu(t *testing.T) {
	prov, err := selectProvisioner("qemu.jumpstarter.dev")
	if err != nil {
		t.Fatalf("selectProvisioner() error = %v", err)
	}
	if got := prov.Name(); got != "qemu.jumpstarter.dev" {
		t.Errorf("Name() = %q, want qemu.jumpstarter.dev", got)
	}
}

func TestSelectProvisionerUnknown(t *testing.T) {
	_, err := selectProvisioner("nope")
	if err == nil {
		t.Fatal("selectProvisioner() error = nil, want error")
	}
	if !strings.Contains(err.Error(), "qemu.jumpstarter.dev") {
		t.Errorf("error %q does not list qemu.jumpstarter.dev as supported", err)
	}
	if !strings.Contains(err.Error(), "cuttlefish.jumpstarter.dev") {
		t.Errorf("error %q does not list cuttlefish.jumpstarter.dev as supported", err)
	}
}
