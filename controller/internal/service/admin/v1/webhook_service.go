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

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	adminauthz "github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/authz"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/impersonation"
	adminv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service/utils"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

// WebhookService implements jumpstarter.admin.v1.WebhookService.
type WebhookService struct {
	adminv1.UnimplementedWebhookServiceServer
	imp *impersonation.Factory
	// watcher is the controller's non-impersonated kube client. Used to
	// load the existing CRD for the ownership pre-check on mutating
	// verbs and to issue the cluster-scope SAR that admin callers
	// satisfy for the bypass.
	watcher kclient.Client
}

func NewWebhookService(imp *impersonation.Factory, watcher kclient.Client) *WebhookService {
	return &WebhookService{imp: imp, watcher: watcher}
}

func (s *WebhookService) GetWebhook(ctx context.Context, req *jumpstarterv1.GetRequest) (*adminv1.Webhook, error) {
	key, err := utils.ParseWebhookIdentifier(req.GetName())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var w jumpstarterdevv1alpha1.Webhook
	if err := c.Get(ctx, *key, &w); err != nil {
		return nil, kerr(err)
	}
	return webhookToProto(&w), nil
}

func (s *WebhookService) ListWebhooks(ctx context.Context, req *adminv1.WebhookListRequest) (*adminv1.WebhookListResponse, error) {
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var list jumpstarterdevv1alpha1.WebhookList
	if err := c.List(ctx, &list, &kclient.ListOptions{
		Namespace: ns,
		Limit:     int64(req.GetPageSize()),
		Continue:  req.GetPageToken(),
	}); err != nil {
		return nil, kerr(err)
	}
	out := &adminv1.WebhookListResponse{NextPageToken: list.Continue}
	for i := range list.Items {
		out.Webhooks = append(out.Webhooks, webhookToProto(&list.Items[i]))
	}
	return out, nil
}

func (s *WebhookService) CreateWebhook(ctx context.Context, req *adminv1.WebhookCreateRequest) (*adminv1.Webhook, error) {
	if req.GetWebhook() == nil {
		return nil, status.Error(codes.InvalidArgument, "webhook is required")
	}
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return nil, err
	}
	name := req.GetWebhookId()
	if name == "" {
		return nil, status.Error(codes.InvalidArgument, "webhook_id is required")
	}
	secretRef, ok := secretRefFromString(req.GetWebhook().GetSecretRef())
	if !ok {
		return nil, status.Error(codes.InvalidArgument, "secret_ref must be in 'name/key' form")
	}
	events := make([]string, 0, len(req.GetWebhook().GetEvents()))
	for _, e := range req.GetWebhook().GetEvents() {
		s := eventClassToString(e)
		if s == "" {
			return nil, status.Errorf(codes.InvalidArgument, "unknown event class: %v", e)
		}
		events = append(events, s)
	}
	w := &jumpstarterdevv1alpha1.Webhook{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      name,
		},
		Spec: jumpstarterdevv1alpha1.WebhookSpec{
			URL:       req.GetWebhook().GetUrl(),
			SecretRef: secretRef,
			Events:    events,
		},
	}
	stampOwner(&w.ObjectMeta, identity.MustFromContext(ctx))

	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	if err := c.Create(ctx, w); err != nil {
		return nil, kerr(err)
	}
	return webhookToProto(w), nil
}

func (s *WebhookService) UpdateWebhook(ctx context.Context, req *adminv1.WebhookUpdateRequest) (*adminv1.Webhook, error) {
	if req.GetWebhook() == nil {
		return nil, status.Error(codes.InvalidArgument, "webhook is required")
	}
	key, err := utils.ParseWebhookIdentifier(req.GetWebhook().GetName())
	if err != nil {
		return nil, err
	}
	var existing jumpstarterdevv1alpha1.Webhook
	if err := s.watcher.Get(ctx, *key, &existing); err != nil {
		return nil, kerr(err)
	}
	if err := adminauthz.RequireNotExternallyManaged(&existing, "webhooks"); err != nil {
		return nil, err
	}
	if err := adminauthz.RequireOwnerOrClusterAdmin(ctx, s.watcher, &existing,
		"jumpstarter.dev", "webhooks", "update"); err != nil {
		return nil, err
	}

	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	w := *existing.DeepCopy()
	original := kclient.MergeFrom(w.DeepCopy())
	if u := req.GetWebhook().GetUrl(); u != "" {
		w.Spec.URL = u
	}
	if sr := req.GetWebhook().GetSecretRef(); sr != "" {
		secretRef, ok := secretRefFromString(sr)
		if !ok {
			return nil, status.Error(codes.InvalidArgument, "secret_ref must be in 'name/key' form")
		}
		w.Spec.SecretRef = secretRef
	}
	if len(req.GetWebhook().GetEvents()) > 0 {
		events := make([]string, 0, len(req.GetWebhook().GetEvents()))
		for _, e := range req.GetWebhook().GetEvents() {
			s := eventClassToString(e)
			if s == "" {
				return nil, status.Errorf(codes.InvalidArgument, "unknown event class: %v", e)
			}
			events = append(events, s)
		}
		w.Spec.Events = events
	}
	// Owner annotation is stamped only at creation time (per JEP-0014).
	if err := c.Patch(ctx, &w, original); err != nil {
		return nil, kerr(err)
	}
	return webhookToProto(&w), nil
}

func (s *WebhookService) DeleteWebhook(ctx context.Context, req *jumpstarterv1.DeleteRequest) (*emptypb.Empty, error) {
	key, err := utils.ParseWebhookIdentifier(req.GetName())
	if err != nil {
		return nil, err
	}
	var existing jumpstarterdevv1alpha1.Webhook
	if err := s.watcher.Get(ctx, *key, &existing); err != nil {
		return nil, kerr(err)
	}
	if err := adminauthz.RequireNotExternallyManaged(&existing, "webhooks"); err != nil {
		return nil, err
	}
	if err := adminauthz.RequireOwnerOrClusterAdmin(ctx, s.watcher, &existing,
		"jumpstarter.dev", "webhooks", "delete"); err != nil {
		return nil, err
	}

	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	if err := c.Delete(ctx, &jumpstarterdevv1alpha1.Webhook{
		ObjectMeta: metav1.ObjectMeta{Namespace: key.Namespace, Name: key.Name},
	}); err != nil {
		return nil, kerr(err)
	}
	return &emptypb.Empty{}, nil
}
