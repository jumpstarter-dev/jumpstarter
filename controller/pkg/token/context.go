package token

import (
	"context"
	"strings"

	"github.com/golang-jwt/jwt/v5"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

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
	return authorization[7:], nil
}

type PtrClaims[T any] interface {
	jwt.Claims
	*T
}

func ParseWithClaims[T jwt.Claims, PT PtrClaims[T]](token string, psk string, iss string, aud string) (*T, error) {
	var empty T

	parsed, err := jwt.ParseWithClaims(token, PT(&empty),
		func(t *jwt.Token) (interface{}, error) {
			return []byte(psk), nil
		},
		jwt.WithValidMethods([]string{
			jwt.SigningMethodHS256.Alg(),
			jwt.SigningMethodHS384.Alg(),
			jwt.SigningMethodHS512.Alg(),
		}),
		jwt.WithIssuer(iss),
		jwt.WithAudience(aud),
		jwt.WithExpirationRequired(),
	)
	if err != nil || !parsed.Valid {
		return nil, status.Errorf(codes.PermissionDenied, "unable to validate jwt token")
	}

	claims, ok := parsed.Claims.(PT)
	if !ok {
		return nil, status.Errorf(codes.Internal, "unable to parse jwt claims")
	}

	return claims, nil
}
