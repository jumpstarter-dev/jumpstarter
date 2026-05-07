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
	adminauthz "github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/authz"
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

// ClientService implements jumpstarter.admin.v1.ClientService — CRUD over
// the Client Kubernetes resource (note: the deliberate name overlap with
// jumpstarter.client.v1.ClientService follows from mirroring the kube
// resource surface; see client.proto for the rationale).
type ClientService struct {
	adminv1.UnimplementedClientServiceServer
	imp     *impersonation.Factory
	watcher kclient.WithWatch
}

func NewClientService(imp *impersonation.Factory, watcher kclient.WithWatch) *ClientService {
	return &ClientService{imp: imp, watcher: watcher}
}

func (s *ClientService) GetClient(ctx context.Context, req *jumpstarterv1.GetRequest) (*jumpstarterv1.Client, error) {
	key, err := utils.ParseClientIdentifier(req.GetName())
	if err != nil {
		return nil, err
	}
	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	var obj jumpstarterdevv1alpha1.Client
	if err := c.Get(ctx, *key, &obj); err != nil {
		return nil, kerr(err)
	}
	return clientToProto(&obj), nil
}

func (s *ClientService) ListClients(ctx context.Context, req *jumpstarterv1.ClientListRequest) (*jumpstarterv1.ClientListResponse, error) {
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
	var list jumpstarterdevv1alpha1.ClientList
	if err := c.List(ctx, &list, &kclient.ListOptions{
		Namespace:     ns,
		LabelSelector: selector,
		Limit:         int64(req.GetPageSize()),
		Continue:      req.GetPageToken(),
	}); err != nil {
		return nil, kerr(err)
	}
	out := &jumpstarterv1.ClientListResponse{NextPageToken: list.Continue}
	for i := range list.Items {
		out.Clients = append(out.Clients, clientToProto(&list.Items[i]))
	}
	return out, nil
}

func (s *ClientService) CreateClient(ctx context.Context, req *jumpstarterv1.ClientCreateRequest) (*jumpstarterv1.Client, error) {
	if req.GetClient() == nil {
		return nil, status.Error(codes.InvalidArgument, "client is required")
	}
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return nil, err
	}
	name := req.GetClientId()
	if name == "" {
		return nil, status.Error(codes.InvalidArgument, "client_id is required")
	}

	id := identity.MustFromContext(ctx)
	obj := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      name,
			Labels:    req.GetClient().GetLabels(),
		},
	}
	if u := req.GetClient().GetUsername(); u != "" {
		obj.Spec.Username = &u
	} else if id.Username != "" {
		u := id.Username
		obj.Spec.Username = &u
	}
	stampOwner(&obj.ObjectMeta, id)

	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	if err := c.Create(ctx, obj); err != nil {
		return nil, kerr(err)
	}

	// Poll the Client's status with the controller's non-impersonated
	// client and read the bootstrap Secret with it too — self-service
	// developers (and even cluster admins acting on a foreign-owned
	// Client) need not have direct `secrets` RBAC; the inline token is
	// returned in lieu of that, per JEP-0014 §DD-3.
	token, err := s.waitForBootstrap(ctx, types.NamespacedName{Namespace: ns, Name: name})
	if err != nil {
		return nil, err
	}
	var fresh jumpstarterdevv1alpha1.Client
	if err := c.Get(ctx, types.NamespacedName{Namespace: ns, Name: name}, &fresh); err != nil {
		return nil, kerr(err)
	}
	out := clientToProto(&fresh)
	{
		tok := token
		out.Token = &tok
	}
	return out, nil
}

func (s *ClientService) waitForBootstrap(ctx context.Context, key types.NamespacedName) (string, error) {
	var secretName string
	pollErr := wait.PollUntilContextTimeout(ctx, 200*time.Millisecond, credentialWaitTimeout, true, func(ctx context.Context) (bool, error) {
		var obj jumpstarterdevv1alpha1.Client
		if err := s.watcher.Get(ctx, key, &obj); err != nil {
			if k8sIsNotFound(err) {
				return false, nil
			}
			return false, err
		}
		if obj.Status.Credential != nil && obj.Status.Credential.Name != "" {
			secretName = obj.Status.Credential.Name
			return true, nil
		}
		return false, nil
	})
	if pollErr != nil {
		return "", status.Errorf(codes.DeadlineExceeded, "credential provisioning timed out: %v", pollErr)
	}
	var sec corev1.Secret
	if err := s.watcher.Get(ctx, types.NamespacedName{Namespace: key.Namespace, Name: secretName}, &sec); err != nil {
		return "", kerr(err)
	}
	if t, ok := sec.Data["token"]; ok {
		return string(t), nil
	}
	return "", status.Errorf(codes.Internal, "credential secret %q has no 'token' key", secretName)
}

func (s *ClientService) UpdateClient(ctx context.Context, req *jumpstarterv1.ClientUpdateRequest) (*jumpstarterv1.Client, error) {
	if req.GetClient() == nil {
		return nil, status.Error(codes.InvalidArgument, "client is required")
	}
	key, err := utils.ParseClientIdentifier(req.GetClient().GetName())
	if err != nil {
		return nil, err
	}
	// Load via the controller's non-impersonated client so the ownership
	// check runs even when the impersonated user lacks `get` (e.g. a role
	// granting only `update`).
	var existing jumpstarterdevv1alpha1.Client
	if err := s.watcher.Get(ctx, *key, &existing); err != nil {
		return nil, kerr(err)
	}
	if err := adminauthz.RequireNotExternallyManaged(&existing, "clients"); err != nil {
		return nil, err
	}
	if err := adminauthz.RequireOwnerOrClusterAdmin(ctx, s.watcher, &existing,
		"jumpstarter.dev", "clients", "update"); err != nil {
		return nil, err
	}

	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	obj := *existing.DeepCopy()
	original := kclient.MergeFrom(obj.DeepCopy())
	if labelsIn := req.GetClient().GetLabels(); labelsIn != nil {
		obj.Labels = labelsIn
	}
	if u := req.GetClient().GetUsername(); u != "" {
		obj.Spec.Username = &u
	}
	// Owner annotation is stamped only at creation time (per JEP-0014):
	// preserving it here keeps cluster-admin updates from silently
	// re-attributing ownership.
	if err := c.Patch(ctx, &obj, original); err != nil {
		return nil, kerr(err)
	}
	return clientToProto(&obj), nil
}

func (s *ClientService) DeleteClient(ctx context.Context, req *jumpstarterv1.DeleteRequest) (*emptypb.Empty, error) {
	key, err := utils.ParseClientIdentifier(req.GetName())
	if err != nil {
		return nil, err
	}
	var existing jumpstarterdevv1alpha1.Client
	if err := s.watcher.Get(ctx, *key, &existing); err != nil {
		return nil, kerr(err)
	}
	if err := adminauthz.RequireNotExternallyManaged(&existing, "clients"); err != nil {
		return nil, err
	}
	if err := adminauthz.RequireOwnerOrClusterAdmin(ctx, s.watcher, &existing,
		"jumpstarter.dev", "clients", "delete"); err != nil {
		return nil, err
	}

	c, err := s.imp.For(ctx)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "build impersonation client: %v", err)
	}
	if err := c.Delete(ctx, &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Namespace: key.Namespace, Name: key.Name},
	}); err != nil {
		return nil, kerr(err)
	}
	return &emptypb.Empty{}, nil
}

func (s *ClientService) WatchClients(req *jumpstarterv1.WatchRequest, stream adminv1.ClientService_WatchClientsServer) error {
	ns, err := utils.ParseNamespaceIdentifier(req.GetParent())
	if err != nil {
		return err
	}
	selector, err := labels.Parse(req.GetFilter())
	if err != nil {
		return status.Errorf(codes.InvalidArgument, "invalid filter: %v", err)
	}
	return runWatch(stream.Context(), s.watcher, ns, req.GetResourceVersion(), selector,
		&jumpstarterdevv1alpha1.ClientList{},
		func(rv string, ev EventEnvelope) error {
			out := &adminv1.ClientEvent{
				EventType:       ev.Type,
				ResourceVersion: rv,
			}
			if obj, ok := ev.Object.(*jumpstarterdevv1alpha1.Client); ok {
				out.Client = clientToProto(obj)
			}
			return stream.Send(out)
		},
	)
}
