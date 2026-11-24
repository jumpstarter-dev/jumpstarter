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
)

// +kubebuilder:validation:XValidation:rule="((has(self.selector.matchLabels) && size(self.selector.matchLabels) > 0) || (has(self.selector.matchExpressions) && size(self.selector.matchExpressions) > 0)) || (has(self.exporterRef) && has(self.exporterRef.name) && size(self.exporterRef.name) > 0) || (has(self.members) && size(self.members) > 0)",message="one of selector or exporterRef.name is required, or members for a lease of more than one exporter"
// +kubebuilder:validation:XValidation:rule="!(has(self.members) && size(self.members) > 0) || !(((has(self.selector.matchLabels) && size(self.selector.matchLabels) > 0) || (has(self.selector.matchExpressions) && size(self.selector.matchExpressions) > 0)) || (has(self.exporterRef) && has(self.exporterRef.name) && size(self.exporterRef.name) > 0))",message="members cannot be used with selector or exporterRef"
// +kubebuilder:validation:XValidation:rule="!has(self.members) || size(self.members) == 0 || self.members.exists(m, !has(m.optional) || !m.optional)",message="a lease with members must have a required member"
// +kubebuilder:validation:XValidation:rule="!has(self.constraints) || (has(self.members) && self.constraints.all(c, c.members.all(n, self.members.exists(m, m.name == n))))",message="constraints must reference declared members"
// +kubebuilder:validation:XValidation:rule="has(self.members) == has(oldSelf.members) && (!has(self.members) || self.members == oldSelf.members)",message="members are immutable after creation"
// +kubebuilder:validation:XValidation:rule="has(self.constraints) == has(oldSelf.constraints) && (!has(self.constraints) || self.constraints == oldSelf.constraints)",message="constraints are immutable after creation"
// +kubebuilder:validation:XValidation:rule="!has(self.forwards) || (has(self.members) && self.forwards.all(f, f.endpoints.all(e, self.members.exists(m, m.name == e.member))))",message="forward endpoints must reference declared members"
// +kubebuilder:validation:XValidation:rule="!has(self.forwards) || !has(self.members) || self.forwards.all(f, !self.members.exists(m, m.name == f.name))",message="a forward and a member cannot have the same name"
// +kubebuilder:validation:XValidation:rule="has(self.forwards) == has(oldSelf.forwards) && (!has(self.forwards) || self.forwards == oldSelf.forwards)",message="forwards are immutable after creation"
// +kubebuilder:validation:XValidation:rule="has(self.templateRef) == has(oldSelf.templateRef) && (!has(self.templateRef) || self.templateRef == oldSelf.templateRef)",message="templateRef is immutable after creation"
// +kubebuilder:validation:XValidation:rule="!has(oldSelf.tags) || self.tags == oldSelf.tags",message="tags are immutable after creation"
// +kubebuilder:validation:XValidation:rule="!has(oldSelf.context) || self.context == oldSelf.context",message="context is immutable after creation"
// +kubebuilder:validation:XValidation:rule="!has(self.context) || self.context.all(k, size(k) <= 32 && size(self.context[k]) <= 64)",message="context keys max 32 chars, values max 64 chars"
// LeaseSpec defines the desired state of Lease
type LeaseSpec struct {
	// The client that is requesting the lease
	ClientRef corev1.LocalObjectReference `json:"clientRef"`
	// Duration of the lease. Must be positive when provided.
	// Can be omitted (nil) when both BeginTime and EndTime are provided,
	// in which case it's calculated as EndTime - BeginTime.
	Duration *metav1.Duration `json:"duration,omitempty"`
	// The selector for the exporter to be used
	// +kubebuilder:default:={}
	Selector metav1.LabelSelector `json:"selector"`
	// Optionally pin this lease to a specific exporter name.
	ExporterRef *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
	// User-defined tags for the lease. Immutable after creation.
	// Maximum 10 entries. Keys must be simple names (no slashes) conforming to Kubernetes label rules.
	// +kubebuilder:validation:MaxProperties=10
	Tags map[string]string `json:"tags,omitempty"`
	// AllowDisabled permits leasing a disabled exporter when used with ExporterRef.
	// When true and ExporterRef is set, the controller will allow leasing an exporter
	// even if its spec.enabled field is false. This is useful for investigating
	// broken exporters that have been administratively disabled.
	// Ignored for selector-based leases (disabled exporters are always filtered out).
	AllowDisabled bool `json:"allowDisabled,omitempty"`
	// The release flag requests the controller to end the lease now
	Release bool `json:"release,omitempty"`
	// Requested start time. If omitted, lease starts when exporter is acquired.
	// Immutable after lease starts (cannot change the past).
	BeginTime *metav1.Time `json:"beginTime,omitempty"`
	// Requested end time. If specified with BeginTime, Duration is calculated.
	// Can be updated to extend or shorten active leases.
	EndTime *metav1.Time `json:"endTime,omitempty"`
	// User-defined context metadata for the lease (e.g. build_id, image_digest, VCS ref).
	// Immutable after creation. Maximum 8 entries; keys max 32 chars, values max 64 chars.
	// +kubebuilder:validation:MaxProperties=8
	Context map[string]string `json:"context,omitempty"`
	// List of client names that have shared access to this lease.
	// Only the lease owner can modify this list.
	// +listType=set
	// +kubebuilder:validation:MaxItems=10
	SharedWith []string `json:"sharedWith,omitempty"`
	// Members makes this a member-form lease: each member has a role name and
	// selects one exporter. The controller binds all the required members or
	// none. Members cannot be used with the top-level selector or exporterRef.
	// Immutable after creation.
	// +listType=map
	// +listMapKey=name
	// +kubebuilder:validation:MaxItems=8
	Members []LeaseMember `json:"members,omitempty"`
	// Constraints are rules for sets of members. Immutable after creation.
	// +kubebuilder:validation:MaxItems=4
	Constraints []LeaseConstraint `json:"constraints,omitempty"`
	// TemplateRef records the LeaseTemplate that this lease was expanded from.
	// The client service sets it; the controller does not read it at bind time.
	TemplateRef *LeaseTemplateReference `json:"templateRef,omitempty"`
	// Forwards connect ports on different members of a member-form lease
	// through the router. Each endpoint refers to a member of the lease, and
	// no forward has the name of a member. Immutable after creation.
	// +listType=map
	// +listMapKey=name
	// +kubebuilder:validation:MaxItems=16
	Forwards []LeaseForward `json:"forwards,omitempty"`
}

// LeaseForward is one forward of a member-form lease: a named set of
// endpoints. It connects one provides port to one or more requires ports, or
// two connects ports. The controller gets the direction of each port from the
// exporter reports.
type LeaseForward struct {
	// Name of the forward. It is a DNS label, as a role name is.
	// +kubebuilder:validation:MaxLength=63
	// +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
	Name string `json:"name"`
	// Endpoints are the ends of the forward, in any sequence. The list key
	// is the member, thus each endpoint is on a different member.
	// +listType=map
	// +listMapKey=member
	// +kubebuilder:validation:MinItems=2
	// +kubebuilder:validation:MaxItems=8
	Endpoints []ForwardEndpoint `json:"endpoints"`
}

// ForwardEndpoint is one end of a forward: a member of the lease and a port
// that the exporter of that member reports.
type ForwardEndpoint struct {
	// Member is the role name of the member.
	// +kubebuilder:validation:MaxLength=63
	// +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
	Member string `json:"member"`
	// Port is the name of the port on the exporter of the member.
	// +kubebuilder:validation:MaxLength=63
	// +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
	Port string `json:"port"`
}

// LeaseForwardState is the state of one forward of a lease. A forward has one
// state for all its endpoints.
// +kubebuilder:validation:Enum=Pending;Disabled;Listening;Connected;Failed
type LeaseForwardState string

const (
	// LeaseForwardStatePending: the controller did not complete the setup.
	LeaseForwardStatePending LeaseForwardState = "Pending"
	// LeaseForwardStateDisabled: an optional member is Omitted, and the
	// forward cannot operate without it.
	LeaseForwardStateDisabled LeaseForwardState = "Disabled"
	// LeaseForwardStateListening: the setup is complete. No connection is open.
	LeaseForwardStateListening LeaseForwardState = "Listening"
	// LeaseForwardStateConnected: a minimum of one connection is open.
	LeaseForwardStateConnected LeaseForwardState = "Connected"
	// LeaseForwardStateFailed: the validation or the setup failed, or a
	// necessary member is Lost.
	LeaseForwardStateFailed LeaseForwardState = "Failed"
)

// Reasons of the lease conditions that forwards add.
const (
	// LeaseReasonForwardsReady: each forward that is not Disabled is
	// Listening or Connected (ForwardsReady is true).
	LeaseReasonForwardsReady = "ForwardsReady"
	// LeaseReasonForwardsPending: a minimum of one forward is Pending, and
	// none is Failed (ForwardsReady and Ready are false).
	LeaseReasonForwardsPending = "ForwardsPending"
	// LeaseReasonForwardFailed: a minimum of one forward is Failed
	// (ForwardsReady is false, and the lease is Degraded).
	LeaseReasonForwardFailed = "Failed"
)

// LeaseForwardStatus is the observed state of one forward of a lease.
type LeaseForwardStatus struct {
	// Name of the forward.
	Name string `json:"name"`
	// State of the forward.
	State LeaseForwardState `json:"state"`
	// Message is a human-readable message for the state: the role of the
	// Omitted member of a Disabled forward, or the cause of a failure.
	Message string `json:"message,omitempty"`
	// Ends is the state of each bound end of the forward, in the order of
	// the endpoints. The key is the member. The controller service writes
	// the reports and the setup times; the lease controller calculates State
	// from them.
	// +listType=map
	// +listMapKey=member
	// +kubebuilder:validation:MaxItems=8
	Ends []LeaseForwardEndStatus `json:"ends,omitempty"`
}

// LeaseForwardEndState is the state of one end of a forward: the last state
// that the exporter of the endpoint reported.
// +kubebuilder:validation:Enum=Pending;Ready;Connected;Failed
type LeaseForwardEndState string

const (
	// LeaseForwardEndStatePending: the exporter of the end sent no report.
	LeaseForwardEndStatePending LeaseForwardEndState = "Pending"
	// LeaseForwardEndStateReady: the listener is open, or the forward is
	// recorded.
	LeaseForwardEndStateReady LeaseForwardEndState = "Ready"
	// LeaseForwardEndStateConnected: a minimum of one connection is open.
	LeaseForwardEndStateConnected LeaseForwardEndState = "Connected"
	// LeaseForwardEndStateFailed: the setup failed.
	LeaseForwardEndStateFailed LeaseForwardEndState = "Failed"
)

// LeaseForwardEndStatus is the observed state of one end of a forward: the
// last report that the controller accepted from the exporter of the member,
// and the time of the last setup instruction.
type LeaseForwardEndStatus struct {
	// Member is the role name of the member of this end.
	Member string `json:"member"`
	// State is the state of the last accepted report. It is Pending until
	// the exporter sends a report.
	State LeaseForwardEndState `json:"state"`
	// Message is the cause that a Failed report gave.
	Message string `json:"message,omitempty"`
	// Sequence is the sequence number of the last report that the controller
	// accepted. A report with a number that is not larger is ignored.
	// +kubebuilder:validation:Minimum=0
	Sequence uint64 `json:"sequence,omitempty"`
	// SetupTime is the time of the last setup instruction for this end.
	SetupTime *metav1.Time `json:"setupTime,omitempty"`
	// ReportTime is the time of the last report that the controller accepted.
	ReportTime *metav1.Time `json:"reportTime,omitempty"`
}

// LeaseMember is one member of a member-form lease: a role name and the
// selection of one exporter.
// +kubebuilder:validation:XValidation:rule="!has(self.excluded) || !self.excluded || (has(self.optional) && self.optional)",message="only an optional member can be excluded"
// +kubebuilder:validation:XValidation:rule="(((has(self.selector) && ((has(self.selector.matchLabels) && size(self.selector.matchLabels) > 0) || (has(self.selector.matchExpressions) && size(self.selector.matchExpressions) > 0))) ? 1 : 0) + ((has(self.exporterRef) && has(self.exporterRef.name) && size(self.exporterRef.name) > 0) ? 1 : 0)) == 1",message="exactly one of selector or exporterRef.name is required"
type LeaseMember struct {
	// Name is the role name of the member, for example "phone". It is a DNS label.
	// +kubebuilder:validation:MaxLength=63
	// +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
	Name string `json:"name"`
	// Selector selects the exporter of the member by its labels.
	Selector *metav1.LabelSelector `json:"selector,omitempty"`
	// ExporterRef pins the member to a specific exporter name.
	ExporterRef *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
	// Optional lets the lease become ready without this member.
	Optional bool `json:"optional,omitempty"`
	// Excluded tells the controller not to bind this optional member. Only
	// template expansion sets it, for a request that leaves the role out.
	Excluded bool `json:"excluded,omitempty"`
	// AllowDisabled permits a disabled exporter when used with ExporterRef.
	AllowDisabled bool `json:"allowDisabled,omitempty"`
}

// LeaseConstraint is a rule for a set of members of a lease.
type LeaseConstraint struct {
	// Members are the role names that the rule applies to.
	// +listType=set
	// +kubebuilder:validation:MinItems=2
	// +kubebuilder:validation:MaxItems=8
	// +kubebuilder:validation:items:MaxLength=63
	Members []string `json:"members"`
	// SameLabel is a label key, for example "bus" or "rf-domain". The exporters
	// that the members bind must have the same value of this label. The
	// controller selects the value at bind time.
	// +kubebuilder:validation:MinLength=1
	// +kubebuilder:validation:MaxLength=317
	SameLabel string `json:"sameLabel"`
}

// LeaseTemplateReference records the template a lease was expanded from.
type LeaseTemplateReference struct {
	// Name of the LeaseTemplate.
	// +kubebuilder:validation:MaxLength=253
	Name string `json:"name"`
	// Generation is the metadata.generation of the template at expansion.
	Generation int64 `json:"generation"`
}

// LeaseMemberState is the state of one member of a member-form lease.
// +kubebuilder:validation:Enum=Pending;Bound;Omitted;Lost
type LeaseMemberState string

const (
	// LeaseMemberStatePending: the member has no exporter at this time.
	LeaseMemberStatePending LeaseMemberState = "Pending"
	// LeaseMemberStateBound: the member has an exporter.
	LeaseMemberStateBound LeaseMemberState = "Bound"
	// LeaseMemberStateOmitted: an optional member has no exporter.
	LeaseMemberStateOmitted LeaseMemberState = "Omitted"
	// LeaseMemberStateLost: a bound member lost its exporter. This state is final.
	LeaseMemberStateLost LeaseMemberState = "Lost"
)

// Reasons of member states and of the lease conditions that member-form
// leases add.
const (
	// LeaseMemberReasonExcluded: the request left the optional member out.
	LeaseMemberReasonExcluded = "Excluded"
	// LeaseMemberReasonOffline: the exporter of the member went offline.
	LeaseMemberReasonOffline = "Offline"
	// LeaseMemberReasonExporterDeleted: an administrator deleted the exporter.
	LeaseMemberReasonExporterDeleted = "ExporterDeleted"
	// LeaseMemberReasonExporterReleased: the exporter released the lease.
	LeaseMemberReasonExporterReleased = "ExporterReleased"

	// LeaseReasonInsufficientExporters: the members need more different
	// exporters than there are.
	LeaseReasonInsufficientExporters = "InsufficientExporters"
	// LeaseReasonConstraintUnsatisfiable: an assignment exists without the
	// constraints but not with them.
	LeaseReasonConstraintUnsatisfiable = "ConstraintUnsatisfiable"
	// LeaseReasonConstraintLimit: the constraint search was stopped at its limit.
	LeaseReasonConstraintLimit = "ConstraintLimit"
	// LeaseReasonBeforeLeaseHook: the beforeLease hook of a member still runs.
	LeaseReasonBeforeLeaseHook = "BeforeLeaseHook"
	// LeaseReasonBeforeLeaseHookFailed: the beforeLease hook of a member failed.
	LeaseReasonBeforeLeaseHookFailed = "BeforeLeaseHookFailed"
	// LeaseReasonMemberLost: a bound member lost its exporter.
	LeaseReasonMemberLost = "MemberLost"
	// LeaseReasonExporterConflict: an older lease holds the exporter of this
	// lease (found when claims are created for leases bound before an upgrade).
	LeaseReasonExporterConflict = "ExporterConflict"
)

// LeaseMemberStatus is the observed state of one member of a lease.
type LeaseMemberStatus struct {
	// Name is the role name of the member.
	Name string `json:"name"`
	// State of the member.
	State LeaseMemberState `json:"state"`
	// Reason is a machine-readable reason for the state.
	Reason string `json:"reason,omitempty"`
	// Message is a human-readable message for the state.
	Message string `json:"message,omitempty"`
	// ExporterRef is the exporter of the member. A Lost member keeps it.
	ExporterRef *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
	// Priority is the priority of the member from the access policy.
	Priority int `json:"priority,omitempty"`
	// SpotAccess indicates whether the member was granted with spot access.
	SpotAccess bool `json:"spotAccess,omitempty"`
}

// LeaseStatus defines the observed state of Lease.
type LeaseStatus struct {
	// BeginTime is the actual start time of the lease.
	BeginTime *metav1.Time `json:"beginTime,omitempty"`
	// EndTime is the actual end time of the lease.
	EndTime *metav1.Time `json:"endTime,omitempty"`
	// ExporterRef is a reference to the exporter assigned to this lease.
	// It stays nil for a member-form lease: see Members.
	ExporterRef *corev1.LocalObjectReference `json:"exporterRef,omitempty"`
	// Members is the state of each member of a member-form lease, in spec order.
	// +listType=map
	// +listMapKey=name
	Members []LeaseMemberStatus `json:"members,omitempty"`
	// ExclusionGroups are the exclusion groups that this lease holds.
	// +listType=set
	ExclusionGroups []string `json:"exclusionGroups,omitempty"`
	// BindAttempt counts the bind attempts of this lease. Each ExporterClaim
	// of the lease records the attempt that created it.
	BindAttempt int64 `json:"bindAttempt,omitempty"`
	// Forwards is the state of each forward of the lease, in spec order.
	// +listType=map
	// +listMapKey=name
	Forwards []LeaseForwardStatus `json:"forwards,omitempty"`
	// Ended indicates whether the lease has been terminated.
	Ended bool `json:"ended"`
	// Priority is the effective priority of the lease from the access policy.
	Priority int `json:"priority,omitempty"`
	// SpotAccess indicates whether this lease was granted with spot (preemptible) access.
	SpotAccess bool `json:"spotAccess,omitempty"`
	// SharedWith is the effective set of client names granted shared access to
	// this lease. It is derived by the controller from Spec.SharedWith after
	// evaluating exporter access policies; Spec.SharedWith remains the owner's
	// desired intent and is never mutated by the controller.
	SharedWith []string `json:"sharedWith,omitempty"`
	// Conditions represent the latest available observations of the lease state.
	Conditions []metav1.Condition `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
}

type LeaseConditionType string

const (
	LeaseConditionTypePending       LeaseConditionType = "Pending"
	LeaseConditionTypeReady         LeaseConditionType = "Ready"
	LeaseConditionTypeUnsatisfiable LeaseConditionType = "Unsatisfiable"
	LeaseConditionTypeInvalid       LeaseConditionType = "Invalid"
	// LeaseConditionTypeDegraded is true when a bound member of a member-form
	// lease lost its exporter. The lease keeps its other members.
	LeaseConditionTypeDegraded LeaseConditionType = "Degraded"
	// LeaseConditionTypeForwardsReady is true when each forward of the lease
	// that is not Disabled is Listening or Connected. Only a lease with
	// forwards has it.
	LeaseConditionTypeForwardsReady LeaseConditionType = "ForwardsReady"
)

type LeaseLabel string

const (
	LeaseLabelEnded        LeaseLabel = "jumpstarter.dev/lease-ended"
	LeaseLabelEndedValue   string     = "true"
	LeaseTagMetadataPrefix string     = "metadata.jumpstarter.dev/"
)

// MaxSharedWithEntries is the maximum number of clients a lease may be shared
// with.
const MaxSharedWithEntries = 10

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:JSONPath=".status.ended",name=Ended,type=boolean
// +kubebuilder:printcolumn:JSONPath=".spec.clientRef.name",name=Client,type=string
// +kubebuilder:printcolumn:JSONPath=".status.exporterRef.name",name=Exporter,type=string

// Lease is the Schema for the leases API
type Lease struct {
	// Lease is the schema for the Leases API. Leases represent a
	// request for a specific exporter by a client. The lease is
	// acquired by the client and the exporter is assigned to the lease.
	// The lease is released by the client when the client is done with
	// the exporter. For more information see the Jumpstarter documentation:
	//
	// https://jumpstarter.dev/main/reference/man-pages/jmp.html#jmp-create-lease
	// https://jumpstarter.dev/main/reference/man-pages/jmp.html#jmp-shell
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   LeaseSpec   `json:"spec,omitempty"`
	Status LeaseStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// LeaseList contains a list of Lease
type LeaseList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Lease `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Lease{}, &LeaseList{})
}
