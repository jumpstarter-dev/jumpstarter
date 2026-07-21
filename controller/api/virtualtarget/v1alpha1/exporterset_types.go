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
// +kubebuilder:validation:XValidation:rule="self.maxReplicas == 0 || self.minAvailableReplicas <= self.maxReplicas",message="minAvailableReplicas must be less than or equal to maxReplicas (when maxReplicas is not 0)"
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
	// Replicas is the total number of exporter instances owned by this set.
	Replicas int32 `json:"replicas"`

	// ReadyReplicas is the number of instances that are online and registered.
	ReadyReplicas int32 `json:"readyReplicas"`

	// AvailableReplicas is the number of ready, unleased, and enabled instances (warm pool).
	AvailableReplicas int32 `json:"availableReplicas"`

	// UnavailableReplicas is the number of instances that are not yet ready.
	UnavailableReplicas int32 `json:"unavailableReplicas"`

	// LeasedReplicas is the number of instances currently leased.
	LeasedReplicas int32 `json:"leasedReplicas"`

	// PodsPending is the number of owned pods in Pending phase.
	PodsPending int32 `json:"podsPending"`

	// PodsRunning is the number of owned pods in Running phase.
	PodsRunning int32 `json:"podsRunning"`

	// PodsFailed is the number of owned pods in Failed phase.
	PodsFailed int32 `json:"podsFailed"`

	// PodsUnknown is the number of owned pods in Unknown or Succeeded phase.
	PodsUnknown int32 `json:"podsUnknown"`

	// ExportersActive is the number of exporters currently serving a lease.
	ExportersActive int32 `json:"exportersActive"`

	// ExportersIdle is the number of exporters that are online, enabled, and not leased.
	ExportersIdle int32 `json:"exportersIdle"`

	// ExportersDisabled is the number of exporters with spec.enabled=false.
	ExportersDisabled int32 `json:"exportersDisabled"`

	// ExportersOffline is the number of enabled exporters that are not online and not leased.
	ExportersOffline int32 `json:"exportersOffline"`

	// Selector is the serialized label selector for HPA compatibility.
	Selector string `json:"selector,omitempty"`

	// Conditions represent the latest available observations of the ExporterSet state.
	Conditions []metav1.Condition `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
}

// ExporterSetConditionType defines the condition types for ExporterSet.
type ExporterSetConditionType string

const (
	// ExporterSetConditionAvailable indicates minimum replicas are ready and serving.
	ExporterSetConditionAvailable ExporterSetConditionType = "Available"
	// ExporterSetConditionProgressing indicates the set is scaling or updating.
	ExporterSetConditionProgressing ExporterSetConditionType = "Progressing"
	// ExporterSetConditionDegraded indicates some replicas are failing or offline.
	ExporterSetConditionDegraded ExporterSetConditionType = "Degraded"
	// ExporterSetConditionScalingLimited indicates scaling is constrained by limits.
	ExporterSetConditionScalingLimited ExporterSetConditionType = "ScalingLimited"
)

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:subresource:scale:specpath=.spec.maxReplicas,statuspath=.status.replicas,selectorpath=.status.selector
// +kubebuilder:printcolumn:name="Replicas",type="integer",JSONPath=".status.replicas"
// +kubebuilder:printcolumn:name="Ready",type="integer",JSONPath=".status.readyReplicas"
// +kubebuilder:printcolumn:name="Available",type="integer",JSONPath=".status.availableReplicas"
// +kubebuilder:printcolumn:name="Unavailable",type="integer",JSONPath=".status.unavailableReplicas"
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
