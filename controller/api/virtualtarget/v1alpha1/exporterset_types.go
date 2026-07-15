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

package v1alpha1

import (
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// RecycleStrategy defines what happens when a lease is released.
type RecycleStrategy string

const (
	// RecycleStrategyExitAndReplace destroys and recreates the instance on lease release.
	RecycleStrategyExitAndReplace RecycleStrategy = "ExitAndReplace"
	// RecycleStrategyInPlaceReuse reuses the instance in place after lease release.
	RecycleStrategyInPlaceReuse RecycleStrategy = "InPlaceReuse"
)

// DriverConfig defines a single driver entry in an exporter template.
type DriverConfig struct {
	// Type is the fully qualified Python driver class name.
	Type string `json:"type"`

	// Config holds driver-specific configuration.
	// +optional
	// +kubebuilder:pruning:PreserveUnknownFields
	// +kubebuilder:validation:Schemaless
	Config *apiextensionsv1.JSON `json:"config,omitempty"`
}

// ExporterTemplateSpec defines the exporter configuration within a template.
type ExporterTemplateSpec struct {
	// Drivers is the list of drivers to configure on each exporter instance.
	// +optional
	Drivers []DriverConfig `json:"drivers,omitempty"`
}

// EmbeddedObjectMeta defines metadata fields allowed on ExporterSet templates.
type EmbeddedObjectMeta struct {
	// Labels to apply to created Exporter resources.
	// +optional
	Labels map[string]string `json:"labels,omitempty"`

	// Annotations to apply to created Exporter resources.
	// +optional
	Annotations map[string]string `json:"annotations,omitempty"`
}

// ExporterSetTemplate defines the template for exporter instances created by this set.
type ExporterSetTemplate struct {
	// Metadata for created Exporter resources.
	// +optional
	Metadata EmbeddedObjectMeta `json:"metadata,omitempty"`

	// Spec defines the exporter configuration.
	Spec ExporterTemplateSpec `json:"spec,omitempty"`
}

// ExporterSetSpec defines the desired state of ExporterSet.
// +kubebuilder:validation:XValidation:rule="self.maxReplicas == 0 || self.minReplicas <= self.maxReplicas",message="minReplicas must be less than or equal to maxReplicas (when maxReplicas is not 0)"
type ExporterSetSpec struct {
	// MinReplicas is the minimum number of instances (floor).
	// +kubebuilder:default=0
	// +kubebuilder:validation:Minimum=0
	MinReplicas int32 `json:"minReplicas,omitempty"`

	// MaxReplicas is the maximum number of instances (ceiling).
	// 0 means no upper bound.
	// +kubebuilder:default=4
	// +kubebuilder:validation:Minimum=0
	MaxReplicas int32 `json:"maxReplicas,omitempty"`

	// MinAvailableReplicas is the warm buffer: ready and unleased instances.
	// +kubebuilder:default=0
	// +kubebuilder:validation:Minimum=0
	MinAvailableReplicas int32 `json:"minAvailableReplicas,omitempty"`

	// ScaleDownCooldown is how long to wait before scaling down excess replicas.
	// +kubebuilder:default="5m"
	ScaleDownCooldown *metav1.Duration `json:"scaleDownCooldown,omitempty"`

	// RecycleStrategy defines what happens when a lease is released.
	// +kubebuilder:default=ExitAndReplace
	// +kubebuilder:validation:Enum=ExitAndReplace;InPlaceReuse
	RecycleStrategy RecycleStrategy `json:"recycleStrategy,omitempty"`

	// VirtualTargetClassName references a VirtualTargetClass in the same namespace.
	// +kubebuilder:validation:MinLength=1
	VirtualTargetClassName string `json:"virtualTargetClassName"`

	// Parameters are optional overrides deep-merged over the class parameters.
	// +optional
	// +kubebuilder:pruning:PreserveUnknownFields
	// +kubebuilder:validation:Schemaless
	Parameters *apiextensionsv1.JSON `json:"parameters,omitempty"`

	// Selector defines the label selector for matching exporters owned by this set.
	Selector metav1.LabelSelector `json:"selector"`

	// Template defines the exporter template for instances created by this set.
	Template ExporterSetTemplate `json:"template"`
}

// ExporterSetStatus defines the observed state of ExporterSet.
type ExporterSetStatus struct {
	// Replicas is the total number of exporter instances.
	Replicas int32 `json:"replicas"`

	// ReadyReplicas is the number of instances that are registered and ready.
	ReadyReplicas int32 `json:"readyReplicas"`

	// AvailableReplicas is the number of ready and unleased instances (warm pool).
	AvailableReplicas int32 `json:"availableReplicas"`

	// LeasedReplicas is the number of instances currently leased.
	LeasedReplicas int32 `json:"leasedReplicas"`

	// Conditions represent the latest available observations of the ExporterSet state.
	Conditions []metav1.Condition `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
}

// ExporterSetConditionType defines the condition types for ExporterSet.
type ExporterSetConditionType string

const (
	// ExporterSetConditionTypeHealthy indicates all desired replicas are ready.
	ExporterSetConditionTypeHealthy ExporterSetConditionType = "Healthy"
	// ExporterSetConditionTypeScalingLimited indicates scaling is constrained by limits.
	ExporterSetConditionTypeScalingLimited ExporterSetConditionType = "ScalingLimited"
)

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:subresource:scale:specpath=.spec.maxReplicas,statuspath=.status.replicas
// +kubebuilder:printcolumn:name="Replicas",type="integer",JSONPath=".status.replicas"
// +kubebuilder:printcolumn:name="Ready",type="integer",JSONPath=".status.readyReplicas"
// +kubebuilder:printcolumn:name="Available",type="integer",JSONPath=".status.availableReplicas"
// +kubebuilder:printcolumn:name="Leased",type="integer",JSONPath=".status.leasedReplicas"
// +kubebuilder:printcolumn:name="Class",type="string",JSONPath=".spec.virtualTargetClassName"

// ExporterSet is the Schema for the exportersets API.
// It manages a scalable set of virtual exporter instances backed by a VirtualTargetClass.
type ExporterSet struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   ExporterSetSpec   `json:"spec,omitempty"`
	Status ExporterSetStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// ExporterSetList contains a list of ExporterSet
type ExporterSetList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []ExporterSet `json:"items"`
}

func init() {
	SchemeBuilder.Register(&ExporterSet{}, &ExporterSetList{})
}
