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
	"fmt"
	"strings"
)

const (
	// QuadletDir is the systemd directory for Podman quadlet
	// .container files.
	QuadletDir = "/etc/containers/systemd"

	// ExporterConfigDir is where exporter config YAML files are
	// placed on remote hosts.
	ExporterConfigDir = "/etc/jumpstarter/exporters"

	// sharedVolumeSuffix is appended to the Podman volume name.
	sharedVolumeSuffix = "-shared"

	// sharedMountPath is the container mount for the shared volume
	// (Unix sockets: QMP, serial, launcher).
	sharedMountPath = "/shared"

	// launcherSocketPath is the Unix socket used by jumpstarter-exec.
	launcherSocketPath = "/shared/launcher.sock"
)

// QuadletConfig holds the parameters needed to generate quadlet
// .container files for a single exporter instance.
type QuadletConfig struct {
	// Name is the exporter instance name (used in container and
	// service names).
	Name string

	// Namespace is the Kubernetes namespace (for log context).
	Namespace string

	// ExporterImage is the exporter container image.
	ExporterImage string

	// RuntimeImage is the QEMU runtime container image.
	RuntimeImage string

	// KVM enables /dev/kvm device passthrough to the runtime
	// container.
	KVM bool

	// ExtraDevices lists additional host devices to pass through to
	// the runtime container (e.g. /dev/vhost-net).
	ExtraDevices []string
}

// RuntimeContainerFile generates the Podman quadlet .container file
// for the QEMU runtime sidecar.
func RuntimeContainerFile(cfg QuadletConfig) string {
	volumeName := podmanVolumeName(cfg.Name)

	var b strings.Builder

	b.WriteString("[Unit]\n")
	fmt.Fprintf(&b, "Description=Jumpstarter QEMU Runtime for %s\n", cfg.Name)
	b.WriteString("\n")

	b.WriteString("[Container]\n")
	fmt.Fprintf(&b, "ContainerName=%s-runtime\n", cfg.Name)
	fmt.Fprintf(&b, "Image=%s\n", cfg.RuntimeImage)
	fmt.Fprintf(&b, "Volume=%s:%s:z\n", volumeName, sharedMountPath)
	fmt.Fprintf(&b,
		"Environment=JUMPSTARTER_EXEC_LOG_FIELDS=component=exporter,exporter=%s,namespace=%s\n",
		cfg.Name, cfg.Namespace,
	)

	if cfg.KVM {
		b.WriteString("AddDevice=/dev/kvm\n")
	}
	for _, dev := range cfg.ExtraDevices {
		fmt.Fprintf(&b, "AddDevice=%s\n", dev)
	}

	b.WriteString("\n")

	b.WriteString("[Service]\n")
	b.WriteString("Restart=always\n")
	b.WriteString("\n")

	b.WriteString("[Install]\n")
	b.WriteString("WantedBy=default.target\n")

	return b.String()
}

// ExporterContainerFile generates the Podman quadlet .container file
// for the Jumpstarter exporter.
func ExporterContainerFile(cfg QuadletConfig) string {
	volumeName := podmanVolumeName(cfg.Name)
	runtimeService := cfg.Name + "-runtime"
	configFile := ExporterConfigDir + "/" + cfg.Name + ".yaml"

	var b strings.Builder

	b.WriteString("[Unit]\n")
	fmt.Fprintf(&b, "Description=Jumpstarter Exporter for %s\n", cfg.Name)
	fmt.Fprintf(&b, "Requires=%s.service\n", runtimeService)
	fmt.Fprintf(&b, "After=%s.service\n", runtimeService)
	b.WriteString("\n")

	b.WriteString("[Container]\n")
	fmt.Fprintf(&b, "ContainerName=%s-exporter\n", cfg.Name)
	fmt.Fprintf(&b, "Image=%s\n", cfg.ExporterImage)
	fmt.Fprintf(&b, "Volume=%s:%s:z\n", volumeName, sharedMountPath)
	fmt.Fprintf(&b, "Volume=%s:%s:ro\n", ExporterConfigDir, ExporterConfigDir)
	fmt.Fprintf(&b, "Environment=JUMPSTARTER_LAUNCHER_SOCKET=%s\n", launcherSocketPath)
	fmt.Fprintf(&b, "Exec=jmp run --exporter-config %s\n", configFile)
	b.WriteString("\n")

	b.WriteString("[Service]\n")
	b.WriteString("Restart=on-failure\n")
	b.WriteString("\n")

	b.WriteString("[Install]\n")
	b.WriteString("WantedBy=default.target\n")

	return b.String()
}

// RuntimeContainerFileName returns the quadlet filename for the
// runtime container.
func RuntimeContainerFileName(name string) string {
	return name + "-runtime.container"
}

// ExporterContainerFileName returns the quadlet filename for the
// exporter container.
func ExporterContainerFileName(name string) string {
	return name + "-exporter.container"
}

// RuntimeServiceName returns the systemd service name for the
// runtime container.
func RuntimeServiceName(name string) string {
	return name + "-runtime"
}

// ExporterServiceName returns the systemd service name for the
// exporter container.
func ExporterServiceName(name string) string {
	return name + "-exporter"
}

// PodmanVolumeName returns the Podman volume name for shared
// communication between exporter and runtime containers.
func PodmanVolumeName(name string) string {
	return podmanVolumeName(name)
}

func podmanVolumeName(name string) string {
	return "jumpstarter-" + name + sharedVolumeSuffix
}
