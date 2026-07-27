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

package exporterset

import (
	"encoding/json"
	"testing"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
)

func TestDeriveDriverName(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"jumpstarter_driver_qemu.driver.Qemu", "Qemu"},
		{"jumpstarter_driver_network.driver.TcpNetwork", "TcpNetwork"},
		{"SingleWord", "SingleWord"},
		{"", ""},
	}

	for _, tt := range tests {
		got := deriveDriverName(tt.input)
		if got != tt.want {
			t.Errorf("deriveDriverName(%q) = %q, want %q", tt.input, got, tt.want)
		}
	}
}

func TestBuildExportMap(t *testing.T) {
	config := map[string]interface{}{
		"arch": "x86_64",
		"smp":  2,
	}
	configRaw, _ := json.Marshal(config)

	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name: "qemu",
			Type: "jumpstarter_driver_qemu.driver.Qemu",
			Config: &apiextensionsv1.JSON{
				Raw: configRaw,
			},
		},
		{
			Type: "jumpstarter_driver_network.driver.TcpNetwork",
		},
	}

	result := buildExportMap(drivers)

	if len(result) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(result))
	}

	qemu, ok := result["qemu"]
	if !ok {
		t.Fatal("'qemu' key not found")
	}
	if qemu.Type != "jumpstarter_driver_qemu.driver.Qemu" {
		t.Errorf("qemu type = %q", qemu.Type)
	}
	if qemu.Config["arch"] != "x86_64" {
		t.Errorf("qemu config arch = %v", qemu.Config["arch"])
	}

	tcp, ok := result["TcpNetwork"]
	if !ok {
		t.Fatal("'TcpNetwork' key not found (should derive from type)")
	}
	if tcp.Type != "jumpstarter_driver_network.driver.TcpNetwork" {
		t.Errorf("tcp type = %q", tcp.Type)
	}
}
