/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package managedby detects whether a Jumpstarter resource is being
// reconciled by an external tool (GitOps controllers, Helm,
// kustomize-controller, ArgoCD, Flux, …). The detection logic is
// shared by the proto converter (which surfaces the result as
// `metadata.externally_managed`) and the admin.v1 enforcement helper
// (which refuses Update/Delete on externally-managed resources).
package managedby

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	// LabelManagedBy is the recommended Kubernetes label every major
	// GitOps and packaging tool (Helm, kustomize-controller, Flux,
	// ArgoCD when properly configured) sets to identify itself.
	// https://kubernetes.io/docs/concepts/overview/working-with-objects/common-labels/
	LabelManagedBy = "app.kubernetes.io/managed-by"

	// AnnotationArgoCDTrackingID is ArgoCD's per-resource marker; it's
	// always present on ArgoCD-tracked resources even when the
	// recommended label is omitted from the source manifest.
	AnnotationArgoCDTrackingID = "argocd.argoproj.io/tracking-id"

	// AnnotationHelmReleaseName is Helm's belt-and-suspenders marker
	// for resources installed by `helm install`.
	AnnotationHelmReleaseName = "meta.helm.sh/release-name"
)

// IsExternal returns true when m carries any of the well-known
// markers that GitOps / packaging tools use to claim ownership of a
// resource. The recommended label alone covers most cases; the two
// annotation fallbacks catch tooling that doesn't set it.
//
// The Jumpstarter controller never sets `app.kubernetes.io/managed-by`
// on resources it creates — absence of the label is "ours" by
// convention.
func IsExternal(m metav1.Object) bool {
	if m == nil {
		return false
	}
	if m.GetLabels()[LabelManagedBy] != "" {
		return true
	}
	annos := m.GetAnnotations()
	if annos[AnnotationArgoCDTrackingID] != "" {
		return true
	}
	if annos[AnnotationHelmReleaseName] != "" {
		return true
	}
	return false
}
