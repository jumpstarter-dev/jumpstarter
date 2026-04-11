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

// InterfaceRequirement defines a required or optional interface for an ExporterClass.
type InterfaceRequirement struct {
	// Name is the accessor name used in generated client code (e.g., power, serial).
	Name string `json:"name"`
	// InterfaceRef references a DriverInterface by name within the same namespace.
	InterfaceRef string `json:"interfaceRef"`
	// Required indicates whether this interface must be present for an exporter
	// to satisfy this ExporterClass.
	// +kubebuilder:default:=true
	Required bool `json:"required"`
}

// ExporterClassSpec defines the desired state of ExporterClass.
type ExporterClassSpec struct {
	// Extends optionally references a parent ExporterClass by name to inherit
	// its selector and interface requirements.
	// +optional
	Extends string `json:"extends,omitempty"`
	// Selector specifies standard Kubernetes label selectors for exporter matching.
	// +optional
	Selector *metav1.LabelSelector `json:"selector,omitempty"`
	// Interfaces lists the required and optional driver interface requirements.
	// +optional
	Interfaces []InterfaceRequirement `json:"interfaces,omitempty"`
}

// ExporterClassStatus defines the observed state of ExporterClass.
type ExporterClassStatus struct {
	// SatisfiedExporterCount is the number of exporters satisfying all required interfaces.
	SatisfiedExporterCount int `json:"satisfiedExporterCount,omitempty"`
	// ResolvedInterfaces is the flattened list of DriverInterface names after
	// resolving the extends chain.
	ResolvedInterfaces []string `json:"resolvedInterfaces,omitempty"`
	// Conditions represent the latest available observations of the ExporterClass's state.
	Conditions []metav1.Condition `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
}

type ExporterClassConditionType string

const (
	ExporterClassConditionTypeReady    ExporterClassConditionType = "Ready"
	ExporterClassConditionTypeDegraded ExporterClassConditionType = "Degraded"
)

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Extends",type="string",JSONPath=".spec.extends"
// +kubebuilder:printcolumn:name="Satisfied",type="integer",JSONPath=".status.satisfiedExporterCount"

// ExporterClass is the Schema for the exporterclasses API.
// An ExporterClass declares a device profile as a set of required and optional
// DriverInterface references plus label selectors.
type ExporterClass struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   ExporterClassSpec   `json:"spec,omitempty"`
	Status ExporterClassStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// ExporterClassList contains a list of ExporterClass.
type ExporterClassList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []ExporterClass `json:"items"`
}

func init() {
	SchemeBuilder.Register(&ExporterClass{}, &ExporterClassList{})
}
