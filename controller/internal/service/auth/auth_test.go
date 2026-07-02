package auth

import (
	"bytes"
	"context"
	"fmt"
	"net"
	"strings"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"google.golang.org/grpc/codes"
	grpcpeer "google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	ctrlzap "sigs.k8s.io/controller-runtime/pkg/log/zap"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

// ---------------------------------------------------------------------------
// Test stubs (mirrors the pattern from internal/oidc/token_test.go)
// ---------------------------------------------------------------------------

type stubAuthenticator struct {
	resp *authenticator.Response
	ok   bool
	err  error
}

func (s *stubAuthenticator) AuthenticateContext(_ context.Context) (*authenticator.Response, bool, error) {
	return s.resp, s.ok, s.err
}

type stubAttributesGetter struct {
	attrs authorizer.Attributes
	err   error
}

func (s *stubAttributesGetter) ContextAttributes(_ context.Context, _ user.Info) (authorizer.Attributes, error) {
	return s.attrs, s.err
}

type stubAuthorizer struct {
	decision authorizer.Decision
	reason   string
	err      error
}

func (s *stubAuthorizer) Authorize(_ context.Context, _ authorizer.Attributes) (authorizer.Decision, string, error) {
	return s.decision, s.reason, s.err
}

// fakeAddr implements net.Addr for injecting peer addresses into context.
type fakeAddr struct {
	network string
	addr    string
}

func (a fakeAddr) Network() string { return a.network }
func (a fakeAddr) String() string  { return a.addr }

// ctxWithPeer creates a context with a gRPC peer carrying the given address.
func ctxWithPeer(addr string) context.Context {
	return grpcpeer.NewContext(context.Background(), &grpcpeer.Peer{
		Addr: fakeAddr{network: "tcp", addr: addr},
	})
}

// captureLog sets up a buffer-backed logger and returns the buffer and a
// context enriched with that logger. The caller can inspect buf.String()
// after the code under test runs. It does NOT mutate the global logf.Log,
// so tests are isolated from each other and safe for t.Parallel().
func captureLog(t *testing.T, ctx context.Context) (context.Context, *bytes.Buffer) {
	t.Helper()
	var buf bytes.Buffer
	logger := ctrlzap.New(ctrlzap.UseDevMode(true), ctrlzap.WriteTo(&buf))
	return logf.IntoContext(ctx, logger), &buf
}

// peerAddrUnknown is the expected return value when PeerAddr cannot determine
// the remote IP (no peer, nil Addr, unparsable address, etc.).
const peerAddrUnknown = "unknown"

// ---------------------------------------------------------------------------
// PeerAddr tests
// ---------------------------------------------------------------------------

func TestPeerAddr(t *testing.T) {
	tests := []struct {
		name     string
		ctx      context.Context
		expected string
	}{
		{
			name:     "no peer in context returns unknown",
			ctx:      context.Background(),
			expected: peerAddrUnknown,
		},
		{
			name:     "peer with host:port returns host only",
			ctx:      ctxWithPeer("10.0.0.5:43210"),
			expected: "10.0.0.5",
		},
		{
			name:     "peer with IPv6 [host]:port returns host only",
			ctx:      ctxWithPeer("[::1]:8080"),
			expected: "::1",
		},
		{
			name:     "peer with bare address (no port) returns unknown",
			ctx:      ctxWithPeer("no-port-here"),
			expected: peerAddrUnknown,
		},
		{
			name:     "peer with empty address returns unknown",
			ctx:      ctxWithPeer(""),
			expected: peerAddrUnknown,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := PeerAddr(tc.ctx)
			if got != tc.expected {
				t.Errorf("PeerAddr() = %q, want %q", got, tc.expected)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// helpers for building Auth with known CRD objects
// ---------------------------------------------------------------------------

func buildScheme() *runtime.Scheme {
	scheme := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(scheme)
	return scheme
}

func newFakeClient(objs ...kclient.Object) kclient.Client {
	return fake.NewClientBuilder().
		WithScheme(buildScheme()).
		WithObjects(objs...).
		Build()
}

func newAuth(authn *stubAuthenticator, authz *stubAuthorizer, attr *stubAttributesGetter, objs ...kclient.Object) *Auth {
	return NewAuth(newFakeClient(objs...), authn, authz, attr)
}

// ---------------------------------------------------------------------------
// AuthClient logging tests
// ---------------------------------------------------------------------------

func TestAuthClient_TokenVerificationFailure_LogsPeerAndError(t *testing.T) {
	authn := &stubAuthenticator{err: fmt.Errorf("bad token")}
	attr := &stubAttributesGetter{}
	authz := &stubAuthorizer{}

	ctx := ctxWithPeer("192.168.1.10:5000")
	ctx, buf := captureLog(t, ctx)

	a := newAuth(authn, authz, attr)
	_, err := a.AuthClient(ctx, "default")
	if err == nil {
		t.Fatal("expected error, got nil")
	}

	logged := buf.String()

	// Must contain the standardised message.
	if !strings.Contains(logged, "client authentication failed") {
		t.Errorf("expected log message 'client authentication failed', got:\n%s", logged)
	}
	// Must include the peer IP.
	if !strings.Contains(logged, "192.168.1.10") {
		t.Errorf("expected peer IP '192.168.1.10' in log, got:\n%s", logged)
	}
	// Must include the error text.
	if !strings.Contains(logged, "bad token") {
		t.Errorf("expected error text 'bad token' in log, got:\n%s", logged)
	}
}

func TestAuthClient_NamespaceMismatch_LogsClientNameAndPeer(t *testing.T) {
	// Build a real Client CR so VerifyClientObjectToken succeeds.
	clientObj := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: "team-a",
			Name:      "my-client",
		},
	}

	authn := &stubAuthenticator{
		resp: &authenticator.Response{User: &user.DefaultInfo{Name: "u"}},
		ok:   true,
	}
	attr := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User:      &user.DefaultInfo{Name: "u"},
			Namespace: "team-a",
			Resource:  "Client",
			Name:      "my-client",
		},
	}
	authz := &stubAuthorizer{decision: authorizer.DecisionAllow}

	a := newAuth(authn, authz, attr, clientObj)

	ctx := ctxWithPeer("10.20.30.40:9090")
	ctx, buf := captureLog(t, ctx)

	// Ask for namespace "team-b" while the client belongs to "team-a".
	_, err := a.AuthClient(ctx, "team-b")
	if err == nil {
		t.Fatal("expected namespace mismatch error, got nil")
	}

	st, ok := status.FromError(err)
	if !ok || st.Code() != codes.PermissionDenied {
		t.Errorf("expected PermissionDenied, got %v", err)
	}

	logged := buf.String()

	if !strings.Contains(logged, "client authentication failed") {
		t.Errorf("expected 'client authentication failed' in log, got:\n%s", logged)
	}
	if !strings.Contains(logged, "my-client") {
		t.Errorf("expected client name 'my-client' in log, got:\n%s", logged)
	}
	if !strings.Contains(logged, "10.20.30.40") {
		t.Errorf("expected peer IP in log, got:\n%s", logged)
	}
	if !strings.Contains(logged, "namespace mismatch") {
		t.Errorf("expected 'namespace mismatch' error in log, got:\n%s", logged)
	}
}

func TestAuthClient_Success_NoAuthFailureLog(t *testing.T) {
	clientObj := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Namespace: "ns", Name: "c"},
	}
	authn := &stubAuthenticator{
		resp: &authenticator.Response{User: &user.DefaultInfo{Name: "u"}},
		ok:   true,
	}
	attr := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User: &user.DefaultInfo{Name: "u"}, Namespace: "ns", Resource: "Client", Name: "c",
		},
	}
	authz := &stubAuthorizer{decision: authorizer.DecisionAllow}

	ctx := ctxWithPeer("10.0.0.1:1234")
	ctx, buf := captureLog(t, ctx)

	a := newAuth(authn, authz, attr, clientObj)
	client, err := a.AuthClient(ctx, "ns")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if client.Name != "c" {
		t.Errorf("expected client name 'c', got %q", client.Name)
	}

	logged := buf.String()
	if strings.Contains(logged, "authentication failed") {
		t.Errorf("successful auth should produce no failure log, got:\n%s", logged)
	}
}

// ---------------------------------------------------------------------------
// AuthExporter logging tests
// ---------------------------------------------------------------------------

func TestAuthExporter_TokenVerificationFailure_LogsPeerAndError(t *testing.T) {
	authn := &stubAuthenticator{err: fmt.Errorf("expired token")}
	attr := &stubAttributesGetter{}
	authz := &stubAuthorizer{}

	ctx := ctxWithPeer("172.16.0.100:6000")
	ctx, buf := captureLog(t, ctx)

	a := newAuth(authn, authz, attr)
	_, err := a.AuthExporter(ctx, "default")
	if err == nil {
		t.Fatal("expected error, got nil")
	}

	logged := buf.String()

	if !strings.Contains(logged, "exporter authentication failed") {
		t.Errorf("expected 'exporter authentication failed', got:\n%s", logged)
	}
	if !strings.Contains(logged, "172.16.0.100") {
		t.Errorf("expected peer IP in log, got:\n%s", logged)
	}
	if !strings.Contains(logged, "expired token") {
		t.Errorf("expected error text in log, got:\n%s", logged)
	}
}

func TestAuthExporter_NamespaceMismatch_LogsExporterNameAndPeer(t *testing.T) {
	exporterObj := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Namespace: "prod", Name: "my-exporter"},
	}

	authn := &stubAuthenticator{
		resp: &authenticator.Response{User: &user.DefaultInfo{Name: "u"}},
		ok:   true,
	}
	attr := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User: &user.DefaultInfo{Name: "u"}, Namespace: "prod", Resource: "Exporter", Name: "my-exporter",
		},
	}
	authz := &stubAuthorizer{decision: authorizer.DecisionAllow}

	ctx := ctxWithPeer("10.0.0.99:4444")
	ctx, buf := captureLog(t, ctx)

	a := newAuth(authn, authz, attr, exporterObj)

	// Ask for namespace "staging" while exporter belongs to "prod".
	_, err := a.AuthExporter(ctx, "staging")
	if err == nil {
		t.Fatal("expected namespace mismatch error, got nil")
	}

	st, ok := status.FromError(err)
	if !ok || st.Code() != codes.PermissionDenied {
		t.Errorf("expected PermissionDenied, got %v", err)
	}

	logged := buf.String()

	if !strings.Contains(logged, "exporter authentication failed") {
		t.Errorf("expected 'exporter authentication failed' in log, got:\n%s", logged)
	}
	if !strings.Contains(logged, "my-exporter") {
		t.Errorf("expected exporter name in log, got:\n%s", logged)
	}
	if !strings.Contains(logged, "10.0.0.99") {
		t.Errorf("expected peer IP in log, got:\n%s", logged)
	}
}

func TestAuthExporter_Success_NoAuthFailureLog(t *testing.T) {
	exporterObj := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Namespace: "ns", Name: "e"},
	}
	authn := &stubAuthenticator{
		resp: &authenticator.Response{User: &user.DefaultInfo{Name: "u"}},
		ok:   true,
	}
	attr := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User: &user.DefaultInfo{Name: "u"}, Namespace: "ns", Resource: "Exporter", Name: "e",
		},
	}
	authz := &stubAuthorizer{decision: authorizer.DecisionAllow}

	ctx := ctxWithPeer("10.0.0.1:1234")
	ctx, buf := captureLog(t, ctx)

	a := newAuth(authn, authz, attr, exporterObj)
	exporter, err := a.AuthExporter(ctx, "ns")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if exporter.Name != "e" {
		t.Errorf("expected exporter name 'e', got %q", exporter.Name)
	}

	logged := buf.String()
	if strings.Contains(logged, "authentication failed") {
		t.Errorf("successful auth should produce no failure log, got:\n%s", logged)
	}
}

// ---------------------------------------------------------------------------
// Auth logging: no duplicate log on error (controller-service level should not
// re-log what auth already logs)
// ---------------------------------------------------------------------------

func TestAuthClient_ErrorLogIncludesNoDuplicateTokenInMessage(t *testing.T) {
	// Ensure the error message itself doesn't echo back the token.
	// The auth layer only logs "client authentication failed" + the error
	// string from the verification layer.
	authn := &stubAuthenticator{err: fmt.Errorf("token verification failed")}
	attr := &stubAttributesGetter{}
	authz := &stubAuthorizer{}

	ctx := ctxWithPeer("10.0.0.1:1234")
	ctx, buf := captureLog(t, ctx)

	a := newAuth(authn, authz, attr)
	_, _ = a.AuthClient(ctx, "default")

	logged := buf.String()
	// The log should never contain the word "Bearer" or raw token material.
	if strings.Contains(logged, "Bearer") {
		t.Errorf("log should not contain raw bearer prefix:\n%s", logged)
	}
}

// ---------------------------------------------------------------------------
// Token leak tests — verify that sensitive token values never appear in logs.
// Replicates the TestRouterAuthenticateNoTokenLeak pattern from
// controller_service_test.go for the auth package.
// ---------------------------------------------------------------------------

func TestAuthClient_NoTokenLeak(t *testing.T) {
	const sensitiveToken = "header.payload.signature-secret-value"

	// The stub error does NOT include the token — this mirrors the real
	// oidc.VerifyClientObjectToken which returns generic error messages.
	authn := &stubAuthenticator{err: fmt.Errorf("token verification failed")}
	attr := &stubAttributesGetter{}
	authz := &stubAuthorizer{}

	ctx := ctxWithPeer("10.0.0.1:1234")
	ctx, buf := captureLog(t, ctx)

	a := newAuth(authn, authz, attr)
	_, _ = a.AuthClient(ctx, "default")

	logged := buf.String()
	if strings.Contains(logged, sensitiveToken) {
		t.Errorf("JWT token value leaked in auth log output:\n%s", logged)
	}
}

func TestAuthExporter_NoTokenLeak(t *testing.T) {
	const sensitiveToken = "header.payload.signature-secret-value"

	authn := &stubAuthenticator{err: fmt.Errorf("token verification failed")}
	attr := &stubAttributesGetter{}
	authz := &stubAuthorizer{}

	ctx := ctxWithPeer("10.0.0.1:1234")
	ctx, buf := captureLog(t, ctx)

	a := newAuth(authn, authz, attr)
	_, _ = a.AuthExporter(ctx, "default")

	logged := buf.String()
	if strings.Contains(logged, sensitiveToken) {
		t.Errorf("JWT token value leaked in auth log output:\n%s", logged)
	}
}

// ---------------------------------------------------------------------------
// PeerAddr with nil Addr in peer (edge case)
// ---------------------------------------------------------------------------

func TestPeerAddr_NilAddr(t *testing.T) {
	ctx := grpcpeer.NewContext(context.Background(), &grpcpeer.Peer{
		Addr: nil,
	})
	// PeerAddr must not panic when p.Addr is nil and should return "unknown".
	got := PeerAddr(ctx)
	if got != peerAddrUnknown {
		t.Errorf("PeerAddr with nil Addr = %q, want %q", got, peerAddrUnknown)
	}
}

// ---------------------------------------------------------------------------
// PeerAddr with a unix socket address
// ---------------------------------------------------------------------------

func TestPeerAddr_UnixSocket(t *testing.T) {
	ctx := grpcpeer.NewContext(context.Background(), &grpcpeer.Peer{
		Addr: &net.UnixAddr{Name: "/var/run/jumpstarter.sock", Net: "unix"},
	})
	got := PeerAddr(ctx)
	// A Unix socket path should not be returned; it should return "unknown"
	// because SplitHostPort will fail on "/var/run/jumpstarter.sock".
	if got != peerAddrUnknown {
		t.Errorf("PeerAddr for unix socket = %q, want %q", got, peerAddrUnknown)
	}
}
