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

// ClientSpec defines the desired state of Client.
type ClientSpec struct {
	// Username is the identity of the client, used for authentication and authorization.
	Username *string `json:"username,omitempty"`
}

// ClientConditionType defines the condition types for Client resources.
type ClientConditionType string

const (
	ClientConditionTypeTokenExpiring ClientConditionType = "TokenExpiring"
)

// ClientStatus defines the observed state of Client.
type ClientStatus struct {
	// Conditions represent the latest available observations of the client state.
	Conditions []metav1.Condition `json:"conditions,omitempty" patchStrategy:"merge" patchMergeKey:"type"`
	// Credential is a reference to the secret containing the client credentials.
	Credential *corev1.LocalObjectReference `json:"credential,omitempty"`
	// Endpoint is the controller gRPC endpoint URL assigned to this client.
	Endpoint string `json:"endpoint,omitempty"`
	// TokenExpiresAt is the expiry time of the internal credential token.
	TokenExpiresAt *metav1.Time `json:"tokenExpiresAt,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Token Expires",type="string",JSONPath=".status.tokenExpiresAt"

// Client is the Schema for the clients API
type Client struct {
	// The Client in the Jumpstarter controller represents a user that can access the Jumpstarter Controller.
	// Clients can be associated to external identity OIDC providers by providing Username, i.e.
	// Spec.Username: "oidc:user@example.com"
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   ClientSpec   `json:"spec,omitempty"`
	Status ClientStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// ClientList contains a list of Client
type ClientList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Client `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Client{}, &ClientList{})
}
