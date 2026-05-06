/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package authz

import (
	"context"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	authzv1 "k8s.io/api/authorization/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// RequireOwnerOrClusterAdmin enforces JEP-0014's per-resource ownership
// guard for mutating verbs on admin.v1 resources. The mapping from RPC
// to {verb, resource} comes from rpcVerbs; the namespace-scoped allow
// is already enforced by the AuthZ interceptor before the handler runs.
// This helper is the second gate, addressing two cases the SAR cannot:
//
//   - Two developers sharing a namespace and ClusterRole. Both pass the
//     SAR check, but neither should be able to mutate the other's
//     resources.
//   - The auto-provisioned identity Client (legacy client.v1 reconciler
//     creates it on first OIDC contact, no admin-stamped owner
//     annotation) — JEP-0014 §130–137 disallows the user from
//     operating on it as themselves.
//
// Decision logic:
//
//  1. If the existing resource carries `jumpstarter.dev/owner` matching
//     the caller's OwnerHash → allow (self-mutation).
//  2. Otherwise issue a SubjectAccessReview at cluster scope
//     (Namespace="") for verb on resource. If allowed → cluster-admin
//     bypass.
//  3. Otherwise deny with codes.PermissionDenied. Resources without an
//     owner annotation fall into this branch unless the caller is
//     cluster-admin, which is the intended behavior for the
//     auto-provisioned identity Client.
//
// sarClient must be the controller's non-impersonated kube client —
// SAR creation needs the controller SA's `system:auth-delegator`
// permission, not the impersonated user's.
func RequireOwnerOrClusterAdmin(ctx context.Context, sarClient client.Client,
	existing metav1.Object, apiGroup, resource, verb string) error {

	id, ok := identity.FromContext(ctx)
	if !ok {
		return status.Error(codes.Unauthenticated, "no caller identity")
	}

	// Self-mutation: the caller's identity hash matches the resource's
	// owner annotation.
	if hash := id.OwnerHash(); hash != "" {
		if owner := existing.GetAnnotations()[identity.OwnerAnnotation]; owner != "" && owner == hash {
			return nil
		}
	}

	// Cluster-admin bypass: a SAR at cluster scope returns allowed only
	// when the caller is bound via ClusterRoleBinding, not via a
	// per-namespace RoleBinding.
	extra := map[string]authzv1.ExtraValue{}
	if id.Issuer != "" {
		extra["iss"] = authzv1.ExtraValue{id.Issuer}
	}
	if id.Subject != "" {
		extra["sub"] = authzv1.ExtraValue{id.Subject}
	}
	sar := &authzv1.SubjectAccessReview{
		Spec: authzv1.SubjectAccessReviewSpec{
			User:   id.Username,
			Groups: id.Groups,
			Extra:  extra,
			ResourceAttributes: &authzv1.ResourceAttributes{
				Verb:     verb,
				Group:    apiGroup,
				Resource: resource,
				// Namespace intentionally empty — cluster-scope check.
			},
		},
	}
	if err := sarClient.Create(ctx, sar); err != nil {
		return status.Errorf(codes.Internal, "owner-bypass SAR: %v", err)
	}
	if sar.Status.Allowed {
		return nil
	}
	return status.Errorf(codes.PermissionDenied,
		"not the owner of this %s and lacks cluster-wide %s permission", resource, verb)
}
