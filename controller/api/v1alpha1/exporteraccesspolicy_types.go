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

// EDIT THIS FILE!  THIS IS SCAFFOLDING FOR YOU TO OWN!
// NOTE: json tags are required.  Any new fields you add must have json tags for the fields to be serialized.

type From struct {
	ClientSelector metav1.LabelSelector `json:"clientSelector,omitempty"`
}

type Policy struct {
	Priority        int              `json:"priority,omitempty"`
	From            []From           `json:"from,omitempty"`
	MaximumDuration *metav1.Duration `json:"maximumDuration,omitempty"`
	SpotAccess      bool             `json:"spotAccess,omitempty"`
}

// ExporterAccessPolicySpec defines the desired state of ExporterAccessPolicy.
type ExporterAccessPolicySpec struct {
	ExporterSelector metav1.LabelSelector `json:"exporterSelector,omitempty"`
	Policies         []Policy             `json:"policies,omitempty"`
}

// ExporterAccessPolicyStatus defines the observed state of ExporterAccessPolicy.
type ExporterAccessPolicyStatus struct {
	// Status field for the exporter access policies
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status

// ExporterAccessPolicy is the Schema for the exporteraccesspolicies API.
type ExporterAccessPolicy struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   ExporterAccessPolicySpec   `json:"spec,omitempty"`
	Status ExporterAccessPolicyStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// ExporterAccessPolicyList contains a list of ExporterAccessPolicy.
type ExporterAccessPolicyList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []ExporterAccessPolicy `json:"items"`
}

func init() {
	SchemeBuilder.Register(&ExporterAccessPolicy{}, &ExporterAccessPolicyList{})
}
