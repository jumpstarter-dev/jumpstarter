/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package v1

import (
	"context"
	"time"

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
	"k8s.io/apimachinery/pkg/util/wait"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

// credentialWaitTimeout bounds how long Create{Exporter,Client} blocks for
// the controller's reconciler to provision the bootstrap Secret. The
// reconciler usually completes in < 1s; 30s leaves headroom for an
// overloaded apiserver without making the RPC look stuck.
const credentialWaitTimeout = 30 * time.Second

// ExporterService implements jumpstarter.admin.v1.ExporterService.
type ExporterService struct {
	adminv1.UnimplementedExporterServiceServer
	imp     *impersonation.Factory
	watcher kclient.WithWatch
}

func NewExporterService(imp *impersonation.Factory, watcher kclient.WithWatch) *ExporterService {
	return &ExporterService{imp: imp, watcher: watcher}
}

func (s *ExporterService) GetExporter(ctx context.Context, req *jumpstarterv1.GetRequest) (*jumpstarterv1.Exporter, error) {
	key, err := utils.ParseExporterIdentifier(req.GetName())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var e jumpstarterdevv1alpha1.Exporter
	if err := c.Get(ctx, *key, &e); err != nil {
		return nil, kerr(err)
	}
	return exporterToProto(&e), nil
}

func (s *ExporterService) ListExporters(ctx context.Context, req *jumpstarterv1.ExporterListRequest) (*jumpstarterv1.ExporterListResponse, error) {
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
	var list jumpstarterdevv1alpha1.ExporterList
	if err := c.List(ctx, &list, &kclient.ListOptions{
		Namespace:     ns,
		LabelSelector: selector,
		Limit:         int64(req.GetPageSize()),
		Continue:      req.GetPageToken(),
	}); err != nil {
		return nil, kerr(err)
	}
	out := &jumpstarterv1.ExporterListResponse{NextPageToken: list.Continue}
	for i := range list.Items {
		out.Exporters = append(out.Exporters, exporterToProto(&list.Items[i]))
	}
	return out, nil
}

func (s *ExporterService) CreateExporter(ctx context.Context, req *jumpstarterv1.ExporterCreateRequest) (*jumpstarterv1.Exporter, error) {
	if req.GetExporter() == nil {
		return nil, status.Error(codes.InvalidArgument, "exporter is required")
	}
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return nil, err
	}
	name := req.GetExporterId()
	if name == "" {
		return nil, status.Error(codes.InvalidArgument, "exporter_id is required")
	}

	id := identity.MustFromContext(ctx)
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      name,
			Labels:    req.GetExporter().GetLabels(),
		},
	}
	if u := req.GetExporter().GetUsername(); u != "" {
		exp.Spec.Username = &u
	} else if id.Username != "" {
		u := id.Username
		exp.Spec.Username = &u
	}
	stampOwner(&exp.ObjectMeta, id)

	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	if err := c.Create(ctx, exp); err != nil {
		return nil, kerr(err)
	}

	// Wait for the existing ExporterReconciler to provision the credential
	// Secret and stamp Status.Credential. This is the JEP's "inline
	// credential return" contract: clients never poll for a Secret to
	// appear, the RPC blocks until the token is ready or times out.
	token, err := s.waitForBootstrap(ctx, c, types.NamespacedName{Namespace: ns, Name: name})
	if err != nil {
		return nil, err
	}

	// Reload the resource so we return the post-reconcile view.
	var fresh jumpstarterdevv1alpha1.Exporter
	if err := c.Get(ctx, types.NamespacedName{Namespace: ns, Name: name}, &fresh); err != nil {
		return nil, kerr(err)
	}
	out := exporterToProto(&fresh)
	{ tok := token; out.Token = &tok }
	return out, nil
}

// waitForBootstrap polls the Exporter until its Status.Credential is
// populated, then reads the referenced Secret and returns the token.
func (s *ExporterService) waitForBootstrap(ctx context.Context, c kclient.Client, key types.NamespacedName) (string, error) {
	var secretName string
	pollErr := wait.PollUntilContextTimeout(ctx, 200*time.Millisecond, credentialWaitTimeout, true, func(ctx context.Context) (bool, error) {
		var e jumpstarterdevv1alpha1.Exporter
		if err := c.Get(ctx, key, &e); err != nil {
			if k8sIsNotFound(err) {
				return false, nil
			}
			return false, err
		}
		if e.Status.Credential != nil && e.Status.Credential.Name != "" {
			secretName = e.Status.Credential.Name
			return true, nil
		}
		return false, nil
	})
	if pollErr != nil {
		return "", status.Errorf(codes.DeadlineExceeded, "credential provisioning timed out: %v", pollErr)
	}
	var sec corev1.Secret
	if err := c.Get(ctx, types.NamespacedName{Namespace: key.Namespace, Name: secretName}, &sec); err != nil {
		return "", kerr(err)
	}
	if t, ok := sec.Data["token"]; ok {
		return string(t), nil
	}
	return "", status.Errorf(codes.Internal, "credential secret %q has no 'token' key", secretName)
}

func (s *ExporterService) UpdateExporter(ctx context.Context, req *jumpstarterv1.ExporterUpdateRequest) (*jumpstarterv1.Exporter, error) {
	if req.GetExporter() == nil {
		return nil, status.Error(codes.InvalidArgument, "exporter is required")
	}
	key, err := utils.ParseExporterIdentifier(req.GetExporter().GetName())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var e jumpstarterdevv1alpha1.Exporter
	if err := c.Get(ctx, *key, &e); err != nil {
		return nil, kerr(err)
	}
	original := kclient.MergeFrom(e.DeepCopy())
	if labelsIn := req.GetExporter().GetLabels(); labelsIn != nil {
		e.Labels = labelsIn
	}
	if u := req.GetExporter().GetUsername(); u != "" {
		e.Spec.Username = &u
	}
	stampOwner(&e.ObjectMeta, identity.MustFromContext(ctx))
	if err := c.Patch(ctx, &e, original); err != nil {
		return nil, kerr(err)
	}
	return exporterToProto(&e), nil
}

func (s *ExporterService) DeleteExporter(ctx context.Context, req *jumpstarterv1.DeleteRequest) (*emptypb.Empty, error) {
	key, err := utils.ParseExporterIdentifier(req.GetName())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	if err := c.Delete(ctx, &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Namespace: key.Namespace, Name: key.Name},
	}); err != nil {
		return nil, kerr(err)
	}
	return &emptypb.Empty{}, nil
}

func (s *ExporterService) WatchExporters(req *jumpstarterv1.WatchRequest, stream adminv1.ExporterService_WatchExportersServer) error {
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return err
	}
	selector, err := labels.Parse(req.GetFilter())
	if err != nil {
		return status.Errorf(codes.InvalidArgument, "invalid filter: %v", err)
	}
	return runWatch(stream.Context(), s.watcher, ns, req.GetResourceVersion(), selector,
		&jumpstarterdevv1alpha1.ExporterList{},
		func(rv string, ev EventEnvelope) error {
			out := &adminv1.ExporterEvent{
				EventType:       ev.Type,
				ResourceVersion: rv,
			}
			if e, ok := ev.Object.(*jumpstarterdevv1alpha1.Exporter); ok {
				out.Exporter = exporterToProto(e)
			}
			return stream.Send(out)
		},
	)
}
