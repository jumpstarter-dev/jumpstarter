package hofacade

import (
	"crypto/subtle"
	"net/http"
	"strings"

	"github.com/golang-jwt/jwt/v5"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/auth"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/hofacade/howire"
)

// msgUnauthorized is the single body every authentication failure produces:
// missing header, bad token, wrong namespace, and nonexistent Client CR are
// byte-identical on the wire — no existence oracle.
const msgUnauthorized = "Unauthorized"

// ClientResolver resolves an HTTP request to the caller's own Client CR.
// It is the facade's identity seam (F9/F13): production uses
// AuthClientResolver; --insecure-dev-token swaps in StaticClientResolver.
type ClientResolver interface {
	Resolve(r *http.Request) (*jumpstarterdevv1alpha1.Client, error)
}

// ResolverFunc adapts a function to ClientResolver.
type ResolverFunc func(r *http.Request) (*jumpstarterdevv1alpha1.Client, error)

func (f ResolverFunc) Resolve(r *http.Request) (*jumpstarterdevv1alpha1.Client, error) {
	return f(r)
}

// AuthClientResolver reuses the controller's existing gRPC authentication
// stack verbatim (auth.Auth.AuthClient, internal/service/auth/auth.go:61 —
// token union authenticator, BasicAuthorizer, MetadataAttributesGetter, JIT
// provisioning) by synthesizing gRPC incoming metadata from the HTTP request.
// The facade holds no god-credential: every request acts as the caller's own
// Client CR.
type AuthClientResolver struct {
	Auth      *auth.Auth
	Namespace string
}

func (a *AuthClientResolver) Resolve(r *http.Request) (*jumpstarterdevv1alpha1.Client, error) {
	hdr := r.Header.Get("Authorization")
	if hdr == "" {
		return nil, NewUnauthorizedError(msgUnauthorized)
	}

	// The same metadata keys cmd/main.go:280-284 configures. jumpstarter-name
	// is normally omitted so MetadataAttributesGetter derives the Client name
	// from the OIDC username (metadata.go:130-147), preserving JIT
	// provisioning. Internal client tokens are the exception: the getter
	// requires an explicit name for them (metadata.go:123-129), so we supply
	// a hint parsed (UNVERIFIED) from the token's subject. The hint is safe:
	// authenticity is still enforced by the token union authenticator, and
	// BasicAuthorizer only allows when the authenticated username appears in
	// the named Client's Usernames() — a wrong name simply fails authz.
	pairs := []string{
		"authorization", hdr,
		"jumpstarter-namespace", a.Namespace,
		"jumpstarter-kind", "Client",
	}
	if name, ok := internalSubjectClientName(hdr); ok {
		pairs = append(pairs, "jumpstarter-name", name)
	}
	ctx := metadata.NewIncomingContext(r.Context(), metadata.Pairs(pairs...))

	jclient, err := a.Auth.AuthClient(ctx, a.Namespace)
	if err != nil {
		switch status.Code(err) {
		case codes.Unauthenticated, codes.PermissionDenied, codes.NotFound,
			codes.InvalidArgument, codes.Unknown:
			// All auth-shaped failures collapse to one uniform 401.
			// InvalidArgument covers malformed authorization headers
			// (bearer.go:55) and Unknown covers plain authenticator errors
			// ("failed to authenticate token", token.go:53).
			return nil, NewUnauthorizedError(msgUnauthorized)
		default:
			return nil, NewInternalError(howire.MsgInternal, err)
		}
	}
	return jclient, nil
}

// internalSubjectClientName best-effort parses a bearer header as a JWT and,
// when its (unverified) subject has the internal-token shape
// "client:<ns>:<name>:<uid>" (client_helpers.go InternalSubject), returns the
// Client name. Any parse failure means "no hint" — never an error.
func internalSubjectClientName(hdr string) (string, bool) {
	token := hdr
	if len(hdr) >= 7 && strings.EqualFold(hdr[:7], "Bearer ") {
		token = hdr[7:]
	}
	claims := jwt.MapClaims{}
	if _, _, err := jwt.NewParser().ParseUnverified(token, claims); err != nil {
		return "", false
	}
	sub, err := claims.GetSubject()
	if err != nil {
		return "", false
	}
	parts := strings.Split(sub, ":")
	if len(parts) == 4 && parts[0] == "client" && parts[2] != "" {
		return parts[2], true
	}
	return "", false
}

// StaticClientResolver maps fixed bearer tokens to Client CR names. It is an
// authentication BYPASS for local development only (--insecure-dev-token):
// default off, never inferred from the environment, no fallback chaining to
// the real authenticator.
type StaticClientResolver struct {
	Client    kclient.Client
	Namespace string
	Tokens    map[string]string
	// AnonymousClient, when non-empty, maps requests carrying NO
	// Authorization header at all to that Client CR
	// (--insecure-dev-anonymous-client). It exists solely so the UNCHANGED
	// Python driver — which sends no auth header on any route — can run the
	// manual acid test against a dev facade. A present-but-unknown token is
	// still a 401 (never the anonymous fallback).
	AnonymousClient string
}

// NewStaticClientResolver constructs the dev resolver and loudly logs that
// authentication is bypassed.
func NewStaticClientResolver(c kclient.Client, namespace string, tokens map[string]string) *StaticClientResolver {
	ctrl.Log.WithName("hofacade").Error(nil,
		"INSECURE: static dev-token client resolver enabled; OIDC/internal token authentication is BYPASSED. Never use this outside local development.",
		"tokens", len(tokens))
	return &StaticClientResolver{Client: c, Namespace: namespace, Tokens: tokens}
}

func (s *StaticClientResolver) Resolve(r *http.Request) (*jumpstarterdevv1alpha1.Client, error) {
	hdr := r.Header.Get("Authorization")
	if hdr == "" {
		if s.AnonymousClient == "" {
			return nil, NewUnauthorizedError(msgUnauthorized)
		}
		return s.getClient(r, s.AnonymousClient)
	}
	token := hdr
	if len(hdr) >= 7 && strings.EqualFold(hdr[:7], "Bearer ") {
		token = hdr[7:]
	}

	// Constant-time comparison over the whole map: no early exit on match so
	// timing does not reveal which (if any) token prefix-matched.
	var clientName string
	found := false
	for candidate, name := range s.Tokens {
		if subtle.ConstantTimeCompare([]byte(candidate), []byte(token)) == 1 {
			clientName = name
			found = true
		}
	}
	if !found {
		return nil, NewUnauthorizedError(msgUnauthorized)
	}
	return s.getClient(r, clientName)
}

func (s *StaticClientResolver) getClient(r *http.Request, clientName string) (*jumpstarterdevv1alpha1.Client, error) {
	var jclient jumpstarterdevv1alpha1.Client
	if err := s.Client.Get(r.Context(), types.NamespacedName{Namespace: s.Namespace, Name: clientName}, &jclient); err != nil {
		if apierrors.IsNotFound(err) {
			return nil, NewUnauthorizedError(msgUnauthorized)
		}
		return nil, NewInternalError(howire.MsgInternal, err)
	}
	return &jclient, nil
}
