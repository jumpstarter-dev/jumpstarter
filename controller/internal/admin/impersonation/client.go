/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package impersonation produces controller-runtime clients that propagate
// the calling OIDC user's identity to the kube-apiserver via Impersonate-*
// headers. This makes kube-audit logs attribute mutations to the human
// user instead of the controller's ServiceAccount.
package impersonation

import (
	"context"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// Factory builds per-request impersonating clients sharing one base
// rest.Config and one runtime.Scheme.
type Factory struct {
	cfg    *rest.Config
	scheme client.Options
}

// NewFactory captures the controller's rest.Config and client.Options so
// per-request clients can be derived without re-discovering the API.
func NewFactory(cfg *rest.Config, opts client.Options) *Factory {
	return &Factory{cfg: cfg, scheme: opts}
}

// For returns a controller-runtime client that signs every kube request
// with the Identity from ctx as the impersonated user. If no Identity is
// present, the returned client behaves as the controller's own SA.
func (f *Factory) For(ctx context.Context) (client.Client, error) {
	id, ok := identity.FromContext(ctx)
	if !ok || id.Username == "" {
		return client.New(f.cfg, f.scheme)
	}

	cfg := rest.CopyConfig(f.cfg)
	cfg.Impersonate = rest.ImpersonationConfig{
		UserName: id.Username,
		Groups:   id.Groups,
		Extra:    map[string][]string{},
	}
	if id.Issuer != "" {
		cfg.Impersonate.Extra["iss"] = []string{id.Issuer}
	}
	if id.Subject != "" {
		cfg.Impersonate.Extra["sub"] = []string{id.Subject}
	}
	return client.New(cfg, f.scheme)
}
