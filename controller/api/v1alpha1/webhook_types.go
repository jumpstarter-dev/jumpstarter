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
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// WebhookSpec defines the desired state of an outbound Webhook subscription.
type WebhookSpec struct {
	// URL is the destination the controller POSTs signed event payloads to.
	// +kubebuilder:validation:MinLength=1
	URL string `json:"url"`

	// SecretRef references a Secret in the same namespace whose value is used
	// as the HMAC-SHA256 signing key.
	SecretRef corev1.SecretKeySelector `json:"secretRef"`

	// Events selects which event classes are delivered. Valid values match
	// the EventClass enum in jumpstarter.admin.v1.WebhookService:
	// LeaseCreated, LeaseEnded, ExporterOffline, ExporterAvailable,
	// ClientCreated, ClientDeleted.
	// +kubebuilder:validation:MinItems=1
	Events []string `json:"events"`
}

// WebhookStatus tracks delivery liveness mirrored from the dispatcher.
type WebhookStatus struct {
	LastSuccess         *metav1.Time       `json:"lastSuccess,omitempty"`
	LastFailure         *metav1.Time       `json:"lastFailure,omitempty"`
	ConsecutiveFailures int32              `json:"consecutiveFailures,omitempty"`
	Conditions          []metav1.Condition `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="URL",type="string",JSONPath=".spec.url"
// +kubebuilder:printcolumn:name="LastSuccess",type="date",JSONPath=".status.lastSuccess"
// +kubebuilder:printcolumn:name="ConsecutiveFailures",type="integer",JSONPath=".status.consecutiveFailures"

// Webhook subscribes an external HTTP endpoint to controller events.
//
// Delivery is at-least-once with HMAC-SHA256 signatures in the Stripe
// format (X-Jumpstarter-Signature: t=<unix>,v1=<hmac> over <unix>.<body>).
// Retries use exponential backoff up to 1h, capped at 24 attempts before
// the dispatcher pauses delivery for the resource.
type Webhook struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   WebhookSpec   `json:"spec,omitempty"`
	Status WebhookStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// WebhookList contains a list of Webhook
type WebhookList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Webhook `json:"items"`
}

// Event class names used in WebhookSpec.Events.
const (
	WebhookEventLeaseCreated      = "LeaseCreated"
	WebhookEventLeaseEnded        = "LeaseEnded"
	WebhookEventExporterOffline   = "ExporterOffline"
	WebhookEventExporterAvailable = "ExporterAvailable"
	WebhookEventClientCreated     = "ClientCreated"
	WebhookEventClientDeleted     = "ClientDeleted"
)

func init() {
	SchemeBuilder.Register(&Webhook{}, &WebhookList{})
}
