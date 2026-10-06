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

func TestParseHost_full(t *testing.T) {
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
		t.Errorf("host.Port = %d, want 2222", host.Port)
	}
	if host.User != "admin" {
		t.Errorf("host.User = %q, want admin", host.User)
	}
}

func TestParseHost_defaults(t *testing.T) {
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
	if host.User != "root" {
		t.Errorf("host.User = %q, want root (default)", host.User)
	}
	if host.Port != 22 {
		t.Errorf("host.Port = %d, want 22 (default)", host.Port)
	}
}

func TestParseHost_missingHostKey(t *testing.T) {
	_, err := ParseHost(map[string]any{})
	if err == nil {
		t.Fatal("expected error for missing host key")
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
		t.Fatal("expected error for missing name")
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
