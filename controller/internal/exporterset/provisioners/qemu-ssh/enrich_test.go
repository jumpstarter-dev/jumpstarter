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

package qemussh

import (
	"encoding/json"
	"testing"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/exporterset/provisioners/qemucommon"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
)

// Smoke test that qemu-ssh enrichment delegates to qemucommon.
// Full coverage lives in the qemucommon package.
func TestEnrich_delegatesToCommon(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "qemu",
			Type:   qemucommon.QemuDriverType,
			Config: qemucommon.MustJSON(map[string]any{"arch": "x86_64"}),
		},
	}

	result, err := enrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	config := unmarshalConfig(t, findDriver(result, "qemu").Config)
	if got := config["launcher_socket"]; got != qemucommon.LauncherSocketPath {
		t.Errorf("launcher_socket = %v, want %v", got, qemucommon.LauncherSocketPath)
	}

	if findDriver(result, "tcp") == nil {
		t.Fatal("tcp driver not auto-injected")
	}
}

func findDriver(drivers []virtualtargetv1alpha1.DriverConfig, name string) *virtualtargetv1alpha1.DriverConfig {
	for i := range drivers {
		if drivers[i].Name == name {
			return &drivers[i]
		}
	}
	return nil
}

func unmarshalConfig(t *testing.T, raw *apiextensionsv1.JSON) map[string]any {
	t.Helper()
	if raw == nil || raw.Raw == nil {
		t.Fatal("config is nil")
	}
	var config map[string]any
	if err := json.Unmarshal(raw.Raw, &config); err != nil {
		t.Fatalf("unmarshal config: %v", err)
	}
	return config
}
