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

// HostConfig describes a single remote lab host parsed from
// VirtualTargetClass/ExporterSet merged parameters.
type HostConfig struct {
	// Name is the FQDN or IP of the remote host.
	Name string `json:"name"`

	// Arch is the host CPU architecture (e.g. "aarch64", "x86_64").
	// Informational; not used for scheduling in v1.
	Arch string `json:"arch,omitempty"`

	// Slots is the maximum number of concurrent exporter instances
	// this host can run.
	Slots int `json:"slots"`

	// Port is the SSH port. Defaults to the pool-level SSH port or
	// 22 if unset.
	Port int `json:"port,omitempty"`

	// User is a per-host SSH user override. Falls back to the
	// pool-level SSH user.
	User string `json:"user,omitempty"`
}

// SSHConfig holds pool-level SSH defaults parsed from merged
// parameters.
type SSHConfig struct {
	User string `json:"user,omitempty"`
	Port int    `json:"port,omitempty"`
}

// ParseHosts extracts the host list from merged parameters.
// Expected structure: parameters.hosts: [{name, arch, slots, ...}]
func ParseHosts(mergedParameters map[string]any) ([]HostConfig, error) {
	hostsRaw, ok := mergedParameters["hosts"]
	if !ok {
		return nil, fmt.Errorf("parameters.hosts is required for qemu-ssh provisioner")
	}

	data, err := json.Marshal(hostsRaw)
	if err != nil {
		return nil, fmt.Errorf("marshal hosts: %w", err)
	}

	var hosts []HostConfig
	if err := json.Unmarshal(data, &hosts); err != nil {
		return nil, fmt.Errorf("unmarshal hosts: %w", err)
	}

	if len(hosts) == 0 {
		return nil, fmt.Errorf("parameters.hosts must contain at least one host")
	}

	for i, h := range hosts {
		if h.Name == "" {
			return nil, fmt.Errorf("parameters.hosts[%d].name is required", i)
		}
		if h.Slots <= 0 {
			return nil, fmt.Errorf("parameters.hosts[%d].slots must be > 0 (host %q)", i, h.Name)
		}
	}

	return hosts, nil
}

// ParseSSHConfig extracts pool-level SSH defaults from merged
// parameters.
func ParseSSHConfig(mergedParameters map[string]any) SSHConfig {
	var cfg SSHConfig
	sshRaw, ok := mergedParameters["ssh"]
	if !ok {
		return cfg
	}

	data, err := json.Marshal(sshRaw)
	if err != nil {
		return cfg
	}
	_ = json.Unmarshal(data, &cfg)
	return cfg
}

// ResolveSSHUser returns the effective SSH user for a host, falling
// back to pool defaults.
func ResolveSSHUser(host HostConfig, poolSSH SSHConfig) string {
	if host.User != "" {
		return host.User
	}
	if poolSSH.User != "" {
		return poolSSH.User
	}
	return "root"
}

// ResolveSSHPort returns the effective SSH port for a host, falling
// back to pool defaults, then 22.
func ResolveSSHPort(host HostConfig, poolSSH SSHConfig) int {
	if host.Port > 0 {
		return host.Port
	}
	if poolSSH.Port > 0 {
		return poolSSH.Port
	}
	return 22
}

// ExporterAnnotations is a minimal accessor interface for reading
// annotations from Exporter CRs. This avoids importing the full
// Exporter type into this pure library package.
type ExporterAnnotations interface {
	GetAnnotations() map[string]string
	GetName() string
}

// SelectHost picks a host with available capacity. It counts current
// assignments by reading the AnnotationHost annotation from each
// existing exporter. Returns the first host with a free slot.
func SelectHost(hosts []HostConfig, existingExporters []ExporterAnnotations) (*HostConfig, error) {
	usage := countHostUsage(existingExporters)

	for i := range hosts {
		h := &hosts[i]
		used := usage[h.Name]
		if used < h.Slots {
			return h, nil
		}
	}

	return nil, fmt.Errorf("no host has available capacity (all slots full)")
}

// countHostUsage counts how many exporters are assigned to each host
// based on their annotations.
func countHostUsage(exporters []ExporterAnnotations) map[string]int {
	usage := make(map[string]int)
	for _, exp := range exporters {
		annotations := exp.GetAnnotations()
		if annotations == nil {
			continue
		}
		host, ok := annotations[AnnotationHost]
		if !ok || host == "" {
			continue
		}
		usage[host]++
	}
	return usage
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
