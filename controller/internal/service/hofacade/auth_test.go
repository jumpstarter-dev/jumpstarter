package hofacade

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/golang-jwt/jwt/v5"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authorization"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/auth"
)

// stubAuthn is a ContextAuthenticator that records the synthesized gRPC
// incoming metadata so the transport adaptation (HTTP header -> gRPC
// metadata) can be asserted, and returns a canned result.
type stubAuthn struct {
	resp   *authenticator.Response
	ok     bool
	err    error
	called bool
	md     metadata.MD
}

func (s *stubAuthn) AuthenticateContext(ctx context.Context) (*authenticator.Response, bool, error) {
	s.called = true
	s.md, _ = metadata.FromIncomingContext(ctx)
	return s.resp, s.ok, s.err
}

type allowAll struct{}

func (allowAll) Authorize(ctx context.Context, attrs authorizer.Attributes) (authorizer.Decision, string, error) {
	return authorizer.DecisionAllow, "", nil
}

func authTestScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := clientgoscheme.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	return scheme
}

func newAuthResolver(t *testing.T, authn *stubAuthn, objs ...runtime.Object) *AuthClientResolver {
	t.Helper()
	c := fake.NewClientBuilder().WithScheme(authTestScheme(t)).WithRuntimeObjects(objs...).Build()
	// Real MetadataAttributesGetter with the exact keys cmd/main.go:280-284
	// configures, so the full name-derivation path is exercised.
	attr := authorization.NewMetadataAttributesGetter(authorization.MetadataAttributesGetterConfig{
		NamespaceKey: "jumpstarter-namespace",
		ResourceKey:  "jumpstarter-kind",
		NameKey:      "jumpstarter-name",
	})
	return &AuthClientResolver{
		Auth:      auth.NewAuth(c, authn, allowAll{}, attr),
		Namespace: "testns",
	}
}

func errBody(t *testing.T, err error) string {
	t.Helper()
	appErr, ok := err.(*AppError)
	if !ok {
		t.Fatalf("expected *AppError, got %T: %v", err, err)
	}
	b, merr := json.Marshal(appErr.JSONResponse())
	if merr != nil {
		t.Fatal(merr)
	}
	return string(b)
}

// Test 32a: a missing Authorization header is rejected with 401 without ever
// invoking the authenticator (no work for unauthenticated requests).
func TestAuthResolverMissingHeader(t *testing.T) {
	authn := &stubAuthn{}
	res := newAuthResolver(t, authn)
	_, err := res.Resolve(httptest.NewRequest(http.MethodGet, "/cvds", nil))
	if err == nil {
		t.Fatal("expected error")
	}
	appErr, ok := err.(*AppError)
	if !ok || appErr.StatusCode != http.StatusUnauthorized {
		t.Errorf("want 401 AppError, got %v", err)
	}
	if authn.called {
		t.Error("authenticator invoked despite missing Authorization header")
	}
}

// Test 32b: the resolver synthesizes gRPC incoming metadata carrying the
// verbatim Authorization header plus jumpstarter-namespace and
// jumpstarter-kind=Client; jumpstarter-name is omitted for external OIDC
// tokens so MetadataAttributesGetter derives it (preserving JIT provisioning).
func TestAuthResolverMetadataInjection(t *testing.T) {
	authn := &stubAuthn{err: status.Error(codes.Unauthenticated, "bad token")}
	res := newAuthResolver(t, authn)
	req := httptest.NewRequest(http.MethodGet, "/cvds", nil)
	req.Header.Set("Authorization", "Bearer opaque-oidc-token")
	_, _ = res.Resolve(req)

	if !authn.called {
		t.Fatal("authenticator not invoked")
	}
	if got := authn.md.Get("authorization"); len(got) != 1 || got[0] != "Bearer opaque-oidc-token" {
		t.Errorf("authorization metadata: got %v", got)
	}
	if got := authn.md.Get("jumpstarter-namespace"); len(got) != 1 || got[0] != "testns" {
		t.Errorf("jumpstarter-namespace metadata: got %v", got)
	}
	if got := authn.md.Get("jumpstarter-kind"); len(got) != 1 || got[0] != "Client" {
		t.Errorf("jumpstarter-kind metadata: got %v", got)
	}
	if got := authn.md.Get("jumpstarter-name"); len(got) != 0 {
		t.Errorf("jumpstarter-name must be omitted for opaque tokens, got %v", got)
	}
}

// Test 32c: every auth-failure gRPC code maps to a byte-identical 401 body —
// no existence oracle distinguishing bad token, wrong namespace, or missing
// Client CR. The full five-code 401 list is pinned (including the deviation
// beyond the plan's original three): InvalidArgument covers malformed
// Authorization headers (bearer.go:55) and Unknown covers plain authenticator
// failures ("failed to authenticate token", token.go:53) — dropping either
// back to 500 would turn ordinary bad tokens into internal errors.
func TestAuthResolverFailureCodeMapping(t *testing.T) {
	req := func() *http.Request {
		r := httptest.NewRequest(http.MethodGet, "/cvds", nil)
		r.Header.Set("Authorization", "Bearer tok")
		return r
	}

	var bodies []string
	for _, tc := range []struct {
		name string
		err  error
	}{
		{"unauthenticated", status.Error(codes.Unauthenticated, "bad token")},
		{"permission denied", status.Error(codes.PermissionDenied, "namespace mismatch")},
		{"not found", status.Error(codes.NotFound, `client "testns/alice" not found`)},
		{"invalid argument", status.Error(codes.InvalidArgument, "invalid authorization header")},
		{"unknown", status.Error(codes.Unknown, "failed to authenticate token")},
	} {
		authn := &stubAuthn{err: tc.err}
		res := newAuthResolver(t, authn)
		_, err := res.Resolve(req())
		if err == nil {
			t.Fatalf("%s: expected error", tc.name)
		}
		appErr, ok := err.(*AppError)
		if !ok || appErr.StatusCode != http.StatusUnauthorized {
			t.Errorf("%s: want 401, got %v", tc.name, err)
			continue
		}
		bodies = append(bodies, errBody(t, err))
	}
	for i := 1; i < len(bodies); i++ {
		if bodies[i] != bodies[0] {
			t.Errorf("401 bodies differ (existence oracle): %s vs %s", bodies[0], bodies[i])
		}
	}

	// Codes outside the auth-shaped set stay 500: infrastructure failures
	// must not masquerade as bad credentials.
	for _, tc := range []struct {
		name string
		err  error
	}{
		{"internal", status.Error(codes.Internal, "boom")},
		{"unavailable", status.Error(codes.Unavailable, "apiserver down")},
	} {
		authn := &stubAuthn{err: tc.err}
		res := newAuthResolver(t, authn)
		_, err := res.Resolve(req())
		appErr, ok := err.(*AppError)
		if !ok || appErr.StatusCode != http.StatusInternalServerError {
			t.Errorf("%s: want 500 AppError, got %v", tc.name, err)
		}
	}
}

// Success path: an external OIDC identity resolves through the real
// MetadataAttributesGetter (name derived from the username) to the caller's
// own Client CR.
func TestAuthResolverSuccess(t *testing.T) {
	authn := &stubAuthn{
		resp: &authenticator.Response{User: &user.DefaultInfo{Name: "dex:alice"}},
		ok:   true,
	}
	res := newAuthResolver(t, authn, &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Name: "alice", Namespace: "testns"},
	})
	req := httptest.NewRequest(http.MethodGet, "/cvds", nil)
	req.Header.Set("Authorization", "Bearer tok")
	jclient, err := res.Resolve(req)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if jclient.Name != "alice" {
		t.Errorf("resolved client: got %q want alice", jclient.Name)
	}
}

// Internal client tokens are JWTs with sub "client:<ns>:<name>:<uid>" and the
// MetadataAttributesGetter requires an explicit name for them
// (metadata.go:123-129), so the resolver supplies a jumpstarter-name hint
// parsed (unverified) from the token; authenticity is still enforced by the
// authenticator + BasicAuthorizer.
func TestAuthResolverInternalTokenNameHint(t *testing.T) {
	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{
		"sub": "client:testns:alice:uid-1",
	}).SignedString([]byte("test-only"))
	if err != nil {
		t.Fatal(err)
	}

	authn := &stubAuthn{
		resp: &authenticator.Response{User: &user.DefaultInfo{Name: "internal:client:testns:alice:uid-1"}},
		ok:   true,
	}
	res := newAuthResolver(t, authn, &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Name: "alice", Namespace: "testns"},
	})
	req := httptest.NewRequest(http.MethodGet, "/cvds", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	jclient, err := res.Resolve(req)
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if jclient.Name != "alice" {
		t.Errorf("resolved client: got %q want alice", jclient.Name)
	}
	if got := authn.md.Get("jumpstarter-name"); len(got) != 1 || got[0] != "alice" {
		t.Errorf("jumpstarter-name hint: got %v want [alice]", got)
	}
}

// Test 32d: the static dev-token resolver resolves a seeded Client CR and
// rejects unknown tokens and absent CRs with the same uniform 401.
func TestStaticClientResolver(t *testing.T) {
	c := fake.NewClientBuilder().WithScheme(authTestScheme(t)).WithRuntimeObjects(
		&jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: "alice", Namespace: "testns"}},
	).Build()
	res := NewStaticClientResolver(c, "testns", map[string]string{
		"tok-a":    "alice",
		"tok-gone": "ghost",
	})

	req := func(hdr string) *http.Request {
		r := httptest.NewRequest(http.MethodGet, "/cvds", nil)
		if hdr != "" {
			r.Header.Set("Authorization", hdr)
		}
		return r
	}

	jclient, err := res.Resolve(req("Bearer tok-a"))
	if err != nil || jclient.Name != "alice" {
		t.Errorf("known token: got (%v, %v), want alice", jclient, err)
	}

	// Missing header, unknown token, and known token with absent CR: all 401.
	for _, hdr := range []string{"", "Bearer nope", "Bearer tok-gone"} {
		_, err := res.Resolve(req(hdr))
		appErr, ok := err.(*AppError)
		if !ok || appErr.StatusCode != http.StatusUnauthorized {
			t.Errorf("header %q: want 401 AppError, got %v", hdr, err)
		}
	}
}

// --insecure-dev-anonymous-client: a request with NO Authorization header
// resolves to the configured Client CR (the unchanged Python driver sends no
// auth header), while a present-but-unknown token is still a 401 — the
// anonymous mapping is never a fallback for bad credentials.
func TestStaticClientResolverAnonymous(t *testing.T) {
	c := fake.NewClientBuilder().WithScheme(authTestScheme(t)).WithRuntimeObjects(
		&jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: "alice", Namespace: "testns"}},
	).Build()
	res := NewStaticClientResolver(c, "testns", map[string]string{"tok-a": "alice"})
	res.AnonymousClient = "alice"

	jclient, err := res.Resolve(httptest.NewRequest(http.MethodGet, "/cvds", nil))
	if err != nil || jclient.Name != "alice" {
		t.Errorf("anonymous request: got (%v, %v), want alice", jclient, err)
	}

	req := httptest.NewRequest(http.MethodGet, "/cvds", nil)
	req.Header.Set("Authorization", "Bearer nope")
	_, err = res.Resolve(req)
	if appErr, ok := err.(*AppError); !ok || appErr.StatusCode != http.StatusUnauthorized {
		t.Errorf("unknown token must stay 401 even with an anonymous mapping, got %v", err)
	}

	// Anonymous mapping to an absent CR is the same uniform 401.
	res.AnonymousClient = "ghost"
	if _, err := res.Resolve(httptest.NewRequest(http.MethodGet, "/cvds", nil)); err == nil {
		t.Error("anonymous mapping to absent CR must 401")
	}
}
