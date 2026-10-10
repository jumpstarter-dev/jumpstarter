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
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// ExporterSpec defines the desired state of Exporter.
type ExporterSpec struct {
	// Username is the identity of the exporter, used for authentication and authorization.
	Username *string `json:"username,omitempty"`
	// Enabled controls whether this exporter is eligible for lease assignment.
	// When set to false, the controller will not assign new leases to this exporter.
	// Useful for temporarily taking an exporter offline for maintenance without deleting it,
	// or for graceful scale-down of virtual exporter pools.
	// +kubebuilder:default=true
	Enabled *bool `json:"enabled,omitempty"`
}

// ExporterStatus defines the observed state of Exporter.
type ExporterStatus struct {
	// Conditions represent the latest available observations of the exporter state.
	Conditions []metav1.Condition `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
	// Credential is a reference to the secret containing the exporter credentials.
	Credential *corev1.LocalObjectReference `json:"credential,omitempty"`
	// Devices is the list of driver instances currently reported by the exporter.
	Devices []Device `json:"devices,omitempty"`
	// LeaseRef is a reference to the lease currently assigned to this exporter.
	LeaseRef *corev1.LocalObjectReference `json:"leaseRef,omitempty"`
	// LeaseHooks records how far the exporter got with the lifecycle hooks of
	// its latest lease. It survives exporter restarts, so a restarted exporter
	// neither repeats a finished beforeLease hook nor skips an owed afterLease hook.
	// It is dropped when an exporter that does not record its lease hooks registers.
	LeaseHooks *ExporterLeaseHooks `json:"leaseHooks,omitempty"`
	// RecordsLeaseHooks is set when the exporter process that last registered
	// records its lease hooks. Such an exporter is not assigned a new lease while
	// its record owes an afterLease hook, whatever status it reports.
	// +optional
	RecordsLeaseHooks bool `json:"recordsLeaseHooks,omitempty"`
	// LastSeen is the timestamp of the last communication from the exporter.
	LastSeen metav1.Time `json:"lastSeen,omitempty"`
	// Endpoint is the gRPC endpoint URL where the exporter is reachable.
	Endpoint string `json:"endpoint,omitempty"`
	// ExporterStatusValue is the current operational status reported by the exporter
	// +kubebuilder:validation:Enum=Unspecified;Offline;Available;BeforeLeaseHook;LeaseReady;AfterLeaseHook;BeforeLeaseHookFailed;AfterLeaseHookFailed
	ExporterStatusValue string `json:"exporterStatus,omitempty"`
	// StatusMessage is an optional human-readable message describing the current state
	StatusMessage string `json:"statusMessage,omitempty"`
}

// ExporterLeaseHooks is the lifecycle hook record of an exporter's latest lease.
type ExporterLeaseHooks struct {
	// LeaseRef is the lease the hooks belong to.
	LeaseRef corev1.LocalObjectReference `json:"leaseRef"`
	// LeaseUID is the UID of that lease. A lease name can be reused once the
	// lease is deleted, so the name alone does not identify it.
	LeaseUID types.UID `json:"leaseUID"`
	// ClientName is the client that held the lease. An afterLease hook that runs
	// after the lease has ended (for example after an exporter restart) gets it.
	// +optional
	ClientName string `json:"clientName,omitempty"`
	// BeforeLease is the beforeLease hook's state; unset until it starts.
	BeforeLease *LeaseHookStatus `json:"beforeLease,omitempty"`
	// AfterLease is the afterLease hook's state; unset until it starts.
	AfterLease *LeaseHookStatus `json:"afterLease,omitempty"`
}

// LeaseHookStatus is the state of one lease lifecycle hook.
type LeaseHookStatus struct {
	// Phase of the hook. Running also covers a hook cut off by an exporter restart.
	// +kubebuilder:validation:Enum=Running;Succeeded;Failed;Skipped
	Phase LeaseHookPhase `json:"phase"`
	// OnFailure is the hook's configured failure action when it ran.
	// +kubebuilder:validation:Enum=warn;endLease;exit
	// +optional
	OnFailure string `json:"onFailure,omitempty"`
	// Message is the failure or skip reason, if any.
	// +optional
	Message string `json:"message,omitempty"`
	// Attempts counts the times the hook was started; more than one means it
	// was re-run after an exporter restart cut it off.
	// +optional
	Attempts int32 `json:"attempts,omitempty"`
	// LastTransitionTime is when the hook entered its current phase.
	LastTransitionTime metav1.Time `json:"lastTransitionTime"`
}

type LeaseHookPhase string

const (
	LeaseHookPhaseRunning   LeaseHookPhase = "Running"
	LeaseHookPhaseSucceeded LeaseHookPhase = "Succeeded"
	LeaseHookPhaseFailed    LeaseHookPhase = "Failed"
	LeaseHookPhaseSkipped   LeaseHookPhase = "Skipped"
)

// Finished reports whether a hook in this phase has run its course.
func (p LeaseHookPhase) Finished() bool {
	return p == LeaseHookPhaseSucceeded || p == LeaseHookPhaseFailed || p == LeaseHookPhaseSkipped
}

// IsFor reports whether the record belongs to the lease with this name and UID.
func (h *ExporterLeaseHooks) IsFor(name string, uid types.UID) bool {
	return h != nil && h.LeaseRef.Name == name && h.LeaseUID == uid
}

// OwesAfterLease reports whether the record's lease still needs its afterLease
// hook: setup started, and cleanup has not finished.
func (h *ExporterLeaseHooks) OwesAfterLease() bool {
	return h != nil && h.BeforeLease != nil && (h.AfterLease == nil || !h.AfterLease.Phase.Finished())
}

type ExporterConditionType string

const (
	ExporterConditionTypeRegistered ExporterConditionType = "Registered"
	ExporterConditionTypeOnline     ExporterConditionType = "Online"
)

// ExporterStatus values - PascalCase for Kubernetes, converted from proto ALL_CAPS
const (
	ExporterStatusUnspecified           = "Unspecified"
	ExporterStatusOffline               = "Offline"
	ExporterStatusAvailable             = "Available"
	ExporterStatusBeforeLeaseHook       = "BeforeLeaseHook"
	ExporterStatusLeaseReady            = "LeaseReady"
	ExporterStatusAfterLeaseHook        = "AfterLeaseHook"
	ExporterStatusBeforeLeaseHookFailed = "BeforeLeaseHookFailed"
	ExporterStatusAfterLeaseHookFailed  = "AfterLeaseHookFailed"
)

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Enabled",type="boolean",JSONPath=".spec.enabled"
// +kubebuilder:printcolumn:name="Status",type="string",JSONPath=".status.exporterStatus"
// +kubebuilder:printcolumn:name="Message",type="string",JSONPath=".status.statusMessage",priority=1

// Exporter is the Schema for the exporters API
type Exporter struct {
	// Exporters represent the services that connect to the physical or virtual
	// devices. They are responsible for providing the access to the devices and
	// for the communication with the devices. A jumpstarter exporter service
	// should be run on a linux machine, or a pod, with the exporter credentials
	// and the right configuration for this resource to become online. For
	// more information see the Jumpstarter documentation:
	// https://jumpstarter.dev/main/introduction/exporters.html#exporters
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   ExporterSpec   `json:"spec,omitempty"`
	Status ExporterStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// ExporterList contains a list of Exporter
type ExporterList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Exporter `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Exporter{}, &ExporterList{})
}
