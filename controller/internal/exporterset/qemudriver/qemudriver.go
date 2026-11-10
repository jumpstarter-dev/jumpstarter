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

// OwnedChildTypes lists driver types that Qemu.__post_init__
// creates as children (they require parent=Qemu). These must not
// appear as top-level drivers in the export map.
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
					"parent argument); remove it from the drivers list",
				d.Name, d.Type,
			)
		}
	}
	return nil
}
