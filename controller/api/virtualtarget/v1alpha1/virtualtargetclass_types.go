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
	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// BindingMode controls when instances are provisioned.
type BindingMode string

const (
	// BindingModeImmediate provisions instances immediately to maintain a warm pool.
	BindingModeImmediate BindingMode = "Immediate"
	// BindingModeWaitForFirstConsumer provisions instances on lease request.
	BindingModeWaitForFirstConsumer BindingMode = "WaitForFirstConsumer"
)

// ReclaimPolicy controls what happens to the virtual target after lease release.
type ReclaimPolicy string

const (
	// ReclaimPolicyDelete destroys the target after lease release.
	ReclaimPolicyDelete ReclaimPolicy = "Delete"
	// ReclaimPolicyRetain preserves the target for debugging after lease release.
	ReclaimPolicyRetain ReclaimPolicy = "Retain"
)

// SchedulingSpec defines node placement constraints inherited by rendered Pods.
type SchedulingSpec struct {
	// NodeSelector is a map of key-value pairs for node selection.
	// +optional
	NodeSelector map[string]string `json:"nodeSelector,omitempty"`

	// Tolerations are tolerations for the rendered Pods.
	// +optional
	Tolerations []corev1.Toleration `json:"tolerations,omitempty"`

	// Resources defines resource requirements for the rendered Pods.
	// +optional
	Resources *corev1.ResourceRequirements `json:"resources,omitempty"`
}

// CABundleRef references a ConfigMap key containing PEM-encoded CA certificates.
// These are injected into rendered Pods for TLS verification.
type CABundleRef struct {
	// Name of the ConfigMap.
	// +kubebuilder:validation:MinLength=1
	Name string `json:"name"`

	// Key within the ConfigMap. Defaults to "ca-bundle.crt".
	// +kubebuilder:default="ca-bundle.crt"
	// +optional
	Key string `json:"key,omitempty"`
}

// VirtualTargetClassSpec defines the desired state of VirtualTargetClass.
type VirtualTargetClassSpec struct {
	// Provisioner identifies which exporter-set controller handles this class.
	// Example: "qemu.jumpstarter.dev", "corellium.jumpstarter.dev"
	// +kubebuilder:validation:MinLength=1
	Provisioner string `json:"provisioner"`

	// CredentialsSecretRef is a reference to a Secret in the same namespace
	// containing credentials for API-backed provisioners.
	// +optional
	CredentialsSecretRef *corev1.LocalObjectReference `json:"credentialsSecretRef,omitempty"`

	// Parameters holds provisioner-specific configuration as a nested object.
	// The active provisioner validates merged parameters during reconcile.
	// +optional
	// +kubebuilder:pruning:PreserveUnknownFields
	// +kubebuilder:validation:Schemaless
	Parameters *apiextensionsv1.JSON `json:"parameters,omitempty"`

	// BindingMode controls when instances are provisioned.
	// Immediate: maintain a warm pool (default).
	// WaitForFirstConsumer: provision on lease request.
	// +kubebuilder:default=Immediate
	// +kubebuilder:validation:Enum=Immediate;WaitForFirstConsumer
	BindingMode BindingMode `json:"bindingMode,omitempty"`

	// ReclaimPolicy controls what happens to the virtual target after lease release.
	// Delete: target is destroyed (default).
	// Retain: target is preserved for debugging.
	// +kubebuilder:default=Delete
	// +kubebuilder:validation:Enum=Delete;Retain
	ReclaimPolicy ReclaimPolicy `json:"reclaimPolicy,omitempty"`

	// Scheduling defines node placement constraints inherited by rendered Pods.
	// +optional
	Scheduling *SchedulingSpec `json:"scheduling,omitempty"`

	// CABundleConfigMapRef references a ConfigMap containing CA certificates
	// to inject into rendered Pods for corporate/private TLS verification.
	// +optional
	CABundleConfigMapRef *CABundleRef `json:"caBundleConfigMapRef,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:printcolumn:name="Provisioner",type="string",JSONPath=".spec.provisioner"
// +kubebuilder:printcolumn:name="Binding",type="string",JSONPath=".spec.bindingMode"

// VirtualTargetClass is the Schema for the virtualtargetclasses API.
// It defines a namespaced backend profile for virtual target provisioners.
type VirtualTargetClass struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec VirtualTargetClassSpec `json:"spec,omitempty"`
}

// +kubebuilder:object:root=true

// VirtualTargetClassList contains a list of VirtualTargetClass
type VirtualTargetClassList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []VirtualTargetClass `json:"items"`
}

func init() {
	SchemeBuilder.Register(&VirtualTargetClass{}, &VirtualTargetClassList{})
}
