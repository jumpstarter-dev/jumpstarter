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

	"k8s.io/apimachinery/pkg/api/resource"
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
}

type StorageConfig struct {
	Type             string
	Size             string
	StorageClassName string
	AccessModes      []string
	Source           StorageSource
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
		sc, err := parseStorageConfig(stor)
		if err != nil {
			return nil, err
		}
		p.Storage = sc
		if p.Storage.Size != "" {
			p.DiskSize = p.Storage.Size
		}
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

func parseStorageConfig(stor map[string]any) (*StorageConfig, error) {
	sc := &StorageConfig{
		Type:        StorageTypeContainerDisk,
		Size:        defaultDiskSize,
		AccessModes: []string{"ReadWriteOnce"},
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

	if size, ok := stor["size"].(string); ok && size != "" {
		sc.Size = size
	}

	if scn, ok := stor["storageClassName"].(string); ok && scn != "" {
		sc.StorageClassName = scn
	}

	if am, ok := stor["accessModes"].([]any); ok && len(am) > 0 {
		sc.AccessModes = make([]string, 0, len(am))
		for _, v := range am {
			if s, ok := v.(string); ok {
				sc.AccessModes = append(sc.AccessModes, s)
			}
		}
	}

	if src, ok := stor["source"].(map[string]any); ok {
		if httpSrc, ok := src["http"].(map[string]any); ok {
			url, _ := httpSrc["url"].(string)
			if url == "" {
				return nil, fmt.Errorf("storage source http requires \"url\"")
			}
			sc.Source.HTTP = &HTTPSource{URL: url}
		}
		if regSrc, ok := src["registry"].(map[string]any); ok {
			img, _ := regSrc["image"].(string)
			if img == "" {
				return nil, fmt.Errorf("storage source registry requires \"image\"")
			}
			sc.Source.Registry = &RegistrySource{Image: img}
		}
		if pvcSrc, ok := src["pvc"].(map[string]any); ok {
			name, _ := pvcSrc["name"].(string)
			ns, _ := pvcSrc["namespace"].(string)
			if name == "" {
				return nil, fmt.Errorf("storage source pvc requires \"name\"")
			}
			sc.Source.PVC = &PVCSource{Name: name, Namespace: ns}
		}
	}

	if sc.Type == StorageTypeDataVolume && sc.Source == (StorageSource{}) {
		return nil, fmt.Errorf("storage type %q requires a source (http, registry, or pvc)", StorageTypeDataVolume)
	}

	return sc, nil
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
