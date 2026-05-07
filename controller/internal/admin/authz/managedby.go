/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package authz

import (
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/managedby"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// RequireNotExternallyManaged refuses mutating admin.v1 verbs on
// resources tracked by an external tool (ArgoCD, Helm, kustomize, …).
// The policy is intentionally absolute — even cluster admins are
// blocked from editing GitOps-managed resources via admin.v1; they can
// still kubectl-edit if they really mean to fight the tool of record.
//
// Returns nil when the resource carries no external-management
// markers; returns codes.FailedPrecondition (HTTP 412) otherwise so
// the response is distinguishable from the 403 Forbidden the
// per-caller ownership check returns.
func RequireNotExternallyManaged(existing metav1.Object, resource string) error {
	if managedby.IsExternal(existing) {
		return status.Errorf(codes.FailedPrecondition,
			"%s is externally managed (e.g. by ArgoCD/Helm); use that tool to modify it",
			resource)
	}
	return nil
}
