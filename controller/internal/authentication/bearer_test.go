package authentication

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
)

type stubTokenAuthenticator struct {
	resp *authenticator.Response
	ok   bool
	err  error
}

func (s stubTokenAuthenticator) AuthenticateToken(context.Context, string) (*authenticator.Response, bool, error) {
	return s.resp, s.ok, s.err
}

func unsignedToken(t *testing.T, claims jwt.MapClaims) string {
	t.Helper()
	token, err := jwt.NewWithClaims(jwt.SigningMethodNone, claims).SignedString(jwt.UnsafeAllowNoneSignatureType)
	if err != nil {
		t.Fatal(err)
	}
	return token
}

func TestAuthenticateContextErrorCodes(t *testing.T) {
	now := time.Now()
	expired := unsignedToken(t, jwt.MapClaims{"exp": now.Add(-time.Minute).Unix()})
	current := unsignedToken(t, jwt.MapClaims{"exp": now.Add(time.Hour).Unix()})
	noExpiry := unsignedToken(t, jwt.MapClaims{"sub": "exporter"})
	notInitialized := errors.New("oidc: authenticator not initialized")
	keysUnavailable := errors.New("oidc: verify token: failed to verify signature: fetching keys oidc: get keys failed")

	tests := []struct {
		name  string
		token string
		err   error
		code  codes.Code
	}{
		{"expired token", expired, errors.New("oidc: verify token: oidc: token is expired"), codes.Unauthenticated},
		{"expired token before discovery", expired, notInitialized, codes.Unauthenticated},
		{"discovery pending", current, notInitialized, codes.Unavailable},
		{"keys unavailable", current, keysUnavailable, codes.Unknown},
		{"no expiry claim", noExpiry, keysUnavailable, codes.Unknown},
		{"not a JWT", "opaque", errors.New("rejected"), codes.Unknown},
		{"existing status", expired, status.Error(codes.PermissionDenied, "denied"), codes.PermissionDenied},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			auth := NewBearerTokenAuthenticator(stubTokenAuthenticator{err: tc.err})
			ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("authorization", "Bearer "+tc.token))
			resp, ok, err := auth.AuthenticateContext(ctx)
			if resp != nil || ok {
				t.Fatalf("rejected token authenticated: %v %v", resp, ok)
			}
			if code := status.Code(err); code != tc.code {
				t.Fatalf("code = %v, want %v (%v)", code, tc.code, err)
			}
			// Clients recognize an expired token by the authenticator's message.
			if got, want := status.Convert(err).Message(), status.Convert(tc.err).Message(); got != want {
				t.Fatalf("message = %q, want %q", got, want)
			}
		})
	}
}

func TestAuthenticateContextPassesThroughResult(t *testing.T) {
	want := &authenticator.Response{User: &user.DefaultInfo{Name: "exporter"}}
	for _, ok := range []bool{true, false} {
		auth := NewBearerTokenAuthenticator(stubTokenAuthenticator{resp: want, ok: ok})
		ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("authorization", "Bearer token"))
		resp, gotOK, err := auth.AuthenticateContext(ctx)
		if err != nil || gotOK != ok || resp != want {
			t.Fatalf("AuthenticateContext = %v, %v, %v; want %v, %v, nil", resp, gotOK, err, want, ok)
		}
	}
}
