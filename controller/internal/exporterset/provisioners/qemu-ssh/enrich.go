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
	"fmt"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
)

const (
	qemuDriverType = "jumpstarter_driver_qemu.driver.Qemu"
	tcpDriverType  = "jumpstarter_driver_network.driver.TcpNetwork"
)

// enrichExporterExport adjusts driver configuration for off-cluster
// deployment. The logic mirrors the in-cluster QEMU provisioner but
// paths match the quadlet container layout.
func enrichExporterExport(
	drivers []virtualtargetv1alpha1.DriverConfig,
	mergedParameters map[string]any,
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	result := make([]virtualtargetv1alpha1.DriverConfig, 0, len(drivers)+1)
	hasTCP := false

	for _, d := range drivers {
		if d.Type == tcpDriverType {
			hasTCP = true
		}

		if d.Type == qemuDriverType {
			var err error
			d, err = enrichQemuDriver(d, mergedParameters)
			if err != nil {
				return nil, err
			}
		}
		result = append(result, d)
	}

	if !hasTCP {
		result = append(result, virtualtargetv1alpha1.DriverConfig{
			Name: "tcp",
			Type: tcpDriverType,
			Config: mustJSON(map[string]any{
				"host": "127.0.0.1",
				"port": 2222,
			}),
		})
	}

	return result, nil
}

func enrichQemuDriver(
	d virtualtargetv1alpha1.DriverConfig,
	params map[string]any,
) (virtualtargetv1alpha1.DriverConfig, error) {
	config := make(map[string]any)
	if d.Config != nil && d.Config.Raw != nil {
		if err := json.Unmarshal(d.Config.Raw, &config); err != nil {
			return d, fmt.Errorf("unmarshal QEMU driver config: %w", err)
		}
	}

	config["launcher_socket"] = launcherSocketPath

	setDefault(config, "arch", params, "arch")
	setDefault(config, "smp", params, "resources.cpu")
	setDefault(config, "mem", params, "resources.memory")
	setDefault(config, "disk_size", params, "storage.size")

	if _, hasPartitions := config["default_partitions"]; !hasPartitions {
		arch, _ := config["arch"].(string)
		config["default_partitions"] = defaultPartitionsForArch(arch)
	}

	hostfwd, _ := config["hostfwd"].(map[string]any)
	if hostfwd == nil {
		hostfwd = make(map[string]any)
	}
	if _, hasSSH := hostfwd["ssh"]; !hasSSH {
		hostfwd["ssh"] = map[string]any{
			"hostaddr":  "127.0.0.1",
			"hostport":  2222,
			"guestport": 22,
		}
		config["hostfwd"] = hostfwd
	}

	raw, _ := json.Marshal(config)
	d.Config = &apiextensionsv1.JSON{Raw: raw}
	return d, nil
}

func defaultPartitionsForArch(arch string) map[string]string {
	switch arch {
	case "aarch64":
		return map[string]string{
			"OVMF_CODE.fd": "/usr/share/AAVMF/AAVMF_CODE.fd",
			"OVMF_VARS.fd": "/usr/share/AAVMF/AAVMF_VARS.fd",
		}
	default:
		return map[string]string{
			"OVMF_CODE.fd": "/usr/share/edk2/ovmf/OVMF_CODE.fd",
			"OVMF_VARS.fd": "/usr/share/edk2/ovmf/OVMF_VARS.fd",
		}
	}
}

func setDefault(config map[string]any, key string, params map[string]any, paramPath string) {
	if _, exists := config[key]; exists {
		return
	}

	parts := splitDot(paramPath)
	var val any = params
	for _, p := range parts {
		m, ok := val.(map[string]any)
		if !ok {
			return
		}
		val = m[p]
	}

	if val != nil {
		if key == "disk_size" || key == "mem" {
			val = normalizeQemuSize(val)
		}
		config[key] = val
	}
}

func normalizeQemuSize(v any) any {
	s, ok := v.(string)
	if !ok || len(s) < 2 {
		return v
	}
	if s[len(s)-1] != 'i' {
		return v
	}
	switch s[len(s)-2] {
	case 'K', 'M', 'G', 'T', 'k', 'm', 'g', 't':
		return s[:len(s)-1]
	default:
		return v
	}
}

func splitDot(s string) []string {
	result := make([]string, 0, 2)
	start := 0
	for i := range s {
		if s[i] == '.' {
			result = append(result, s[start:i])
			start = i + 1
		}
	}
	result = append(result, s[start:])
	return result
}

func mustJSON(v any) *apiextensionsv1.JSON {
	raw, _ := json.Marshal(v)
	return &apiextensionsv1.JSON{Raw: raw}
}
