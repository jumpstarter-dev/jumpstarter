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

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
)

const (
	// QemuDriverType is the Python class path for the QEMU driver.
	QemuDriverType = "jumpstarter_driver_qemu.driver.Qemu"
)

// OwnedChildTypes lists driver types that Qemu.__post_init__
// creates as children (they require parent=Qemu). These must not
// appear as top-level drivers in the export map — use a ref instead
// (e.g. ref: qemu.power).
var OwnedChildTypes = map[string]struct{}{
	"jumpstarter_driver_qemu.driver.QemuPower":   {},
	"jumpstarter_driver_qemu.driver.QemuFlasher": {},
}

// ValidateNoOwnedChildren checks that no driver in the list is an
// owned child type. Returns an error with guidance on how to fix it.
func ValidateNoOwnedChildren(drivers []virtualtargetv1alpha1.DriverConfig) error {
	for _, d := range drivers {
		if _, owned := OwnedChildTypes[d.Type]; owned {
			return fmt.Errorf(
				"driver %q has type %q which is auto-created as a child of Qemu "+
					"and cannot be a top-level driver (it would crash with a missing "+
					"parent argument); use a ref instead, e.g.: "+
					`{name: %q, ref: "<qemu-driver-name>.power"}`,
				d.Name, d.Type, d.Name,
			)
		}
	}
	return nil
}

// EnrichFunc is called for each QEMU driver entry to apply
// provisioner-specific enrichment (e.g. launcher_socket, firmware).
type EnrichFunc func(d virtualtargetv1alpha1.DriverConfig) (virtualtargetv1alpha1.DriverConfig, error)

// EnrichAndAliasPower iterates drivers, calls enrichFn on each QEMU
// driver entry, and appends a root-level power ref alias when no
// explicit power driver exists. Callers must call
// ValidateNoOwnedChildren first.
func EnrichAndAliasPower(
	drivers []virtualtargetv1alpha1.DriverConfig,
	enrichFn EnrichFunc,
) ([]virtualtargetv1alpha1.DriverConfig, error) {
	result := make([]virtualtargetv1alpha1.DriverConfig, 0, len(drivers))
	qemuName := ""
	hasPower := false

	for _, d := range drivers {
		if d.Type == QemuDriverType {
			qemuName = d.Name
			var err error
			d, err = enrichFn(d)
			if err != nil {
				return nil, err
			}
		}
		if d.Name == "power" {
			hasPower = true
		}
		result = append(result, d)
	}

	if qemuName != "" && !hasPower {
		result = append(result, virtualtargetv1alpha1.DriverConfig{
			Name: "power",
			Ref:  qemuName + ".power",
		})
	}

	return result, nil
}
