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

func TestParseHost_valid(t *testing.T) {
	params := map[string]any{
		"host": map[string]any{
			"name": "bench-01.lab.example.com",
			"port": float64(2222),
			"user": "admin",
		},
	}

	host, err := ParseHost(params)
	if err != nil {
		t.Fatalf("ParseHost() error = %v", err)
	}

	if host.Name != "bench-01.lab.example.com" {
		t.Errorf("host.Name = %q", host.Name)
	}
	if host.Port != 2222 {
		t.Errorf("host.Port = %d", host.Port)
	}
	if host.User != "admin" {
		t.Errorf("host.User = %q", host.User)
	}
}

func TestParseHost_nameOnly(t *testing.T) {
	params := map[string]any{
		"host": map[string]any{
			"name": "bench-01.lab.example.com",
		},
	}

	host, err := ParseHost(params)
	if err != nil {
		t.Fatalf("ParseHost() error = %v", err)
	}
	if host.Name != "bench-01.lab.example.com" {
		t.Errorf("host.Name = %q", host.Name)
	}
	if host.Port != 0 || host.User != "" {
		t.Errorf("expected empty port/user overrides, got %+v", host)
	}
}

func TestParseHost_missingHostKey(t *testing.T) {
	_, err := ParseHost(map[string]any{})
	if err == nil {
		t.Fatal("ParseHost() expected error for missing host key")
	}
}

func TestParseHost_missingName(t *testing.T) {
	params := map[string]any{
		"host": map[string]any{
			"port": float64(2222),
		},
	}

	_, err := ParseHost(params)
	if err == nil {
		t.Fatal("ParseHost() expected error for missing name")
	}
}

func TestParseSSHConfig(t *testing.T) {
	params := map[string]any{
		"ssh": map[string]any{
			"user": "jumpstarter",
			"port": float64(2222),
		},
	}

	cfg, err := ParseSSHConfig(params)
	if err != nil {
		t.Fatalf("ParseSSHConfig() error = %v", err)
	}
	if cfg.User != "jumpstarter" {
		t.Errorf("User = %q, want jumpstarter", cfg.User)
	}
	if cfg.Port != 2222 {
		t.Errorf("Port = %d, want 2222", cfg.Port)
	}
}

func TestParseSSHConfig_missing(t *testing.T) {
	cfg, err := ParseSSHConfig(map[string]any{})
	if err != nil {
		t.Fatalf("ParseSSHConfig() error = %v", err)
	}
	if cfg.User != "" || cfg.Port != 0 {
		t.Errorf("expected zero SSHConfig, got %+v", cfg)
	}
}

func TestParseSSHConfig_invalidType(t *testing.T) {
	params := map[string]any{
		"ssh": map[string]any{
			"user": 12345,
		},
	}

	_, err := ParseSSHConfig(params)
	if err == nil {
		t.Fatal("ParseSSHConfig() expected error for invalid user type")
	}
}

func TestResolveSSHUser(t *testing.T) {
	cases := []struct {
		name     string
		host     HostConfig
		ssh      SSHConfig
		wantUser string
	}{
		{"host override", HostConfig{User: "admin"}, SSHConfig{User: "default"}, "admin"},
		{"ssh default", HostConfig{}, SSHConfig{User: "default"}, "default"},
		{"fallback root", HostConfig{}, SSHConfig{}, "root"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := ResolveSSHUser(tc.host, tc.ssh); got != tc.wantUser {
				t.Errorf("ResolveSSHUser() = %q, want %q", got, tc.wantUser)
			}
		})
	}
}

func TestResolveSSHPort(t *testing.T) {
	cases := []struct {
		name     string
		host     HostConfig
		ssh      SSHConfig
		wantPort int
	}{
		{"host override", HostConfig{Port: 2222}, SSHConfig{Port: 3333}, 2222},
		{"ssh default", HostConfig{}, SSHConfig{Port: 3333}, 3333},
		{"fallback 22", HostConfig{}, SSHConfig{}, 22},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := ResolveSSHPort(tc.host, tc.ssh); got != tc.wantPort {
				t.Errorf("ResolveSSHPort() = %d, want %d", got, tc.wantPort)
			}
		})
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
