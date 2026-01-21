package auth

import (
	"context"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authorization"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

type Auth struct {
	client kclient.Client
	authn  authentication.ContextAuthenticator
	authz  authorizer.Authorizer
	attr   authorization.ContextAttributesGetter
}

func NewAuth(
	client kclient.Client,
	authn authentication.ContextAuthenticator,
	authz authorizer.Authorizer,
	attr authorization.ContextAttributesGetter,
) *Auth {
	return &Auth{
		client: client,
		authn:  authn,
		authz:  authz,
		attr:   attr,
	}
}

func (s *Auth) AuthClient(ctx context.Context, namespace string) (*jumpstarterdevv1alpha1.Client, error) {
	jclient, err := oidc.VerifyClientObjectToken(
		ctx,
		s.authn,
		s.authz,
		s.attr,
		s.client,
	)

	if err != nil {
		return nil, err
	}

	if namespace != jclient.Namespace {
		return nil, status.Error(codes.PermissionDenied, "namespace mismatch")
	}

	return jclient, nil
}

func (s *Auth) AuthExporter(ctx context.Context, namespace string) (*jumpstarterdevv1alpha1.Exporter, error) {
	jexporter, err := oidc.VerifyExporterObjectToken(
		ctx,
		s.authn,
		s.authz,
		s.attr,
		s.client,
	)

	if err != nil {
		return nil, err
	}

	if namespace != jexporter.Namespace {
		return nil, status.Error(codes.PermissionDenied, "namespace mismatch")
	}

	return jexporter, nil
}
