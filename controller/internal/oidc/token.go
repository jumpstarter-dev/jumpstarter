package oidc

import (
	"context"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authorization"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func k8sToGRPCCode(err error) codes.Code {
	switch {
	case apierrors.IsNotFound(err):
		return codes.NotFound
	case apierrors.IsForbidden(err):
		return codes.PermissionDenied
	case apierrors.IsUnauthorized(err):
		return codes.Unauthenticated
	case apierrors.IsAlreadyExists(err):
		return codes.AlreadyExists
	case apierrors.IsConflict(err):
		return codes.Aborted
	case apierrors.IsInvalid(err):
		return codes.InvalidArgument
	case apierrors.IsServiceUnavailable(err):
		return codes.Unavailable
	case apierrors.IsTimeout(err), apierrors.IsServerTimeout(err):
		return codes.DeadlineExceeded
	default:
		return codes.Internal
	}
}

// errorCode keeps the code of a gRPC status error and maps a Kubernetes API
// error, such as the lookup of a deleted object, to its gRPC equivalent.
func errorCode(err error) codes.Code {
	if st, ok := status.FromError(err); ok {
		return st.Code()
	}
	return k8sToGRPCCode(err)
}

func VerifyOIDCToken(
	ctx context.Context,
	auth authentication.ContextAuthenticator,
	attr authorization.ContextAttributesGetter,
) (authorizer.Attributes, error) {
	resp, ok, err := auth.AuthenticateContext(ctx)
	if err != nil {
		return nil, err
	}

	// No configured issuer matches the token, which retrying cannot change.
	if !ok {
		return nil, status.Error(codes.Unauthenticated, "failed to authenticate token")
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
		return nil, status.Errorf(errorCode(err), "client %s/%s: %v", clientNamespace, clientName, err)
	}

	if decision != authorizer.DecisionAllow {
		return nil, status.Errorf(codes.PermissionDenied, "permission denied for client %s/%s", clientNamespace, clientName)
	}

	var client jumpstarterdevv1alpha1.Client
	if err = kclient.Get(ctx, types.NamespacedName{
		Namespace: clientNamespace,
		Name:      clientName,
	}, &client); err != nil {
		return nil, status.Errorf(k8sToGRPCCode(err), "client %s/%s: %v", clientNamespace, clientName, err)
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
		return nil, status.Errorf(errorCode(err), "exporter %s/%s: %v", exporterNamespace, exporterName, err)
	}

	if decision != authorizer.DecisionAllow {
		return nil, status.Errorf(codes.PermissionDenied, "permission denied for exporter %s/%s", exporterNamespace, exporterName)
	}

	var exporter jumpstarterdevv1alpha1.Exporter
	if err = kclient.Get(ctx, types.NamespacedName{
		Namespace: exporterNamespace,
		Name:      exporterName,
	}, &exporter); err != nil {
		return nil, status.Errorf(k8sToGRPCCode(err), "exporter %s/%s: %v", exporterNamespace, exporterName, err)
	}

	return &exporter, nil
}
