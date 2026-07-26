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
	"fmt"
	"os"
	"strings"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	sigsyaml "sigs.k8s.io/yaml"
)

const (
	configVolumeName      = "exporter-config"
	configMountPath       = "/etc/jumpstarter/exporters" // exporter reads <name>.yaml from here
	exporterContainerName = "exporter"                   // init-container name in the sidecar Pod
)

// exporterConfigMeta mirrors the metadata block of ExporterConfig YAML.
type exporterConfigMeta struct {
	Namespace string `json:"namespace"`
	Name      string `json:"name"`
}

// exporterConfigTLS mirrors the tls block of ExporterConfig YAML.
type exporterConfigTLS struct {
	CA       string `json:"ca,omitempty"`
	Insecure bool   `json:"insecure,omitempty"`
}

// exporterConfigDriver mirrors a single entry in the export map.
type exporterConfigDriver struct {
	Type   string      `json:"type"`
	Config interface{} `json:"config,omitempty"`
}

// exporterConfigYAML is the in-memory form of the ExporterConfig document.
// Fields use json tags because sigs.k8s.io/yaml marshals through JSON.
type exporterConfigYAML struct {
	APIVersion string                          `json:"apiVersion"`
	Kind       string                          `json:"kind"`
	Metadata   exporterConfigMeta              `json:"metadata"`
	Endpoint   string                          `json:"endpoint"`
	Token      string                          `json:"token"`
	TLS        *exporterConfigTLS              `json:"tls,omitempty"`
	Export     map[string]exporterConfigDriver `json:"export,omitempty"`
}

// controllerEndpoint reads GRPC_ENDPOINT or falls back to localhost:8082.
func controllerEndpoint() string {
	if ep := os.Getenv("GRPC_ENDPOINT"); ep != "" {
		return ep
	}
	return "localhost:8082"
}

// driverKey returns the export-map key for a DriverConfig: d.Name if set,
// otherwise the last dot-segment of d.Type lowercased
// (e.g. "jumpstarter_driver_power.driver.QemuPower" → "qemupower").
func driverKey(d virtualtargetv1alpha1.DriverConfig, idx int) string {
	if d.Name != "" {
		return d.Name
	}
	parts := strings.Split(d.Type, ".")
	if last := strings.ToLower(parts[len(parts)-1]); last != "" {
		return last
	}
	return fmt.Sprintf("driver%d", idx)
}

// renderExporterConfigYAML builds the ExporterConfig YAML for the given exporter,
// including its JWT token, optional CA bundle, and driver list.
func renderExporterConfigYAML(
	exporter *jumpstarterdevv1alpha1.Exporter,
	token string,
	caBundle string,
	drivers []virtualtargetv1alpha1.DriverConfig,
) (string, error) {
	cfg := exporterConfigYAML{
		APIVersion: "jumpstarter.dev/v1alpha1",
		Kind:       "ExporterConfig",
		Metadata: exporterConfigMeta{
			Namespace: exporter.Namespace,
			Name:      exporter.Name,
		},
		Endpoint: controllerEndpoint(),
		Token:    token,
	}

	if caBundle != "" {
		cfg.TLS = &exporterConfigTLS{CA: caBundle}
	}

	if len(drivers) > 0 {
		cfg.Export = make(map[string]exporterConfigDriver, len(drivers))
		for i, d := range drivers {
			key := driverKey(d, i)
			if _, exists := cfg.Export[key]; exists {
				return "", fmt.Errorf("duplicate driver key %q in ExporterSet drivers", key)
			}
			entry := exporterConfigDriver{Type: d.Type}
			if d.Config != nil {
				var parsed interface{}
				if err := json.Unmarshal(d.Config.Raw, &parsed); err != nil {
					return "", fmt.Errorf("unmarshal config for driver %q: %w", key, err)
				}
				entry.Config = parsed
			}
			cfg.Export[key] = entry
		}
	}

	out, err := sigsyaml.Marshal(cfg)
	if err != nil {
		return "", fmt.Errorf("marshal ExporterConfig for %s/%s: %w",
			exporter.Namespace, exporter.Name, err)
	}
	return string(out), nil
}
