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
	"testing"
)

// fakeExporter satisfies ExporterAnnotations for testing without
// importing the full Exporter type.
type fakeExporter struct {
	name        string
	annotations map[string]string
}

func (f *fakeExporter) GetAnnotations() map[string]string { return f.annotations }
func (f *fakeExporter) GetName() string                   { return f.name }

func TestParseHosts_valid(t *testing.T) {
	params := map[string]any{
		"hosts": []any{
			map[string]any{
				"name":  "bench-01.lab.example.com",
				"arch":  "aarch64",
				"slots": float64(2),
			},
			map[string]any{
				"name":  "bench-02.lab.example.com",
				"arch":  "x86_64",
				"slots": float64(4),
				"port":  float64(2222),
				"user":  "admin",
			},
		},
	}

	hosts, err := ParseHosts(params)
	if err != nil {
		t.Fatalf("ParseHosts() error = %v", err)
	}
	if len(hosts) != 2 {
		t.Fatalf("len(hosts) = %d, want 2", len(hosts))
	}

	if hosts[0].Name != "bench-01.lab.example.com" {
		t.Errorf("hosts[0].Name = %q", hosts[0].Name)
	}
	if hosts[0].Arch != "aarch64" {
		t.Errorf("hosts[0].Arch = %q", hosts[0].Arch)
	}
	if hosts[0].Slots != 2 {
		t.Errorf("hosts[0].Slots = %d", hosts[0].Slots)
	}

	if hosts[1].Port != 2222 {
		t.Errorf("hosts[1].Port = %d", hosts[1].Port)
	}
	if hosts[1].User != "admin" {
		t.Errorf("hosts[1].User = %q", hosts[1].User)
	}
}

func TestParseHosts_missingHostsKey(t *testing.T) {
	params := map[string]any{}

	_, err := ParseHosts(params)
	if err == nil {
		t.Fatal("ParseHosts() expected error for missing hosts key")
	}
}

func TestParseHosts_emptyList(t *testing.T) {
	params := map[string]any{
		"hosts": []any{},
	}

	_, err := ParseHosts(params)
	if err == nil {
		t.Fatal("ParseHosts() expected error for empty hosts list")
	}
}

func TestParseHosts_missingName(t *testing.T) {
	params := map[string]any{
		"hosts": []any{
			map[string]any{
				"slots": float64(2),
			},
		},
	}

	_, err := ParseHosts(params)
	if err == nil {
		t.Fatal("ParseHosts() expected error for missing name")
	}
}

func TestParseHosts_zeroSlots(t *testing.T) {
	params := map[string]any{
		"hosts": []any{
			map[string]any{
				"name":  "host-1",
				"slots": float64(0),
			},
		},
	}

	_, err := ParseHosts(params)
	if err == nil {
		t.Fatal("ParseHosts() expected error for zero slots")
	}
}

func TestParseSSHConfig(t *testing.T) {
	params := map[string]any{
		"ssh": map[string]any{
			"user": "jumpstarter",
			"port": float64(2222),
		},
	}

	cfg := ParseSSHConfig(params)
	if cfg.User != "jumpstarter" {
		t.Errorf("User = %q, want jumpstarter", cfg.User)
	}
	if cfg.Port != 2222 {
		t.Errorf("Port = %d, want 2222", cfg.Port)
	}
}

func TestParseSSHConfig_missing(t *testing.T) {
	cfg := ParseSSHConfig(map[string]any{})
	if cfg.User != "" || cfg.Port != 0 {
		t.Errorf("expected zero SSHConfig, got %+v", cfg)
	}
}

func TestResolveSSHUser(t *testing.T) {
	cases := []struct {
		name     string
		host     HostConfig
		pool     SSHConfig
		wantUser string
	}{
		{"host override", HostConfig{User: "admin"}, SSHConfig{User: "default"}, "admin"},
		{"pool default", HostConfig{}, SSHConfig{User: "default"}, "default"},
		{"fallback root", HostConfig{}, SSHConfig{}, "root"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := ResolveSSHUser(tc.host, tc.pool); got != tc.wantUser {
				t.Errorf("ResolveSSHUser() = %q, want %q", got, tc.wantUser)
			}
		})
	}
}

func TestResolveSSHPort(t *testing.T) {
	cases := []struct {
		name     string
		host     HostConfig
		pool     SSHConfig
		wantPort int
	}{
		{"host override", HostConfig{Port: 2222}, SSHConfig{Port: 3333}, 2222},
		{"pool default", HostConfig{}, SSHConfig{Port: 3333}, 3333},
		{"fallback 22", HostConfig{}, SSHConfig{}, 22},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := ResolveSSHPort(tc.host, tc.pool); got != tc.wantPort {
				t.Errorf("ResolveSSHPort() = %d, want %d", got, tc.wantPort)
			}
		})
	}
}

func TestSelectHost_picksFirstAvailable(t *testing.T) {
	hosts := []HostConfig{
		{Name: "host-a", Slots: 1},
		{Name: "host-b", Slots: 2},
	}

	existing := []ExporterAnnotations{
		&fakeExporter{name: "exp-1", annotations: map[string]string{AnnotationHost: "host-a"}},
	}

	got, err := SelectHost(hosts, existing)
	if err != nil {
		t.Fatalf("SelectHost() error = %v", err)
	}
	if got.Name != "host-b" {
		t.Errorf("SelectHost() = %q, want host-b (host-a is full)", got.Name)
	}
}

func TestSelectHost_emptyPool(t *testing.T) {
	hosts := []HostConfig{
		{Name: "host-a", Slots: 2},
		{Name: "host-b", Slots: 2},
	}

	got, err := SelectHost(hosts, nil)
	if err != nil {
		t.Fatalf("SelectHost() error = %v", err)
	}
	if got.Name != "host-a" {
		t.Errorf("SelectHost() = %q, want host-a (first available)", got.Name)
	}
}

func TestSelectHost_allFull(t *testing.T) {
	hosts := []HostConfig{
		{Name: "host-a", Slots: 1},
	}

	existing := []ExporterAnnotations{
		&fakeExporter{name: "exp-1", annotations: map[string]string{AnnotationHost: "host-a"}},
	}

	_, err := SelectHost(hosts, existing)
	if err == nil {
		t.Fatal("SelectHost() expected error when all hosts are full")
	}
}

func TestSelectHost_multipleAssignmentsPerHost(t *testing.T) {
	hosts := []HostConfig{
		{Name: "host-a", Slots: 3},
	}

	existing := []ExporterAnnotations{
		&fakeExporter{name: "exp-1", annotations: map[string]string{AnnotationHost: "host-a"}},
		&fakeExporter{name: "exp-2", annotations: map[string]string{AnnotationHost: "host-a"}},
	}

	got, err := SelectHost(hosts, existing)
	if err != nil {
		t.Fatalf("SelectHost() error = %v", err)
	}
	if got.Name != "host-a" {
		t.Errorf("SelectHost() = %q, want host-a (has 1 free slot)", got.Name)
	}
}

func TestSelectHost_ignoresUnassigned(t *testing.T) {
	hosts := []HostConfig{
		{Name: "host-a", Slots: 1},
	}

	existing := []ExporterAnnotations{
		&fakeExporter{name: "exp-1", annotations: nil},
		&fakeExporter{name: "exp-2", annotations: map[string]string{}},
	}

	got, err := SelectHost(hosts, existing)
	if err != nil {
		t.Fatalf("SelectHost() error = %v", err)
	}
	if got.Name != "host-a" {
		t.Errorf("SelectHost() = %q, want host-a", got.Name)
	}
}

func TestParseRuntimeConfig_kvmEnabled(t *testing.T) {
	params := map[string]any{
		"runtime": map[string]any{
			"kvm": true,
		},
	}

	kvm, devices := ParseRuntimeConfig(params)
	if !kvm {
		t.Error("kvm = false, want true")
	}
	if len(devices) != 0 {
		t.Errorf("devices = %v, want empty", devices)
	}
}

func TestParseRuntimeConfig_withDevices(t *testing.T) {
	params := map[string]any{
		"runtime": map[string]any{
			"kvm":     true,
			"devices": []any{"/dev/vhost-net", "/dev/net/tun"},
		},
	}

	kvm, devices := ParseRuntimeConfig(params)
	if !kvm {
		t.Error("kvm = false, want true")
	}
	if len(devices) != 2 {
		t.Fatalf("devices = %v, want 2 entries", devices)
	}
	if devices[0] != "/dev/vhost-net" || devices[1] != "/dev/net/tun" {
		t.Errorf("devices = %v", devices)
	}
}

func TestParseRuntimeConfig_missing(t *testing.T) {
	kvm, devices := ParseRuntimeConfig(map[string]any{})
	if kvm {
		t.Error("kvm = true, want false")
	}
	if devices != nil {
		t.Errorf("devices = %v, want nil", devices)
	}
}
