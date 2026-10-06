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
)

const (
	// AnnotationHost is the annotation key on Exporter CRs that
	// records which remote host an instance was assigned to.
	AnnotationHost = "qemu-ssh.jumpstarter.dev/host"

	// defaultSSHUser is used when no user is specified.
	defaultSSHUser = "root"

	// defaultSSHPort is used when no port is specified.
	defaultSSHPort = 22
)

// HostConfig describes the single remote lab host for an ExporterSet
// using the qemu-ssh provisioner. Each ExporterSet manages one host;
// additional hosts are modeled as additional ExporterSets under the
// same VirtualTargetClass. Capacity is controlled via ExporterSet
// replica bounds, not per-host slot accounting.
type HostConfig struct {
	// Name is the FQDN or IP of the remote host.
	Name string `json:"name"`

	// Port is the SSH port (default: 22).
	Port int `json:"port,omitempty"`

	// User is the SSH username (default: "root").
	User string `json:"user,omitempty"`
}

// ParseHost extracts the host config from merged parameters and
// applies defaults. The expected YAML structure is:
//
//	parameters:
//	  host:
//	    name: lab-host-01.example.com
//	    user: root       # optional, default "root"
//	    port: 22         # optional, default 22
func ParseHost(mergedParameters map[string]any) (HostConfig, error) {
	hostRaw, ok := mergedParameters["host"]
	if !ok {
		return HostConfig{}, fmt.Errorf("parameters.host is required for qemu-ssh provisioner")
	}

	data, err := json.Marshal(hostRaw)
	if err != nil {
		return HostConfig{}, fmt.Errorf("marshal host: %w", err)
	}

	var host HostConfig
	if err := json.Unmarshal(data, &host); err != nil {
		return HostConfig{}, fmt.Errorf("unmarshal host: %w", err)
	}

	if host.Name == "" {
		return HostConfig{}, fmt.Errorf("parameters.host.name is required")
	}

	if host.User == "" {
		host.User = defaultSSHUser
	}
	if host.Port == 0 {
		host.Port = defaultSSHPort
	}

	return host, nil
}

// RuntimeConfig holds runtime-specific settings parsed from merged
// parameters.
type RuntimeConfig struct {
	KVM          bool
	ExtraDevices []string
	HostNetwork  bool
}

// ParseRuntimeConfig extracts runtime-specific settings from merged
// parameters (e.g. KVM enablement, extra devices, host networking).
func ParseRuntimeConfig(mergedParameters map[string]any) RuntimeConfig {
	runtimeRaw, ok := mergedParameters["runtime"]
	if !ok {
		return RuntimeConfig{}
	}

	runtimeMap, ok := runtimeRaw.(map[string]any)
	if !ok {
		return RuntimeConfig{}
	}

	var cfg RuntimeConfig

	if v, ok := runtimeMap["kvm"].(bool); ok {
		cfg.KVM = v
	}

	if v, ok := runtimeMap["host_network"].(bool); ok {
		cfg.HostNetwork = v
	}

	if devs, ok := runtimeMap["devices"].([]any); ok {
		for _, d := range devs {
			if s, ok := d.(string); ok {
				cfg.ExtraDevices = append(cfg.ExtraDevices, s)
			}
		}
	}

	return cfg
}
