/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package auth wires the admin pipeline AuthN interceptor on top of the
// existing multi-issuer authenticator.Token built by
// internal/config.LoadAuthenticationConfiguration. After the upstream
// authenticator validates the JWT, this package parses the (already
// trusted) claims to recover iss/sub/email and surfaces them via the
// identity package.
package auth

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"strings"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"k8s.io/apiserver/pkg/authentication/authenticator"
)

// MultiIssuerAuthenticator validates a bearer token via the configured set
// of OIDC issuers (the existing internal authenticator.Token, which already
// unions every JWTAuthenticator from AuthenticationConfiguration), then
// extracts iss/sub/email from the validated JWT claims to build an
// identity.Identity for downstream interceptors and handlers.
type MultiIssuerAuthenticator struct {
	bearer *authentication.BearerTokenAuthenticator
}

func NewMultiIssuerAuthenticator(bearer *authentication.BearerTokenAuthenticator) *MultiIssuerAuthenticator {
	return &MultiIssuerAuthenticator{bearer: bearer}
}

// Authenticate validates ctx's bearer token, parses the trusted JWT claims,
// and returns ctx augmented with an identity.Identity. It is the underlying
// primitive used by both the unary and stream interceptors.
func (m *MultiIssuerAuthenticator) Authenticate(ctx context.Context) (context.Context, error) {
	token, err := authentication.BearerTokenFromContext(ctx)
	if err != nil {
		return ctx, err
	}

	resp, ok, err := m.bearer.AuthenticateContext(ctx)
	if err != nil {
		return ctx, status.Errorf(codes.Unauthenticated, "token rejected: %v", err)
	}
	if !ok || resp == nil || resp.User == nil {
		return ctx, status.Errorf(codes.Unauthenticated, "no issuer accepted the token")
	}

	id := identityFromAuthResponse(resp, token)
	return identity.WithIdentity(ctx, id), nil
}

// UnaryServerInterceptor returns a gRPC unary interceptor that authenticates
// the call before invoking the handler.
func (m *MultiIssuerAuthenticator) UnaryServerInterceptor() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		newCtx, err := m.Authenticate(ctx)
		if err != nil {
			return nil, err
		}
		return handler(newCtx, req)
	}
}

// StreamServerInterceptor returns a gRPC stream interceptor that
// authenticates the call before passing the stream through.
func (m *MultiIssuerAuthenticator) StreamServerInterceptor() grpc.StreamServerInterceptor {
	return func(srv any, ss grpc.ServerStream, info *grpc.StreamServerInfo, handler grpc.StreamHandler) error {
		newCtx, err := m.Authenticate(ss.Context())
		if err != nil {
			return err
		}
		return handler(srv, &authedStream{ServerStream: ss, ctx: newCtx})
	}
}

type authedStream struct {
	grpc.ServerStream
	ctx context.Context
}

func (a *authedStream) Context() context.Context { return a.ctx }

// identityFromAuthResponse derives an Identity from the (validated) authenticator
// response and the original raw JWT. The bearer/koidc authenticator only
// surfaces user.Info — it does not expose iss/sub directly — so we parse the
// JWT claims here. This is safe because the token has already been
// signature-, audience-, and expiry-validated.
func identityFromAuthResponse(resp *authenticator.Response, rawJWT string) identity.Identity {
	id := identity.Identity{
		Username: resp.User.GetName(),
		Groups:   resp.User.GetGroups(),
	}
	if claims, err := unsafeDecodeJWTClaims(rawJWT); err == nil {
		if v, ok := claims["iss"].(string); ok {
			id.Issuer = v
		}
		if v, ok := claims["sub"].(string); ok {
			id.Subject = v
		}
		if v, ok := claims["email"].(string); ok {
			id.Email = v
		}
	}
	// Internal/service-account tokens have no iss/sub claim shape; surface a
	// stable owner key derived from the upstream username so downstream
	// owner-stamping still produces a non-empty value.
	if id.Issuer == "" && id.Subject == "" && id.Username != "" {
		id.Issuer = "internal"
		id.Subject = id.Username
	}
	return id
}

// unsafeDecodeJWTClaims parses the claims segment of a compact JWS without
// re-verifying signature/expiry. The caller MUST have already validated the
// token via the upstream authenticator. This is used purely to recover
// claims that authenticator.Response does not surface.
func unsafeDecodeJWTClaims(rawJWT string) (map[string]any, error) {
	parts := strings.Split(rawJWT, ".")
	if len(parts) < 2 {
		return nil, errMalformedJWT
	}
	body, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, err
	}
	var claims map[string]any
	if err := json.Unmarshal(body, &claims); err != nil {
		return nil, err
	}
	return claims, nil
}

var errMalformedJWT = status.Errorf(codes.Internal, "validated JWT is malformed")
