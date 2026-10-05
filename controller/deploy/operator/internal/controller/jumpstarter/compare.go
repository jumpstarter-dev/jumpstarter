/*
Copyright 2025.

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

package jumpstarter

import (
	"fmt"

	"github.com/pmezard/go-difflib/difflib"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	"k8s.io/apimachinery/pkg/api/equality"
	"sigs.k8s.io/yaml"
)

// deploymentNeedsUpdate checks if a deployment needs to be updated using K8s semantic equality.
func deploymentNeedsUpdate(existing, desired *appsv1.Deployment) bool {
	// Compare labels (only if desired.Labels is non-nil)
	if desired.Labels != nil && !equality.Semantic.DeepEqual(existing.Labels, desired.Labels) {
		return true
	}

	// Compare annotations (only if desired.Annotations is non-nil)
	if desired.Annotations != nil && !equality.Semantic.DeepEqual(existing.Annotations, desired.Annotations) {
		return true
	}

	// Compare the entire Spec using K8s semantic equality (handles nil vs empty automatically)
	return !equality.Semantic.DeepEqual(existing.Spec, desired.Spec)
}

// configMapNeedsUpdate checks if a configmap needs to be updated using K8s semantic equality.
func configMapNeedsUpdate(existing, desired *corev1.ConfigMap) bool {
	// Compare labels (only if desired.Labels is non-nil)
	if desired.Labels != nil && !equality.Semantic.DeepEqual(existing.Labels, desired.Labels) {
		return true
	}

	// Compare annotations (only if desired.Annotations is non-nil)
	if desired.Annotations != nil && !equality.Semantic.DeepEqual(existing.Annotations, desired.Annotations) {
		return true
	}

	// Compare data (only if desired.Data is non-nil)
	if desired.Data != nil && !equality.Semantic.DeepEqual(existing.Data, desired.Data) {
		return true
	}

	// Compare binary data (only if desired.BinaryData is non-nil)
	if desired.BinaryData != nil && !equality.Semantic.DeepEqual(existing.BinaryData, desired.BinaryData) {
		return true
	}

	return false
}

// serviceAccountNeedsUpdate checks if a service account needs to be updated using K8s semantic equality.
func serviceAccountNeedsUpdate(existing, desired *corev1.ServiceAccount) bool {
	// Compare labels (only if desired.Labels is non-nil)
	if desired.Labels != nil && !equality.Semantic.DeepEqual(existing.Labels, desired.Labels) {
		return true
	}

	// Compare annotations (only if desired.Annotations is non-nil)
	if desired.Annotations != nil && !equality.Semantic.DeepEqual(existing.Annotations, desired.Annotations) {
		return true
	}

	return false
}

// roleNeedsUpdate checks if a role needs to be updated using K8s semantic equality.
func roleNeedsUpdate(existing, desired *rbacv1.Role) bool {
	// Compare labels (only if desired.Labels is non-nil)
	if desired.Labels != nil && !equality.Semantic.DeepEqual(existing.Labels, desired.Labels) {
		return true
	}

	// Compare annotations (only if desired.Annotations is non-nil)
	if desired.Annotations != nil && !equality.Semantic.DeepEqual(existing.Annotations, desired.Annotations) {
		return true
	}

	// Compare rules (only if non-nil in desired)
	if desired.Rules != nil && !equality.Semantic.DeepEqual(existing.Rules, desired.Rules) {
		return true
	}

	return false
}

// roleBindingNeedsUpdate checks if a role binding needs to be updated using K8s semantic equality.
func roleBindingNeedsUpdate(existing, desired *rbacv1.RoleBinding) bool {
	// Compare labels (only if desired.Labels is non-nil)
	if desired.Labels != nil && !equality.Semantic.DeepEqual(existing.Labels, desired.Labels) {
		return true
	}

	// Compare annotations (only if desired.Annotations is non-nil)
	if desired.Annotations != nil && !equality.Semantic.DeepEqual(existing.Annotations, desired.Annotations) {
		return true
	}

	// Compare subjects (only if non-nil in desired)
	if desired.Subjects != nil && !equality.Semantic.DeepEqual(existing.Subjects, desired.Subjects) {
		return true
	}

	// Compare role ref (only if non-zero in desired)
	if desired.RoleRef.Name != "" && !equality.Semantic.DeepEqual(existing.RoleRef, desired.RoleRef) {
		return true
	}

	return false
}

// generateDiff creates a unified diff between existing and desired resources.
// It works with any Kubernetes resource type.
// Returns the diff string and any error encountered during serialization.
func generateDiff[T any](existing, desired *T) (string, error) {
	// Serialize existing resource to YAML
	existingYAML, err := yaml.Marshal(existing)
	if err != nil {
		return "", fmt.Errorf("failed to marshal existing resource: %w", err)
	}

	// Serialize desired resource to YAML
	desiredYAML, err := yaml.Marshal(desired)
	if err != nil {
		return "", fmt.Errorf("failed to marshal desired resource: %w", err)
	}

	// Generate unified diff
	diff := difflib.UnifiedDiff{
		A:        difflib.SplitLines(string(existingYAML)),
		B:        difflib.SplitLines(string(desiredYAML)),
		FromFile: "Existing",
		ToFile:   "Desired",
		Context:  3,
	}

	return difflib.GetUnifiedDiffString(diff)
}
