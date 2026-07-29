package auth

import (
	"context"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authorization"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
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

// VerifyExporter authenticates the exporter token in ctx and returns the
// matching Exporter object without enforcing a namespace.
func (s *Auth) VerifyExporter(ctx context.Context) (*jumpstarterdevv1alpha1.Exporter, error) {
	jexporter, err := oidc.VerifyExporterObjectToken(
		ctx,
		s.authn,
		s.authz,
		s.attr,
		s.client,
	)

	if err != nil {
		log.FromContext(ctx).Info("exporter authentication failed", "error", err.Error())
		return nil, err
	}

	return jexporter, nil
}

// IsExporter checks the jumpstarter-kind metadata to determine if the caller
// is an exporter.
func (s *Auth) IsExporter(ctx context.Context) bool {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return false
	}
	kinds := md.Get("jumpstarter-kind")
	return len(kinds) == 1 && kinds[0] == "Exporter"
}

func (s *Auth) AuthExporter(ctx context.Context, namespace string) (*jumpstarterdevv1alpha1.Exporter, error) {
	jexporter, err := s.VerifyExporter(ctx)
	if err != nil {
		return nil, err
	}

	if namespace != jexporter.Namespace {
		return nil, status.Error(codes.PermissionDenied, "namespace mismatch")
	}

	return jexporter, nil
}
