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

// Package qemucommon holds shared QEMU driver enrichment helpers used by
// both the in-cluster (qemu.jumpstarter.dev) and off-cluster
// (qemu-ssh.jumpstarter.dev) provisioners.
package qemucommon

import (
	"encoding/json"
	"fmt"
	"strings"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
)

const (
	// QemuDriverType is the Python driver type for the QEMU driver.
	QemuDriverType = "jumpstarter_driver_qemu.driver.Qemu"

	// TcpDriverType is the TCP network wrapper driver type.
	TcpDriverType = "jumpstarter_driver_network.driver.TcpNetwork"

	// LauncherSocketPath is the Unix socket path used by jumpstarter-exec
	// between the exporter and the QEMU runtime container. Both in-cluster
	// Pods and off-cluster quadlets mount this at /shared.
	LauncherSocketPath = "/shared/launcher.sock"

	// DefaultExporterImage is the exporter container image.
	DefaultExporterImage = "quay.io/jumpstarter-dev/jumpstarter:latest"

	// DefaultQEMURuntimeImage is the QEMU runtime container image.
	DefaultQEMURuntimeImage = "quay.io/jumpstarter-dev/virtual/qemu-runtime:latest"
)

// ResolveImage replaces the :latest tag with the controller's own version
// tag. If the version is unknown ("dev"), dirty (contains "-g", indicating
// a non-release git describe), or the image uses a non-latest tag (admin
// override), the image is returned unchanged.
func ResolveImage(version, image string) string {
	if version == "" || version == "dev" || strings.Contains(version, "-g") {
		return image
	}
	v := strings.TrimPrefix(version, "v")
	if base, ok := strings.CutSuffix(image, ":latest"); ok {
		return base + ":" + v
	}
	return image
}

// EnrichExporterExport injects QEMU-specific driver configuration:
//   - Forces launcher_socket on the QEMU driver entry
//   - Defaults arch/smp/mem/disk_size from mergedParameters if not set
//   - Injects default_partitions (firmware paths) based on arch unless user overrides
//   - Auto-injects hostfwd.ssh if not present
//   - Auto-injects tcp wrapper driver entry if not present
func EnrichExporterExport(
	drivers []virtualtargetv1alpha1.DriverConfig,
	mergedParameters map[string]any,
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	result := make([]virtualtargetv1alpha1.DriverConfig, 0, len(drivers)+1)
	hasTCP := false

	for _, d := range drivers {
		if d.Type == TcpDriverType {
			hasTCP = true
		}

		if d.Type == QemuDriverType {
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
			Type: TcpDriverType,
			Config: MustJSON(map[string]any{
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

	config["launcher_socket"] = LauncherSocketPath

	setDefault(config, "arch", params, "arch")
	setDefault(config, "smp", params, "resources.cpu")
	setDefault(config, "mem", params, "resources.memory")
	setDefault(config, "disk_size", params, "storage.size")

	if _, hasPartitions := config["default_partitions"]; !hasPartitions {
		arch, _ := config["arch"].(string)
		config["default_partitions"] = DefaultPartitionsForArch(arch)
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

// DefaultPartitionsForArch returns firmware partition paths for the
// given guest architecture.
func DefaultPartitionsForArch(arch string) map[string]string {
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
		// Kubernetes resource quantities use binary suffixes (Gi, Mi);
		// the QEMU driver expects qemu-img style sizes (G, M).
		if key == "disk_size" || key == "mem" {
			val = NormalizeQemuSize(val)
		}
		config[key] = val
	}
}

// NormalizeQemuSize converts Kubernetes binary quantity strings (e.g. "10Gi")
// to the form expected by the QEMU driver / qemu-img (e.g. "10G").
func NormalizeQemuSize(v any) any {
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

// MustJSON marshals v to apiextensions JSON. Panics are avoided; marshal
// errors yield empty Raw (callers only pass known-good maps).
func MustJSON(v any) *apiextensionsv1.JSON {
	raw, _ := json.Marshal(v)
	return &apiextensionsv1.JSON{Raw: raw}
}
