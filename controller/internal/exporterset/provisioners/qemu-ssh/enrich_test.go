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
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
)

func TestEnrich_injectsLauncherSocket(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "qemu",
			Type:   qemuDriverType,
			Config: mustJSON(map[string]any{"arch": "x86_64"}),
		},
	}

	result, err := enrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	config := unmarshalConfig(t, findDriver(result, "qemu").Config)
	if got := config["launcher_socket"]; got != launcherSocketPath {
		t.Errorf("launcher_socket = %v, want %v", got, launcherSocketPath)
	}
}

func TestEnrich_autoInjectsTCP(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "qemu",
			Type:   qemuDriverType,
			Config: mustJSON(map[string]any{"arch": "x86_64"}),
		},
	}

	result, err := enrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	tcp := findDriver(result, "tcp")
	if tcp == nil {
		t.Fatal("tcp driver not auto-injected")
	}
	if tcp.Type != tcpDriverType {
		t.Errorf("tcp driver type = %q", tcp.Type)
	}
}

func TestEnrich_doesNotDuplicateTCP(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "qemu",
			Type:   qemuDriverType,
			Config: mustJSON(map[string]any{"arch": "x86_64"}),
		},
		{
			Name:   "tcp",
			Type:   tcpDriverType,
			Config: mustJSON(map[string]any{"host": "10.0.0.1", "port": 3333}),
		},
	}

	result, err := enrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	count := 0
	for _, d := range result {
		if d.Type == tcpDriverType {
			count++
		}
	}
	if count != 1 {
		t.Errorf("tcp driver count = %d, want 1", count)
	}
}

func TestEnrich_defaultsFromParameters(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "qemu",
			Type:   qemuDriverType,
			Config: mustJSON(map[string]any{}),
		},
	}

	params := map[string]any{
		"arch": "aarch64",
		"resources": map[string]any{
			"cpu":    4,
			"memory": "4Gi",
		},
		"storage": map[string]any{
			"size": "16Gi",
		},
	}

	result, err := enrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}

	config := unmarshalConfig(t, findDriver(result, "qemu").Config)
	if got := config["arch"]; got != "aarch64" {
		t.Errorf("arch = %v, want aarch64", got)
	}
	if got := config["smp"]; got != float64(4) {
		t.Errorf("smp = %v, want 4", got)
	}
	if got := config["mem"]; got != "4G" {
		t.Errorf("mem = %v, want 4G (normalized from 4Gi)", got)
	}
	if got := config["disk_size"]; got != "16G" {
		t.Errorf("disk_size = %v, want 16G", got)
	}
}

func TestEnrich_defaultPartitionsAarch64(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "qemu",
			Type:   qemuDriverType,
			Config: mustJSON(map[string]any{"arch": "aarch64"}),
		},
	}

	result, err := enrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	config := unmarshalConfig(t, findDriver(result, "qemu").Config)
	partitions, ok := config["default_partitions"].(map[string]any)
	if !ok {
		t.Fatalf("default_partitions not a map: %T", config["default_partitions"])
	}
	if got := partitions["OVMF_CODE.fd"]; got != "/usr/share/AAVMF/AAVMF_CODE.fd" {
		t.Errorf("OVMF_CODE.fd = %v", got)
	}
}

func TestEnrich_hostfwdSSH(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "qemu",
			Type:   qemuDriverType,
			Config: mustJSON(map[string]any{"arch": "x86_64"}),
		},
	}

	result, err := enrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	config := unmarshalConfig(t, findDriver(result, "qemu").Config)
	hostfwd, ok := config["hostfwd"].(map[string]any)
	if !ok {
		t.Fatalf("hostfwd not a map: %T", config["hostfwd"])
	}
	sshFwd, ok := hostfwd["ssh"].(map[string]any)
	if !ok {
		t.Fatalf("hostfwd.ssh not a map: %T", hostfwd["ssh"])
	}
	if got := sshFwd["hostport"].(float64); got != 2222 {
		t.Errorf("hostfwd.ssh.hostport = %v", got)
	}
}

// --- helpers ---

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
