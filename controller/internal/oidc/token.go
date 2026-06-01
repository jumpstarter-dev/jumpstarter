package oidc

import (
	"context"
	"fmt"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authorization"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func VerifyOIDCToken(
	ctx context.Context,
	auth authentication.ContextAuthenticator,
	attr authorization.ContextAttributesGetter,
) (authorizer.Attributes, error) {
	resp, ok, err := auth.AuthenticateContext(ctx)
	if err != nil {
		return nil, err
	}

	if !ok {
		return nil, fmt.Errorf("failed to authenticate token")
	}

	return attr.ContextAttributes(ctx, resp.User)
}

func VerifyClientObjectToken(
	ctx context.Context,
	authz authentication.ContextAuthenticator,
	authn authorizer.Authorizer,
	attr authorization.ContextAttributesGetter,
	kclient client.Client,
) (*jumpstarterdevv1alpha1.Client, error) {
	attrs, err := VerifyOIDCToken(ctx, authz, attr)
	if err != nil {
		return nil, err
	}

	if attrs.GetResource() != "Client" {
		return nil, status.Errorf(codes.InvalidArgument, "object kind mismatch")
	}

	clientName := attrs.GetName()
	clientNamespace := attrs.GetNamespace()

	decision, _, err := authn.Authorize(ctx, attrs)
	if err != nil {
		return nil, fmt.Errorf("client %s/%s: %w", clientNamespace, clientName, err)
	}

	if decision != authorizer.DecisionAllow {
		return nil, status.Errorf(codes.PermissionDenied, "permission denied for client %s/%s", clientNamespace, clientName)
	}

	var client jumpstarterdevv1alpha1.Client
	if err = kclient.Get(ctx, types.NamespacedName{
		Namespace: clientNamespace,
		Name:      clientName,
	}, &client); err != nil {
		return nil, fmt.Errorf("client %s/%s: %w", clientNamespace, clientName, err)
	}

	return &client, nil
}

func VerifyExporterObjectToken(
	ctx context.Context,
	authz authentication.ContextAuthenticator,
	authn authorizer.Authorizer,
	attr authorization.ContextAttributesGetter,
	kclient client.Client,
) (*jumpstarterdevv1alpha1.Exporter, error) {
	attrs, err := VerifyOIDCToken(ctx, authz, attr)
	if err != nil {
		return nil, err
	}

	if attrs.GetResource() != "Exporter" {
		return nil, status.Errorf(codes.InvalidArgument, "object kind mismatch")
	}

	exporterName := attrs.GetName()
	exporterNamespace := attrs.GetNamespace()

	decision, _, err := authn.Authorize(ctx, attrs)
	if err != nil {
		return nil, fmt.Errorf("exporter %s/%s: %w", exporterNamespace, exporterName, err)
	}

	if decision != authorizer.DecisionAllow {
		return nil, status.Errorf(codes.PermissionDenied, "permission denied for exporter %s/%s", exporterNamespace, exporterName)
	}

	var exporter jumpstarterdevv1alpha1.Exporter
	if err = kclient.Get(ctx, types.NamespacedName{
		Namespace: exporterNamespace,
		Name:      exporterName,
	}, &exporter); err != nil {
		return nil, fmt.Errorf("exporter %s/%s: %w", exporterNamespace, exporterName, err)
	}

	return &exporter, nil
}
