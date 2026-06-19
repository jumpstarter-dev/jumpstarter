package auth

import (
	"context"
	"net"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authorization"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// peerAddr returns the remote IP address from the gRPC peer info stored in ctx,
// or "unknown" if unavailable.
func peerAddr(ctx context.Context) string {
	p, ok := peer.FromContext(ctx)
	if !ok {
		return "unknown"
	}
	host, _, err := net.SplitHostPort(p.Addr.String())
	if err != nil {
		return p.Addr.String()
	}
	return host
}

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
	logger := log.FromContext(ctx).WithValues("peer", peerAddr(ctx))

	jclient, err := oidc.VerifyClientObjectToken(
		ctx,
		s.authn,
		s.authz,
		s.attr,
		s.client,
	)

	if err != nil {
		logger.Info("client authentication failed", "error", err.Error())
		return nil, err
	}

	if namespace != jclient.Namespace {
		err := status.Error(codes.PermissionDenied, "namespace mismatch")
		logger.Info("client authentication failed", "client", jclient.Name, "error", err.Error())
		return nil, err
	}

	return jclient, nil
}

func (s *Auth) AuthExporter(ctx context.Context, namespace string) (*jumpstarterdevv1alpha1.Exporter, error) {
	logger := log.FromContext(ctx).WithValues("peer", peerAddr(ctx))

	jexporter, err := oidc.VerifyExporterObjectToken(
		ctx,
		s.authn,
		s.authz,
		s.attr,
		s.client,
	)

	if err != nil {
		logger.Info("exporter authentication failed", "error", err.Error())
		return nil, err
	}

	if namespace != jexporter.Namespace {
		err := status.Error(codes.PermissionDenied, "namespace mismatch")
		logger.Info("exporter authentication failed", "exporter", jexporter.Name, "error", err.Error())
		return nil, err
	}

	return jexporter, nil
}
