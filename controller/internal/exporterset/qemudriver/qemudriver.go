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

// Package qemudriver holds constants and helpers shared across QEMU
// provisioner variants (in-cluster and qemu-ssh).
package qemudriver

import (
	"fmt"
	"strings"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
)

const (
	// QemuDriverType is the Python class path for the QEMU driver.
	QemuDriverType = "jumpstarter_driver_qemu.driver.Qemu"

	// ExposeRootDriversParam controls whether PresetRootChildren are
	// auto-aliased as root-level refs. Set to false in merged parameters
	// to keep a nested-only export map. Defaults to true when unset.
	ExposeRootDriversParam = "exposeRootDrivers"
)

// OwnedChildTypes lists driver types that Qemu.__post_init__
// creates as children (they require parent=Qemu). These must not
// appear as top-level drivers in the export map.
var OwnedChildTypes = map[string]struct{}{
	"jumpstarter_driver_qemu.driver.QemuPower":   {},
	"jumpstarter_driver_qemu.driver.QemuFlasher": {},
}

// PresetRootChildren are Qemu child names exposed as root-level ref
// aliases so clients match hardware-style flat export maps
// (client.power, client.flasher, client.console).
var PresetRootChildren = []string{"power", "flasher", "console"}

// ValidateNoOwnedChildren checks that no driver in the list is an
// owned child type. Returns an error with guidance on how to fix it.
func ValidateNoOwnedChildren(drivers []virtualtargetv1alpha1.DriverConfig) error {
	for _, d := range drivers {
		if _, owned := OwnedChildTypes[d.Type]; owned {
			return fmt.Errorf(
				"driver %q has type %q which is auto-created as a child of Qemu "+
					"and cannot be a top-level driver (it would crash with a missing "+
					"parent argument); remove it from the drivers list",
				d.Name, d.Type,
			)
		}
	}
	return nil
}

// ExposeRootDriversEnabled reports whether root driver aliases should
// be added. Defaults to true when the parameter is unset or not a bool.
func ExposeRootDriversEnabled(params map[string]any) bool {
	if params == nil {
		return true
	}
	v, ok := params[ExposeRootDriversParam]
	if !ok {
		return true
	}
	b, ok := v.(bool)
	if !ok {
		return true
	}
	return b
}

// AliasPresetRoots appends root-level ref aliases for PresetRootChildren
// when enabled (via parameters.exposeRootDrivers, default true), a single
// QEMU driver is present, its name has no dots (Proxy refs split on "."),
// and the root name is not already taken. Nested paths remain available.
func AliasPresetRoots(
	drivers []virtualtargetv1alpha1.DriverConfig,
	params map[string]any,
) []virtualtargetv1alpha1.DriverConfig {
	if !ExposeRootDriversEnabled(params) {
		return drivers
	}

	qemuName := ""
	qemuCount := 0
	present := make(map[string]struct{}, len(drivers))

	for _, d := range drivers {
		present[d.Name] = struct{}{}
		if d.Type == QemuDriverType {
			qemuCount++
			qemuName = d.Name
		}
	}
	if qemuCount != 1 {
		return drivers
	}
	// Proxy resolves refs by splitting on "."; a driver name that
	// already contains dots would produce an unresolvable path.
	if strings.Contains(qemuName, ".") {
		return drivers
	}

	result := make([]virtualtargetv1alpha1.DriverConfig, len(drivers), len(drivers)+len(PresetRootChildren))
	copy(result, drivers)

	for _, child := range PresetRootChildren {
		if _, exists := present[child]; exists {
			continue
		}
		result = append(result, virtualtargetv1alpha1.DriverConfig{
			Name: child,
			Ref:  qemuName + "." + child,
		})
	}
	return result
}
