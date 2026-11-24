/*
Copyright 2026.

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

// LeaseTemplateSpec is a named definition of lease members and constraints
// that a lab administrator publishes. A client requests it by its name, and
// the client service copies it into the new lease.
// +kubebuilder:validation:XValidation:rule="self.members.all(m, !has(m.excluded) || !m.excluded)",message="a template member cannot set excluded"
// +kubebuilder:validation:XValidation:rule="self.members.exists(m, !has(m.optional) || !m.optional)",message="a template must have a required member"
// +kubebuilder:validation:XValidation:rule="!has(self.constraints) || self.constraints.all(c, c.members.all(n, self.members.exists(m, m.name == n)))",message="constraints must reference declared members"
// +kubebuilder:validation:XValidation:rule="!has(self.forwards) || (has(self.members) && self.forwards.all(f, f.endpoints.all(e, self.members.exists(m, m.name == e.member))))",message="forward endpoints must reference declared members"
// +kubebuilder:validation:XValidation:rule="!has(self.forwards) || !has(self.members) || self.forwards.all(f, !self.members.exists(m, m.name == f.name))",message="a forward and a member cannot have the same name"
type LeaseTemplateSpec struct {
	// Description is a short description for persons.
	// +kubebuilder:validation:MaxLength=1024
	Description string `json:"description,omitempty"`
	// Docs are documentation links. The controller does not read them.
	// +kubebuilder:validation:MaxItems=16
	// +kubebuilder:validation:items:MaxLength=2048
	Docs []string `json:"docs,omitempty"`
	// Image is a link to an image of the setup. The controller does not read it.
	// +kubebuilder:validation:MaxLength=2048
	Image string `json:"image,omitempty"`
	// Instructions are plain text for persons. Clients show them and never run them.
	// +kubebuilder:validation:MaxLength=8192
	Instructions string `json:"instructions,omitempty"`

	// Members are the members of a lease that uses this template.
	// +listType=map
	// +listMapKey=name
	// +kubebuilder:validation:MinItems=1
	// +kubebuilder:validation:MaxItems=8
	Members []LeaseMember `json:"members"`
	// Constraints are rules for sets of members.
	// +kubebuilder:validation:MaxItems=4
	Constraints []LeaseConstraint `json:"constraints,omitempty"`
	// Forwards are the forwards of a lease that uses this template. Template
	// expansion copies them without changes.
	// +listType=map
	// +listMapKey=name
	// +kubebuilder:validation:MaxItems=16
	Forwards []LeaseForward `json:"forwards,omitempty"`

	// Defaults are the values that the controller uses when a request does
	// not give them.
	Defaults LeaseTemplateDefaults `json:"defaults,omitempty"`
}

// LeaseTemplateDefaults are the request values that a template can default.
// Duration and SharedWith are the only ones.
type LeaseTemplateDefaults struct {
	// Duration of a lease whose request gives none.
	Duration *metav1.Duration `json:"duration,omitempty"`
	// SharedWith lists the clients that a lease is shared with when its
	// request gives no list. The same rules as LeaseSpec.SharedWith apply.
	// +listType=set
	// +kubebuilder:validation:MaxItems=10
	SharedWith []string `json:"sharedWith,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:printcolumn:JSONPath=".spec.description",name=Description,type=string

// LeaseTemplate is the Schema for the leasetemplates API. It gives a name to
// a set of lease members and constraints. It has no status.
type LeaseTemplate struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec LeaseTemplateSpec `json:"spec"`
}

// +kubebuilder:object:root=true

// LeaseTemplateList contains a list of LeaseTemplate
type LeaseTemplateList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []LeaseTemplate `json:"items"`
}

func init() {
	SchemeBuilder.Register(&LeaseTemplate{}, &LeaseTemplateList{})
}
