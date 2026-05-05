/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package identity carries the OIDC-derived caller identity through the
// admin RPC pipeline. The Identity is set by the admin AuthN interceptor
// and read by AuthZ, the impersonation client wrapper, and per-resource
// service handlers (e.g. for owner-annotation stamping).
package identity

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"
)

// Identity is the OIDC subject the admin pipeline acts on behalf of.
type Identity struct {
	// Issuer is the JWT iss claim (e.g. "https://dex.example.com/").
	Issuer string
	// Subject is the JWT sub claim (a stable per-user identifier within an issuer).
	Subject string
	// Username is the human-readable name resolved from the configured
	// JWTAuthenticator.ClaimMappings.Username (often "<prefix>:<sub>" or the
	// "preferred_username" claim).
	Username string
	// Groups is the optional list of groups derived from the JWT.
	Groups []string
	// Email is the optional email claim, used for display only.
	Email string
}

// OwnerHash returns the stable per-identity hash stamped onto Jumpstarter
// CRDs as the jumpstarter.dev/owner annotation. The hash is sha256("<iss>#<sub>")
// truncated to 16 lowercase hex characters: it is unique across issuers, hides
// PII, and survives username changes within an issuer.
func (i Identity) OwnerHash() string {
	if i.Issuer == "" || i.Subject == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(i.Issuer + "#" + i.Subject))
	return hex.EncodeToString(sum[:])[:16]
}

// String returns a redacted display form for logs. Subject and Issuer are
// retained as they identify the caller; Email is omitted.
func (i Identity) String() string {
	return strings.Join([]string{i.Username, "(", i.Issuer, "/", i.Subject, ")"}, "")
}

type ctxKey struct{}

// WithIdentity returns a copy of ctx that carries the given Identity.
func WithIdentity(ctx context.Context, id Identity) context.Context {
	return context.WithValue(ctx, ctxKey{}, id)
}

// FromContext returns the Identity placed on ctx by the admin AuthN
// interceptor, or the zero Identity when none is present.
func FromContext(ctx context.Context) (Identity, bool) {
	id, ok := ctx.Value(ctxKey{}).(Identity)
	return id, ok
}

// MustFromContext panics when no Identity has been attached. Use only inside
// admin RPC handlers, which must always run after the AuthN interceptor.
func MustFromContext(ctx context.Context) Identity {
	id, ok := FromContext(ctx)
	if !ok {
		panic("admin pipeline invariant: identity missing from context")
	}
	return id
}

// Annotation keys the admin pipeline stamps onto every CRD it mutates.
const (
	OwnerAnnotation       = "jumpstarter.dev/owner"
	CreatedByAnnotation   = "jumpstarter.dev/created-by"
	OwnerIssuerAnnotation = "jumpstarter.dev/owner-issuer"
)
