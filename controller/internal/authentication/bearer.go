package authentication

import (
	"context"
	"strings"

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

	return b.auth.AuthenticateToken(ctx, token)
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
