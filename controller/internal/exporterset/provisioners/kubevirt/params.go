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

package kubevirt

import (
	"fmt"
	"math"
	"net/url"

	"k8s.io/apimachinery/pkg/api/resource"
	"k8s.io/apimachinery/pkg/util/validation"
)

const (
	defaultCPU      = 2
	defaultMemory   = "4Gi"
	defaultDiskSize = "20Gi"

	StorageTypeContainerDisk = "containerDisk"
	StorageTypeDataVolume    = "dataVolume"
)

type HTTPSource struct {
	URL string
}

type RegistrySource struct {
	Image string
}

type PVCSource struct {
	Name      string
	Namespace string
}

type StorageSource struct {
	HTTP     *HTTPSource
	Registry *RegistrySource
	PVC      *PVCSource
	Blank    bool
}

type DiskConfig struct {
	Size             string
	StorageClassName string
	AccessModes      []string
	VolumeMode       string
	BootOrder        *uint
	Source           StorageSource
}

type StorageConfig struct {
	Type           string
	Disks          map[string]DiskConfig
	UploadProxyURL string
	UploadProxyCA  string
}

// KubevirtParams holds parsed and validated parameters from VTC/ExporterSet
// mergedParameters for the KubeVirt provisioner.
type KubevirtParams struct {
	Namespace    string
	CPU          int
	Memory       string
	DiskSize     string
	Image        string
	SSHAccess    string
	ForwardPorts map[string]int
	CloudInit    string
	Storage      *StorageConfig
}

// parseParams extracts and validates KubevirtParams from merged parameters.
func parseParams(params map[string]any) (*KubevirtParams, error) {
	p := &KubevirtParams{
		CPU:      defaultCPU,
		Memory:   defaultMemory,
		DiskSize: defaultDiskSize,
	}

	if ns, ok := params["namespace"].(string); ok && ns != "" {
		p.Namespace = ns
	}
	if p.Namespace == "" {
		return nil, fmt.Errorf("required parameter \"namespace\" not set")
	}

	if img, ok := params["image"].(string); ok && img != "" {
		p.Image = img
	}

	if resources, ok := params["resources"].(map[string]any); ok {
		if cpu, ok := toInt(resources["cpu"]); ok {
			if cpu <= 0 || cpu > math.MaxUint32 {
				return nil, fmt.Errorf("invalid cpu %d: must be between 1 and %d", cpu, math.MaxUint32)
			}
			p.CPU = cpu
		}
		if mem, ok := resources["memory"].(string); ok && mem != "" {
			p.Memory = mem
		}
		if disk, ok := resources["storage"].(string); ok && disk != "" {
			p.DiskSize = disk
		}
	}

	if ssh, ok := params["sshAccess"].(string); ok {
		switch ssh {
		case "", "NodePort", "LoadBalancer":
			p.SSHAccess = ssh
		default:
			return nil, fmt.Errorf("invalid sshAccess %q; must be \"NodePort\", \"LoadBalancer\", or empty", ssh)
		}
	}

	if fwd, ok := params["forwardPorts"].(map[string]any); ok {
		p.ForwardPorts = make(map[string]int, len(fwd))
		for name, v := range fwd {
			port, ok := toInt(v)
			if !ok {
				return nil, fmt.Errorf("forwardPorts[%q]: value %v is not a valid integer", name, v)
			}
			if port < 1 || port > 65535 {
				return nil, fmt.Errorf("forwardPorts[%q]: port %d out of range 1-65535", name, port)
			}
			p.ForwardPorts[name] = port
		}
	}

	if ci, ok := params["cloudInit"].(string); ok {
		p.CloudInit = ci
	}

	if stor, ok := params["storage"].(map[string]any); ok {
		sc, err := parseStorageConfig(stor, p.DiskSize)
		if err != nil {
			return nil, err
		}
		p.Storage = sc
		p.DiskSize = sc.Disks["root"].Size
	}

	// Require either image (for containerDisk) or storage config (for dataVolume).
	if p.Image == "" && (p.Storage == nil || p.Storage.Type == StorageTypeContainerDisk) {
		return nil, fmt.Errorf("required parameter \"image\" not set (needed for containerDisk storage)")
	}

	if _, err := resource.ParseQuantity(p.Memory); err != nil {
		return nil, fmt.Errorf("invalid memory %q: %w", p.Memory, err)
	}
	if _, err := resource.ParseQuantity(p.DiskSize); err != nil {
		return nil, fmt.Errorf("invalid diskSize %q: %w", p.DiskSize, err)
	}

	return p, nil
}

func parseStorageConfig(stor map[string]any, defaultSize string) (*StorageConfig, error) {
	sc := &StorageConfig{
		Type: StorageTypeContainerDisk,
	}

	if t, ok := stor["type"].(string); ok && t != "" {
		switch t {
		case StorageTypeContainerDisk, StorageTypeDataVolume:
			sc.Type = t
		default:
			return nil, fmt.Errorf("invalid storage type %q; must be %q or %q",
				t, StorageTypeContainerDisk, StorageTypeDataVolume)
		}
	}

	defaults, err := parseDiskConfig(stor, DiskConfig{Size: defaultSize, AccessModes: []string{"ReadWriteOnce"}})
	if err != nil {
		return nil, err
	}
	sc.UploadProxyURL, _ = stor["uploadProxyURL"].(string)
	sc.UploadProxyCA, _ = stor["uploadProxyCA"].(string)
	if sc.UploadProxyURL != "" {
		u, err := url.Parse(sc.UploadProxyURL)
		if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
			return nil, fmt.Errorf("storage.uploadProxyURL must be an HTTPS URL without credentials, query, or fragment")
		}
	}
	sc.Disks = map[string]DiskConfig{"root": defaults}
	if raw, exists := stor["disks"]; exists {
		disks, ok := raw.(map[string]any)
		if !ok || len(disks) == 0 || sc.Type != StorageTypeDataVolume {
			return nil, fmt.Errorf("storage.disks must be a nonempty map with storage.type=dataVolume")
		}
		if _, exists := stor["source"]; exists {
			return nil, fmt.Errorf("use storage.disks.root.source instead of storage.source with named disks")
		}
		sc.Disks = make(map[string]DiskConfig, len(disks))
		for name, rawDisk := range disks {
			if len(validation.IsDNS1123Label(name)) != 0 || name == "cloudinit" {
				return nil, fmt.Errorf("invalid or reserved storage disk name %q", name)
			}
			disk, ok := rawDisk.(map[string]any)
			if !ok {
				return nil, fmt.Errorf("storage disk %q must be an object", name)
			}
			parsed, err := parseDiskConfig(disk, defaults)
			if err != nil {
				return nil, fmt.Errorf("storage disk %q: %w", name, err)
			}
			sc.Disks[name] = parsed
		}
	}
	root, ok := sc.Disks["root"]
	if !ok {
		return nil, fmt.Errorf("storage.disks must include root")
	}
	if root.BootOrder == nil {
		order := uint(1)
		root.BootOrder = &order
		sc.Disks["root"] = root
	}
	orders := make(map[uint]bool)
	for _, disk := range sc.Disks {
		if disk.BootOrder != nil {
			if orders[*disk.BootOrder] {
				return nil, fmt.Errorf("disk bootOrder values must be unique")
			}
			orders[*disk.BootOrder] = true
		}
	}
	return sc, nil
}

func parseDiskConfig(raw map[string]any, disk DiskConfig) (DiskConfig, error) {
	for key, dest := range map[string]*string{
		"size": &disk.Size, "storageClassName": &disk.StorageClassName, "volumeMode": &disk.VolumeMode,
	} {
		if value, exists := raw[key]; exists {
			s, ok := value.(string)
			if !ok || s == "" {
				return disk, fmt.Errorf("%s must be a nonempty string", key)
			}
			*dest = s
		}
	}
	if size, err := resource.ParseQuantity(disk.Size); err != nil || size.Sign() <= 0 {
		return disk, fmt.Errorf("invalid disk size %q", disk.Size)
	}
	if disk.VolumeMode != "" && disk.VolumeMode != "Block" && disk.VolumeMode != "Filesystem" {
		return disk, fmt.Errorf("volumeMode must be Block or Filesystem")
	}
	if rawModes, exists := raw["accessModes"]; exists {
		am, ok := rawModes.([]any)
		if !ok || len(am) == 0 {
			return disk, fmt.Errorf("accessModes must be a nonempty list")
		}
		disk.AccessModes = make([]string, 0, len(am))
		for _, v := range am {
			s, ok := v.(string)
			if !ok || (s != "ReadWriteOnce" && s != "ReadWriteMany" && s != "ReadWriteOncePod") {
				return disk, fmt.Errorf("invalid writable access mode %v", v)
			}
			disk.AccessModes = append(disk.AccessModes, s)
		}
	}
	if value, exists := raw["bootOrder"]; exists {
		order, ok := toInt(value)
		if !ok || order <= 0 || order > math.MaxUint32 {
			return disk, fmt.Errorf("bootOrder must be a positive integer")
		}
		u := uint(order)
		disk.BootOrder = &u
	}
	var err error
	disk.Source, err = parseStorageSource(raw)
	return disk, err
}

func parseStorageSource(raw map[string]any) (StorageSource, error) {
	source := StorageSource{Blank: true}
	if value, exists := raw["source"]; exists {
		src, ok := value.(map[string]any)
		if !ok || len(src) != 1 {
			return source, fmt.Errorf("source must specify exactly one of http, registry, pvc, or blank")
		}
		source = StorageSource{}
		if httpSrc, ok := src["http"].(map[string]any); ok {
			url, _ := httpSrc["url"].(string)
			if url == "" {
				return source, fmt.Errorf("storage source http requires \"url\"")
			}
			source.HTTP = &HTTPSource{URL: url}
		}
		if regSrc, ok := src["registry"].(map[string]any); ok {
			img, _ := regSrc["image"].(string)
			if img == "" {
				return source, fmt.Errorf("storage source registry requires \"image\"")
			}
			source.Registry = &RegistrySource{Image: img}
		}
		if pvcSrc, ok := src["pvc"].(map[string]any); ok {
			name, _ := pvcSrc["name"].(string)
			ns, _ := pvcSrc["namespace"].(string)
			if name == "" {
				return source, fmt.Errorf("storage source pvc requires \"name\"")
			}
			source.PVC = &PVCSource{Name: name, Namespace: ns}
		}
		if blank, ok := src["blank"].(map[string]any); ok && len(blank) == 0 {
			source.Blank = true
		}
		if source == (StorageSource{}) {
			return source, fmt.Errorf("invalid storage source: expected http, registry, pvc, or blank")
		}
	}
	return source, nil
}

// toInt converts a numeric interface{} (float64 from JSON, int, etc.) to int.
// Returns false for non-numeric types or values that would overflow int.
func toInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int64:
		if n < math.MinInt || n > math.MaxInt {
			return 0, false
		}
		return int(n), true
	case float64:
		if n != math.Trunc(n) || n < math.MinInt || n > math.MaxInt {
			return 0, false
		}
		return int(n), true
	default:
		return 0, false
	}
}
