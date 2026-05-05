/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package authz delegates admin RPC authorization to the kube-apiserver via
// SubjectAccessReview. The mapping from gRPC FullMethod to {verb, resource}
// is a static table — every admin RPC must be listed in rpcVerbs or the
// interceptor denies it. Per-resource ownership checks (for owner-only
// verbs like update/delete in self-service contexts) are performed by the
// per-resource handlers themselves, since they need the loaded CRD.
package authz

import (
	"context"
	"strings"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	authzv1 "k8s.io/api/authorization/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// rpcVerb describes the SAR attributes for a single admin RPC.
type rpcVerb struct {
	Verb     string // SAR verb: get, list, watch, create, update, patch, delete
	Resource string // SAR resource (plural lowercase): leases, exporters, clients, webhooks
}

// rpcVerbs maps every admin gRPC FullMethod to its SAR resource attributes.
// Adding a new admin RPC MUST add a row here, otherwise the request is denied.
var rpcVerbs = map[string]rpcVerb{
	// LeaseService
	"/jumpstarter.admin.v1.LeaseService/GetLease":     {Verb: "get", Resource: "leases"},
	"/jumpstarter.admin.v1.LeaseService/ListLeases":   {Verb: "list", Resource: "leases"},
	"/jumpstarter.admin.v1.LeaseService/CreateLease":  {Verb: "create", Resource: "leases"},
	"/jumpstarter.admin.v1.LeaseService/UpdateLease":  {Verb: "update", Resource: "leases"},
	"/jumpstarter.admin.v1.LeaseService/DeleteLease":  {Verb: "delete", Resource: "leases"},
	"/jumpstarter.admin.v1.LeaseService/WatchLeases":  {Verb: "watch", Resource: "leases"},

	// ExporterService
	"/jumpstarter.admin.v1.ExporterService/GetExporter":    {Verb: "get", Resource: "exporters"},
	"/jumpstarter.admin.v1.ExporterService/ListExporters":  {Verb: "list", Resource: "exporters"},
	"/jumpstarter.admin.v1.ExporterService/CreateExporter": {Verb: "create", Resource: "exporters"},
	"/jumpstarter.admin.v1.ExporterService/UpdateExporter": {Verb: "update", Resource: "exporters"},
	"/jumpstarter.admin.v1.ExporterService/DeleteExporter": {Verb: "delete", Resource: "exporters"},
	"/jumpstarter.admin.v1.ExporterService/WatchExporters": {Verb: "watch", Resource: "exporters"},

	// ClientService (admin)
	"/jumpstarter.admin.v1.ClientService/GetClient":    {Verb: "get", Resource: "clients"},
	"/jumpstarter.admin.v1.ClientService/ListClients":  {Verb: "list", Resource: "clients"},
	"/jumpstarter.admin.v1.ClientService/CreateClient": {Verb: "create", Resource: "clients"},
	"/jumpstarter.admin.v1.ClientService/UpdateClient": {Verb: "update", Resource: "clients"},
	"/jumpstarter.admin.v1.ClientService/DeleteClient": {Verb: "delete", Resource: "clients"},
	"/jumpstarter.admin.v1.ClientService/WatchClients": {Verb: "watch", Resource: "clients"},

	// WebhookService
	"/jumpstarter.admin.v1.WebhookService/GetWebhook":    {Verb: "get", Resource: "webhooks"},
	"/jumpstarter.admin.v1.WebhookService/ListWebhooks":  {Verb: "list", Resource: "webhooks"},
	"/jumpstarter.admin.v1.WebhookService/CreateWebhook": {Verb: "create", Resource: "webhooks"},
	"/jumpstarter.admin.v1.WebhookService/UpdateWebhook": {Verb: "update", Resource: "webhooks"},
	"/jumpstarter.admin.v1.WebhookService/DeleteWebhook": {Verb: "delete", Resource: "webhooks"},
}

// NamespaceFromRequest is the function signature an RPC handler set provides
// to extract the request's target namespace from its proto request message.
type NamespaceFromRequest func(req any) (string, error)

// Authorizer issues SubjectAccessReviews on behalf of admin RPCs.
type Authorizer struct {
	client    client.Client
	apiGroup  string
	namespace NamespaceFromRequest
}

// NewAuthorizer constructs an Authorizer. apiGroup is typically
// "jumpstarter.dev". namespaceFn extracts the namespace component from a
// request message; if nil, every SAR uses an empty namespace (cluster-scope).
func NewAuthorizer(c client.Client, apiGroup string, namespaceFn NamespaceFromRequest) *Authorizer {
	return &Authorizer{client: c, apiGroup: apiGroup, namespace: namespaceFn}
}

// Authorize issues a SubjectAccessReview for fullMethod and req. It returns
// nil on allow, or a gRPC status error (Unauthenticated / PermissionDenied)
// on deny.
func (a *Authorizer) Authorize(ctx context.Context, fullMethod string, req any) error {
	id, ok := identity.FromContext(ctx)
	if !ok {
		return status.Errorf(codes.Unauthenticated, "no caller identity")
	}
	verb, ok := rpcVerbs[fullMethod]
	if !ok {
		return status.Errorf(codes.PermissionDenied, "no SAR mapping for %s", fullMethod)
	}

	var ns string
	if a.namespace != nil {
		var err error
		ns, err = a.namespace(req)
		if err != nil {
			return status.Errorf(codes.InvalidArgument, "%v", err)
		}
	}

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
				Verb:      verb.Verb,
				Group:     a.apiGroup,
				Resource:  verb.Resource,
				Namespace: ns,
			},
		},
	}
	if err := a.client.Create(ctx, sar); err != nil {
		// Distinguish missing CRD/RBAC errors from genuine transport errors.
		if meta.IsNoMatchError(err) {
			return status.Errorf(codes.Internal, "SAR API not available: %v", err)
		}
		return status.Errorf(codes.Internal, "SubjectAccessReview failed: %v", err)
	}
	if !sar.Status.Allowed {
		reason := strings.TrimSpace(sar.Status.Reason)
		if reason == "" {
			reason = "denied by RBAC"
		}
		return status.Errorf(codes.PermissionDenied, "%s on %s.%s in %q: %s",
			verb.Verb, verb.Resource, a.apiGroup, ns, reason)
	}
	return nil
}

// UnaryServerInterceptor authorizes every admin unary call.
func (a *Authorizer) UnaryServerInterceptor() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		if err := a.Authorize(ctx, info.FullMethod, req); err != nil {
			return nil, err
		}
		return handler(ctx, req)
	}
}

// StreamServerInterceptor authorizes every admin stream call. The stream's
// first message is not yet available, so the SAR runs against an empty
// namespace if no namespaceFn is configured. For Watch* RPCs the stream
// handler should perform a second namespace-scoped check after parsing the
// request.
func (a *Authorizer) StreamServerInterceptor() grpc.StreamServerInterceptor {
	return func(srv any, ss grpc.ServerStream, info *grpc.StreamServerInfo, handler grpc.StreamHandler) error {
		if err := a.Authorize(ss.Context(), info.FullMethod, nil); err != nil {
			return err
		}
		return handler(srv, ss)
	}
}
