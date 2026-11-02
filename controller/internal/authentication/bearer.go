package authentication

import (
	"context"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"k8s.io/apiserver/pkg/authentication/authenticator"
)

var _ = ContextAuthenticator(&BearerTokenAuthenticator{})

type BearerTokenAuthenticator struct {
	auth authenticator.Token
}

func NewBearerTokenAuthenticator(auth authenticator.Token) *BearerTokenAuthenticator {
	return &BearerTokenAuthenticator{auth: auth}
}

func (b *BearerTokenAuthenticator) AuthenticateContext(ctx context.Context) (*authenticator.Response, bool, error) {
	token, err := BearerTokenFromContext(ctx)
	if err != nil {
		return nil, false, err
	}

	resp, ok, err := b.auth.AuthenticateToken(ctx, token)
	if err != nil {
		return nil, false, authenticationError(token, err, time.Now())
	}
	return resp, ok, nil
}

// authenticationError assigns a gRPC code only when the cause is certain, and
// keeps the authenticator's message, which clients match on. Other failures,
// such as an issuer whose signing keys cannot be fetched, stay UNKNOWN.
func authenticationError(token string, err error, now time.Time) error {
	if _, ok := status.FromError(err); ok {
		return err
	}
	// Retrying or waiting cannot make an expired token valid.
	if tokenExpired(token, now) {
		return status.Error(codes.Unauthenticated, err.Error())
	}
	// The issuer's discovery has not completed yet, typically just after a controller restart.
	if strings.Contains(err.Error(), "oidc: authenticator not initialized") {
		return status.Error(codes.Unavailable, err.Error())
	}
	return err
}

// tokenExpired applies the OIDC verifier's expiry rule to the unverified claims.
// The caller sent these claims, so reporting them reveals nothing.
func tokenExpired(token string, now time.Time) bool {
	var claims jwt.RegisteredClaims
	if _, _, err := jwt.NewParser().ParseUnverified(token, &claims); err != nil || claims.ExpiresAt == nil {
		return false
	}
	return claims.ExpiresAt.Before(now)
}

func BearerTokenFromContext(ctx context.Context) (string, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return "", status.Errorf(codes.InvalidArgument, "missing metadata")
	}

	authorizations := md.Get("authorization")

	if len(authorizations) < 1 {
		return "", status.Errorf(codes.Unauthenticated, "missing authorization header")
	}

	// Reference: https://www.rfc-editor.org/rfc/rfc7230#section-3.2.2
	// A sender MUST NOT generate multiple header fields with the same field name in a message
	if len(authorizations) > 1 {
		return "", status.Errorf(codes.InvalidArgument, "multiple authorization headers")
	}

	// Invariant: len(authorizations) == 1
	authorization := authorizations[0]

	// Reference: https://github.com/golang-jwt/jwt/blob/62e504c2/request/extractor.go#L93
	if len(authorization) < 7 || !strings.EqualFold(authorization[:7], "Bearer ") {
		return "", status.Errorf(codes.InvalidArgument, "malformed authorization header")
	}

	// Invariant: len(authorization) >= 7
	token := authorization[7:]

	return token, nil
}
