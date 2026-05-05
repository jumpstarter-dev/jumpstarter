/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package v1 implements the jumpstarter.admin.v1 services. Each service
// performs CRUD on the corresponding Jumpstarter CRD via an impersonating
// kube-client (so kube-audit attributes mutations to the human caller),
// stamps the jumpstarter.dev/owner annotations on every mutation, and
// surfaces Watch* as informer-backed server streams.
package v1

import (
	"context"

	"github.com/google/uuid"
	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/impersonation"
	adminv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service/utils"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/types"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

// LeaseService implements jumpstarter.admin.v1.LeaseService.
type LeaseService struct {
	adminv1.UnimplementedLeaseServiceServer
	imp     *impersonation.Factory
	watcher kclient.WithWatch
	maxTags int32
}

// NewLeaseService constructs the service. imp produces per-request
// impersonating clients; watcher is the controller's NewWithWatch client
// used by the Watch RPC (always non-impersonating because Kubernetes does
// not allow watch impersonation against shared informers).
func NewLeaseService(imp *impersonation.Factory, watcher kclient.WithWatch, maxTags int32) *LeaseService {
	return &LeaseService{imp: imp, watcher: watcher, maxTags: maxTags}
}

func (s *LeaseService) GetLease(ctx context.Context, req *jumpstarterv1.GetRequest) (*jumpstarterv1.Lease, error) {
	key, err := utils.ParseLeaseIdentifier(req.GetName())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var lease jumpstarterdevv1alpha1.Lease
	if err := c.Get(ctx, *key, &lease); err != nil {
		return nil, kerr(err)
	}
	return leaseToProto(&lease), nil
}

func (s *LeaseService) ListLeases(ctx context.Context, req *jumpstarterv1.LeaseListRequest) (*jumpstarterv1.LeaseListResponse, error) {
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return nil, err
	}
	selector, err := labels.Parse(req.GetFilter())
	if err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "invalid filter: %v", err)
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var list jumpstarterdevv1alpha1.LeaseList
	if err := c.List(ctx, &list, &kclient.ListOptions{
		Namespace:     ns,
		LabelSelector: selector,
		Limit:         int64(req.GetPageSize()),
		Continue:      req.GetPageToken(),
	}); err != nil {
		return nil, kerr(err)
	}
	out := &jumpstarterv1.LeaseListResponse{NextPageToken: list.Continue}
	for i := range list.Items {
		out.Leases = append(out.Leases, leaseToProto(&list.Items[i]))
	}
	return out, nil
}

func (s *LeaseService) CreateLease(ctx context.Context, req *jumpstarterv1.LeaseCreateRequest) (*jumpstarterv1.Lease, error) {
	if req.GetLease() == nil {
		return nil, status.Error(codes.InvalidArgument, "lease is required")
	}
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return nil, err
	}
	if err := jumpstarterdevv1alpha1.ValidateLeaseTags(req.GetLease().GetTags(), int(s.maxTags)); err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "invalid lease tags: %v", err)
	}

	id := identity.MustFromContext(ctx)
	name := req.GetLeaseId()
	if name == "" {
		u, err := uuid.NewV7()
		if err != nil {
			return nil, status.Errorf(codes.Internal, "generate lease id: %v", err)
		}
		name = u.String()
	}

	lease := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      name,
			Labels:    req.GetLease().GetLabels(),
		},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			Tags: req.GetLease().GetTags(),
		},
	}
	stampOwner(&lease.ObjectMeta, id)

	// Selector or pinned exporter
	if req.GetLease().GetSelector() != "" {
		sel, err := labels.Parse(req.GetLease().GetSelector())
		if err != nil {
			return nil, status.Errorf(codes.InvalidArgument, "invalid selector: %v", err)
		}
		lease.Spec.Selector = *labelsSelectorAsLabelSelector(sel)
	}
	if req.GetLease().GetExporterName() != "" {
		key, err := utils.ParseExporterIdentifier(req.GetLease().GetExporterName())
		if err != nil {
			return nil, err
		}
		if key.Namespace != ns {
			return nil, status.Error(codes.InvalidArgument, "exporter_name must be in the same namespace as parent")
		}
		lease.Spec.ExporterRef = &corev1.LocalObjectReference{Name: key.Name}
	}
	if lease.Spec.ExporterRef == nil && lease.Spec.Selector.MatchLabels == nil && len(lease.Spec.Selector.MatchExpressions) == 0 {
		return nil, status.Error(codes.InvalidArgument, "one of selector or exporter_name is required")
	}

	if d := req.GetLease().GetDuration(); d != nil {
		dur := metav1.Duration{Duration: d.AsDuration()}
		lease.Spec.Duration = &dur
	}
	if t := req.GetLease().GetBeginTime(); t != nil {
		lease.Spec.BeginTime = &metav1.Time{Time: t.AsTime()}
	}
	if t := req.GetLease().GetEndTime(); t != nil {
		lease.Spec.EndTime = &metav1.Time{Time: t.AsTime()}
	}

	// Caller must own a Client CRD in this namespace; the existing
	// client.v1 path uses AuthClient to find it, but the admin pipeline
	// expects the caller's identity to map to a Client via
	// jumpstarter.dev/owner. We resolve a ClientRef by username here.
	clientName, err := s.findClientForCaller(ctx, ns, id)
	if err != nil {
		return nil, err
	}
	lease.Spec.ClientRef = corev1.LocalObjectReference{Name: clientName}

	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	if err := c.Create(ctx, lease); err != nil {
		return nil, kerr(err)
	}
	return leaseToProto(lease), nil
}

// findClientForCaller resolves a Client CRD in ns whose owner annotation
// matches the caller's identity hash. If none exists, an error is returned —
// the admin Lease.Create flow assumes Clients have been pre-provisioned
// (via admin.v1.ClientService.CreateClient or by the platform on first
// login).
func (s *LeaseService) findClientForCaller(ctx context.Context, ns string, id identity.Identity) (string, error) {
	c, err := s.imp.For(ctx)
	if err != nil {
		return "", status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var list jumpstarterdevv1alpha1.ClientList
	if err := c.List(ctx, &list, kclient.InNamespace(ns)); err != nil {
		return "", kerr(err)
	}
	hash := id.OwnerHash()
	for i := range list.Items {
		if list.Items[i].Annotations[identity.OwnerAnnotation] == hash {
			return list.Items[i].Name, nil
		}
	}
	return "", status.Errorf(codes.FailedPrecondition,
		"no Client found in namespace %q for caller; create one via admin.v1.ClientService.CreateClient first", ns)
}

func (s *LeaseService) UpdateLease(ctx context.Context, req *jumpstarterv1.LeaseUpdateRequest) (*jumpstarterv1.Lease, error) {
	if req.GetLease() == nil {
		return nil, status.Error(codes.InvalidArgument, "lease is required")
	}
	key, err := utils.ParseLeaseIdentifier(req.GetLease().GetName())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var lease jumpstarterdevv1alpha1.Lease
	if err := c.Get(ctx, *key, &lease); err != nil {
		return nil, kerr(err)
	}

	original := kclient.MergeFrom(lease.DeepCopy())
	if t := req.GetLease().GetEndTime(); t != nil {
		lease.Spec.EndTime = &metav1.Time{Time: t.AsTime()}
	}
	if d := req.GetLease().GetDuration(); d != nil {
		dur := metav1.Duration{Duration: d.AsDuration()}
		lease.Spec.Duration = &dur
	}
	if t := req.GetLease().GetBeginTime(); t != nil {
		lease.Spec.BeginTime = &metav1.Time{Time: t.AsTime()}
	}
	if labelsIn := req.GetLease().GetLabels(); labelsIn != nil {
		lease.Labels = labelsIn
	}
	stampOwner(&lease.ObjectMeta, identity.MustFromContext(ctx))
	if err := c.Patch(ctx, &lease, original); err != nil {
		return nil, kerr(err)
	}
	return leaseToProto(&lease), nil
}

func (s *LeaseService) DeleteLease(ctx context.Context, req *jumpstarterv1.DeleteRequest) (*emptypb.Empty, error) {
	key, err := utils.ParseLeaseIdentifier(req.GetName())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var lease jumpstarterdevv1alpha1.Lease
	if err := c.Get(ctx, *key, &lease); err != nil {
		return nil, kerr(err)
	}
	original := kclient.MergeFrom(lease.DeepCopy())
	lease.Spec.Release = true
	if err := c.Patch(ctx, &lease, original); err != nil {
		return nil, kerr(err)
	}
	return &emptypb.Empty{}, nil
}

func (s *LeaseService) WatchLeases(req *jumpstarterv1.WatchRequest, stream adminv1.LeaseService_WatchLeasesServer) error {
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return err
	}
	selector, err := labels.Parse(req.GetFilter())
	if err != nil {
		return status.Errorf(codes.InvalidArgument, "invalid filter: %v", err)
	}
	return runWatch(stream.Context(), s.watcher, ns, req.GetResourceVersion(), selector,
		&jumpstarterdevv1alpha1.LeaseList{},
		func(rv string, ev EventEnvelope) error {
			out := &adminv1.LeaseEvent{
				EventType:       ev.Type,
				ResourceVersion: rv,
			}
			if l, ok := ev.Object.(*jumpstarterdevv1alpha1.Lease); ok {
				out.Lease = leaseToProto(l)
			}
			return stream.Send(out)
		},
	)
}

// labelsSelectorAsLabelSelector converts a parsed labels.Selector to a
// metav1.LabelSelector. We recover the original requirements when possible;
// a freshly-parsed expression always supports this round-trip.
func labelsSelectorAsLabelSelector(sel labels.Selector) *metav1.LabelSelector {
	out := &metav1.LabelSelector{MatchLabels: map[string]string{}}
	reqs, _ := sel.Requirements()
	for _, r := range reqs {
		vals := r.Values().List()
		// MatchLabels is the simple equality-only fast path.
		if r.Operator() == "=" || r.Operator() == "==" {
			if len(vals) == 1 {
				out.MatchLabels[r.Key()] = vals[0]
				continue
			}
		}
		expr := metav1.LabelSelectorRequirement{
			Key:      r.Key(),
			Operator: metav1.LabelSelectorOperator(r.Operator()),
			Values:   vals,
		}
		out.MatchExpressions = append(out.MatchExpressions, expr)
	}
	if len(out.MatchLabels) == 0 {
		out.MatchLabels = nil
	}
	return out
}

// kerr maps a controller-runtime client error to a gRPC status code, then
// returns the wrapped error. Unknown errors propagate as Internal.
func kerr(err error) error {
	if err == nil {
		return nil
	}
	switch {
	case k8sIsNotFound(err):
		return status.Errorf(codes.NotFound, "%v", err)
	case k8sIsAlreadyExists(err):
		return status.Errorf(codes.AlreadyExists, "%v", err)
	case k8sIsForbidden(err):
		return status.Errorf(codes.PermissionDenied, "%v", err)
	case k8sIsInvalid(err):
		return status.Errorf(codes.InvalidArgument, "%v", err)
	case k8sIsConflict(err):
		return status.Errorf(codes.FailedPrecondition, "%v", err)
	default:
		return status.Errorf(codes.Internal, "%v", err)
	}
}

// types.NamespacedName is unused in lease_service.go directly; keep an
// import alias so additional service files referring to it through this
// package do not need to re-import.
var _ = types.NamespacedName{}
