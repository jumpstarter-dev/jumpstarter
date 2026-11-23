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
	"crypto/sha256"
	"encoding/hex"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// ExporterClaimSpec says that one lease holds one exporter or one exclusion
// group. The object name comes from the exporter name or the group name, so
// the API server lets only one claim for it exist.
// +kubebuilder:validation:XValidation:rule="self == oldSelf",message="spec is immutable"
// +kubebuilder:validation:XValidation:rule="has(self.exporter) != has(self.exclusionGroup)",message="exactly one of exporter or exclusionGroup is required"
type ExporterClaimSpec struct {
	// Exporter is the name of the exporter that the lease holds.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=253
	Exporter string `json:"exporter,omitempty"`
	// ExclusionGroup is the exclusion group that the lease holds.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=63
	ExclusionGroup string `json:"exclusionGroup,omitempty"`
	// LeaseRef is the lease that holds the claim.
	LeaseRef ClaimLeaseReference `json:"leaseRef"`
}

// ClaimLeaseReference identifies the lease that holds a claim and the bind
// attempt that created the claim.
type ClaimLeaseReference struct {
	// Name of the lease.
	// +kubebuilder:validation:MaxLength=253
	Name string `json:"name"`
	// UID of the lease. A lease name can be used again after the lease is deleted.
	UID types.UID `json:"uid"`
	// Attempt is status.bindAttempt of the lease when it created the claim.
	Attempt int64 `json:"attempt"`
}

// +kubebuilder:object:root=true
// +kubebuilder:printcolumn:JSONPath=".spec.exporter",name=Exporter,type=string
// +kubebuilder:printcolumn:JSONPath=".spec.exclusionGroup",name=Group,type=string
// +kubebuilder:printcolumn:JSONPath=".spec.leaseRef.name",name=Lease,type=string
// +kubebuilder:printcolumn:JSONPath=".spec.leaseRef.attempt",name=Attempt,type=integer,priority=1

// ExporterClaim is the Schema for the exporterclaims API. A claim shows that
// one lease holds one exporter or one exclusion group. Only the controller
// writes claims.
type ExporterClaim struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec ExporterClaimSpec `json:"spec"`
}

// +kubebuilder:object:root=true

// ExporterClaimList contains a list of ExporterClaim
type ExporterClaimList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []ExporterClaim `json:"items"`
}

const (
	exporterClaimPrefix = "exporter-"
	groupClaimPrefix    = "group-"
)

func claimNameHash(key string) string {
	sum := sha256.Sum256([]byte(key))
	return hex.EncodeToString(sum[:])[:32]
}

// ExporterClaimName is the name of the claim for an exporter: "exporter-" and
// the first 32 hex digits of the SHA-256 of the exporter name.
func ExporterClaimName(exporterName string) string {
	return exporterClaimPrefix + claimNameHash(exporterName)
}

// ExclusionGroupClaimName is the name of the claim for an exclusion group:
// "group-" and the first 32 hex digits of the SHA-256 of the group name.
func ExclusionGroupClaimName(group string) string {
	return groupClaimPrefix + claimNameHash(group)
}

func init() {
	SchemeBuilder.Register(&ExporterClaim{}, &ExporterClaimList{})
}
