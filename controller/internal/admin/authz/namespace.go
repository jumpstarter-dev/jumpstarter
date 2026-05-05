/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package authz

import (
	"fmt"

	adminv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service/utils"
)

// NamespaceFromAdminRequest extracts the target namespace from any admin
// RPC request. Every Jumpstarter CRD is namespace-scoped, so each request
// type carries the namespace either in `parent` ("namespaces/<ns>"), in
// `name` ("namespaces/<ns>/<resource>/<id>"), or nested in the resource's
// own `name` for Update RPCs.
//
// The return value is intended for use as the SubjectAccessReview's
// ResourceAttributes.Namespace, ensuring namespace-scoped RoleBindings
// authorize admin operations rather than being silently ignored at
// cluster scope.
func NamespaceFromAdminRequest(req any) (string, error) {
	switch r := req.(type) {

	// Shared cross-resource requests (Get / Delete / Watch).
	case *jumpstarterv1.GetRequest:
		return namespaceFromObjectName(r.GetName())
	case *jumpstarterv1.DeleteRequest:
		return namespaceFromObjectName(r.GetName())
	case *jumpstarterv1.WatchRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())

	// Lease.
	case *jumpstarterv1.LeaseListRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())
	case *jumpstarterv1.LeaseCreateRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())
	case *jumpstarterv1.LeaseUpdateRequest:
		return namespaceFromObjectName(r.GetLease().GetName())

	// Exporter.
	case *jumpstarterv1.ExporterListRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())
	case *jumpstarterv1.ExporterCreateRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())
	case *jumpstarterv1.ExporterUpdateRequest:
		return namespaceFromObjectName(r.GetExporter().GetName())

	// Client (admin).
	case *jumpstarterv1.ClientListRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())
	case *jumpstarterv1.ClientCreateRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())
	case *jumpstarterv1.ClientUpdateRequest:
		return namespaceFromObjectName(r.GetClient().GetName())

	// Webhook (admin-only resource, request types live in admin.v1).
	case *adminv1.WebhookListRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())
	case *adminv1.WebhookCreateRequest:
		return utils.ParseNamespaceIdentifier(r.GetParent())
	case *adminv1.WebhookUpdateRequest:
		return namespaceFromObjectName(r.GetWebhook().GetName())

	case nil:
		// Stream interceptor invokes Authorize before the first request
		// message arrives; the per-resource handler performs a second
		// namespace-scoped check. Return empty so the cluster-scope SAR
		// is the gate at this stage.
		return "", nil
	}
	return "", fmt.Errorf("namespace extractor: unrecognized admin request type %T", req)
}

// namespaceFromObjectName parses "namespaces/<ns>/<resource>/<id>" and
// returns the namespace. Returns empty + error if the identifier does not
// match the expected shape.
func namespaceFromObjectName(name string) (string, error) {
	// Reuse ParseObjectIdentifier with a wildcard kind by stripping the
	// kind segment manually — there is no Unparse-namespace-only helper.
	if name == "" {
		return "", fmt.Errorf("missing object name")
	}
	// We accept any kind; ParseObjectIdentifier requires a fixed kind, so
	// inline the prefix check.
	const prefix = "namespaces/"
	if len(name) < len(prefix) || name[:len(prefix)] != prefix {
		return "", fmt.Errorf("invalid object name %q: expected prefix %q", name, prefix)
	}
	rest := name[len(prefix):]
	for i := 0; i < len(rest); i++ {
		if rest[i] == '/' {
			return rest[:i], nil
		}
	}
	return "", fmt.Errorf("invalid object name %q: missing resource segment", name)
}
