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
)

// HostConfig describes the single remote lab host for an ExporterSet
// using the qemu-ssh provisioner. Each ExporterSet manages one host;
// additional hosts are modeled as additional ExporterSets under the
// same VirtualTargetClass. Capacity is controlled via ExporterSet
// replica bounds, not per-host slot accounting.
type HostConfig struct {
	// Name is the FQDN or IP of the remote host.
	Name string `json:"name"`

	// Port is the SSH port. Defaults to the parameters-level SSH port
	// or 22 if unset.
	Port int `json:"port,omitempty"`

	// User is a per-host SSH user override. Falls back to the
	// parameters-level SSH user.
	User string `json:"user,omitempty"`
}

// SSHConfig holds parameters-level SSH defaults parsed from merged
// parameters.
type SSHConfig struct {
	User string `json:"user,omitempty"`
	Port int    `json:"port,omitempty"`
}

// ParseHost extracts the single host from merged parameters.
// Expected structure: parameters.host: {name, port?, user?}
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

	return host, nil
}

// ParseSSHConfig extracts parameters-level SSH defaults from merged
// parameters.
func ParseSSHConfig(mergedParameters map[string]any) (SSHConfig, error) {
	var cfg SSHConfig
	sshRaw, ok := mergedParameters["ssh"]
	if !ok {
		return cfg, nil
	}

	data, err := json.Marshal(sshRaw)
	if err != nil {
		return cfg, fmt.Errorf("marshal ssh config: %w", err)
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return cfg, fmt.Errorf("unmarshal ssh config: %w", err)
	}
	return cfg, nil
}

// ResolveSSHUser returns the effective SSH user for a host, falling
// back to parameters-level defaults.
func ResolveSSHUser(host HostConfig, ssh SSHConfig) string {
	if host.User != "" {
		return host.User
	}
	if ssh.User != "" {
		return ssh.User
	}
	return "root"
}

// ResolveSSHPort returns the effective SSH port for a host, falling
// back to parameters-level defaults, then 22.
func ResolveSSHPort(host HostConfig, ssh SSHConfig) int {
	if host.Port > 0 {
		return host.Port
	}
	if ssh.Port > 0 {
		return ssh.Port
	}
	return 22
}

// ParseRuntimeConfig extracts runtime-specific settings from merged
// parameters (e.g. KVM enablement, extra devices).
func ParseRuntimeConfig(mergedParameters map[string]any) (kvm bool, extraDevices []string) {
	runtimeRaw, ok := mergedParameters["runtime"]
	if !ok {
		return false, nil
	}

	runtimeMap, ok := runtimeRaw.(map[string]any)
	if !ok {
		return false, nil
	}

	if v, ok := runtimeMap["kvm"].(bool); ok {
		kvm = v
	}

	if devs, ok := runtimeMap["devices"].([]any); ok {
		for _, d := range devs {
			if s, ok := d.(string); ok {
				extraDevices = append(extraDevices, s)
			}
		}
	}

	return kvm, extraDevices
}
