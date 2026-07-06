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

// VerifyClient authenticates the client token in ctx and returns the matching
// Client object without enforcing a namespace. Authentication failures are
// logged via the context logger, which carries the peer address when the
// caller applied the shared log package's LogContext (as the gRPC
// interceptors do).
func (s *Auth) VerifyClient(ctx context.Context) (*jumpstarterdevv1alpha1.Client, error) {
	jclient, err := oidc.VerifyClientObjectToken(
		ctx,
		s.authn,
		s.authz,
		s.attr,
		s.client,
	)

	if err != nil {
		log.FromContext(ctx).Info("client authentication failed", "error", err.Error())
		return nil, err
	}

	return jclient, nil
}

func (s *Auth) AuthClient(ctx context.Context, namespace string) (*jumpstarterdevv1alpha1.Client, error) {
	jclient, err := s.VerifyClient(ctx)
	if err != nil {
		return nil, err
	}

	if namespace != jclient.Namespace {
		err := status.Error(codes.PermissionDenied, "namespace mismatch")
		log.FromContext(ctx).Info("client authentication failed", "client", jclient.Name, "error", err.Error())
		return nil, err
	}

	return jclient, nil
}

// VerifyExporter authenticates the exporter token in ctx and returns the
// matching Exporter object without enforcing a namespace. Authentication
// failures are logged via the context logger, which carries the peer address
// when the caller applied the shared log package's LogContext (as the gRPC
// interceptors do).
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

func (s *Auth) AuthExporter(ctx context.Context, namespace string) (*jumpstarterdevv1alpha1.Exporter, error) {
	jexporter, err := s.VerifyExporter(ctx)
	if err != nil {
		return nil, err
	}

	if namespace != jexporter.Namespace {
		err := status.Error(codes.PermissionDenied, "namespace mismatch")
		log.FromContext(ctx).Info("exporter authentication failed", "exporter", jexporter.Name, "error", err.Error())
		return nil, err
	}

	return jexporter, nil
}
