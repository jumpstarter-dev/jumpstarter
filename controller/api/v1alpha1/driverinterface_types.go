/*
Copyright 2024.

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

package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// DriverInterfaceProto defines the proto definition for a driver interface.
type DriverInterfaceProto struct {
	// Package is the proto package name used for matching drivers to interfaces
	// (e.g., jumpstarter.interfaces.power.v1).
	Package string `json:"package"`
	// Descriptor is the canonical FileDescriptorProto as base64-encoded bytes.
	// +optional
	Descriptor []byte `json:"descriptor,omitempty"`
}

// DriverImplementation defines a driver implementation for a specific language.
type DriverImplementation struct {
	// Language is the programming language of this driver implementation (e.g., python, java, go).
	Language string `json:"language"`
	// Package is the package name for this driver implementation.
	Package string `json:"package"`
	// Version is the version constraint for the driver package.
	// +optional
	Version string `json:"version,omitempty"`
	// Index is the package index URL for this driver.
	// +optional
	Index string `json:"index,omitempty"`
	// ClientClass is the language-specific client proxy class path
	// (e.g., jumpstarter_driver_power.client:PowerClient).
	// +optional
	ClientClass string `json:"clientClass,omitempty"`
	// DriverClasses lists the language-specific driver implementation class paths.
	// +optional
	DriverClasses []string `json:"driverClasses,omitempty"`
}

// DriverInterfaceSpec defines the desired state of DriverInterface.
type DriverInterfaceSpec struct {
	// Proto defines the proto definition for this driver interface.
	Proto DriverInterfaceProto `json:"proto"`
	// Drivers lists driver implementations for this interface, per language.
	// +optional
	Drivers []DriverImplementation `json:"drivers,omitempty"`
}

// DriverInterfaceStatus defines the observed state of DriverInterface.
type DriverInterfaceStatus struct {
	// ImplementationCount is the number of exporters implementing this interface.
	ImplementationCount int `json:"implementationCount,omitempty"`
	// Conditions represent the latest available observations of the DriverInterface's state.
	Conditions []metav1.Condition `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
}

type DriverInterfaceConditionType string

const (
	DriverInterfaceConditionTypeReady DriverInterfaceConditionType = "Ready"
)

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Package",type="string",JSONPath=".spec.proto.package"
// +kubebuilder:printcolumn:name="Implementations",type="integer",JSONPath=".status.implementationCount"

// DriverInterface is the Schema for the driverinterfaces API.
// A DriverInterface names an interface, references its canonical proto definition,
// and specifies driver implementations per language.
type DriverInterface struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   DriverInterfaceSpec   `json:"spec,omitempty"`
	Status DriverInterfaceStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// DriverInterfaceList contains a list of DriverInterface.
type DriverInterfaceList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []DriverInterface `json:"items"`
}

func init() {
	SchemeBuilder.Register(&DriverInterface{}, &DriverInterfaceList{})
}
