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
	"strings"
	"testing"
)

func baseConfig() QuadletConfig {
	return QuadletConfig{
		Name:          "rpi4-virtual-abc12",
		Namespace:     "jumpstarter",
		ExporterImage: "quay.io/jumpstarter-dev/jumpstarter:latest",
		RuntimeImage:  "quay.io/jumpstarter-dev/virtual/qemu-runtime:latest",
	}
}

func TestRuntimeContainerFile_basic(t *testing.T) {
	cfg := baseConfig()
	got := RuntimeContainerFile(cfg)

	mustContain(t, got, "[Unit]")
	mustContain(t, got, "Description=Jumpstarter QEMU Runtime for rpi4-virtual-abc12")
	mustContain(t, got, "[Container]")
	mustContain(t, got, "ContainerName=rpi4-virtual-abc12-runtime")
	mustContain(t, got, "Image=quay.io/jumpstarter-dev/virtual/qemu-runtime:latest")
	mustContain(t, got, "Volume=jumpstarter-rpi4-virtual-abc12-shared:/shared:z")
	mustContain(t, got, "JUMPSTARTER_EXEC_LOG_FIELDS=component=exporter,exporter=rpi4-virtual-abc12,namespace=jumpstarter")
	mustContain(t, got, "[Service]")
	mustContain(t, got, "Restart=always")
	mustContain(t, got, "[Install]")
	mustContain(t, got, "WantedBy=default.target")
	mustNotContain(t, got, "AddDevice")
}

func TestRuntimeContainerFile_withKVM(t *testing.T) {
	cfg := baseConfig()
	cfg.KVM = true
	got := RuntimeContainerFile(cfg)

	mustContain(t, got, "AddDevice=/dev/kvm")
}

func TestRuntimeContainerFile_withExtraDevices(t *testing.T) {
	cfg := baseConfig()
	cfg.ExtraDevices = []string{"/dev/vhost-net", "/dev/net/tun"}
	got := RuntimeContainerFile(cfg)

	mustContain(t, got, "AddDevice=/dev/vhost-net")
	mustContain(t, got, "AddDevice=/dev/net/tun")
}

func TestExporterContainerFile_basic(t *testing.T) {
	cfg := baseConfig()
	got := ExporterContainerFile(cfg)

	mustContain(t, got, "[Unit]")
	mustContain(t, got, "Description=Jumpstarter Exporter for rpi4-virtual-abc12")
	mustContain(t, got, "Requires=rpi4-virtual-abc12-runtime.service")
	mustContain(t, got, "After=rpi4-virtual-abc12-runtime.service")
	mustContain(t, got, "[Container]")
	mustContain(t, got, "ContainerName=rpi4-virtual-abc12-exporter")
	mustContain(t, got, "Image=quay.io/jumpstarter-dev/jumpstarter:latest")
	mustContain(t, got, "Volume=jumpstarter-rpi4-virtual-abc12-shared:/shared:z")
	mustContain(t, got, "Volume=/etc/jumpstarter/exporters:/etc/jumpstarter/exporters:ro")
	mustContain(t, got, "Environment=JUMPSTARTER_LAUNCHER_SOCKET=/shared/launcher.sock")
	mustContain(t, got, "Exec=jmp run --exporter-config /etc/jumpstarter/exporters/rpi4-virtual-abc12.yaml")
	mustContain(t, got, "[Service]")
	mustContain(t, got, "Restart=on-failure")
	mustContain(t, got, "[Install]")
	mustContain(t, got, "WantedBy=default.target")
}

func TestFileNames(t *testing.T) {
	name := "demo-set-xyz"

	if got := RuntimeContainerFileName(name); got != "demo-set-xyz-runtime.container" {
		t.Errorf("RuntimeContainerFileName = %q", got)
	}
	if got := ExporterContainerFileName(name); got != "demo-set-xyz-exporter.container" {
		t.Errorf("ExporterContainerFileName = %q", got)
	}
}

func TestServiceNames(t *testing.T) {
	name := "demo-set-xyz"

	if got := RuntimeServiceName(name); got != "demo-set-xyz-runtime" {
		t.Errorf("RuntimeServiceName = %q", got)
	}
	if got := ExporterServiceName(name); got != "demo-set-xyz-exporter" {
		t.Errorf("ExporterServiceName = %q", got)
	}
}

func TestPodmanVolumeName(t *testing.T) {
	if got := PodmanVolumeName("rpi4-abc"); got != "jumpstarter-rpi4-abc-shared" {
		t.Errorf("PodmanVolumeName = %q", got)
	}
}

func mustContain(t *testing.T, got, want string) {
	t.Helper()
	if !strings.Contains(got, want) {
		t.Errorf("output missing %q\ngot:\n%s", want, got)
	}
}

func mustNotContain(t *testing.T, got, unwanted string) {
	t.Helper()
	if strings.Contains(got, unwanted) {
		t.Errorf("output should not contain %q\ngot:\n%s", unwanted, got)
	}
}
