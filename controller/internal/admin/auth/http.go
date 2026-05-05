/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package auth

import (
	"net/http"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/authz"
)

// HTTPMiddleware bridges the production gap where the grpc-gateway's
// HandlerServer registration variant invokes admin services in-process,
// bypassing the gRPC AuthN + AuthZ interceptor chain. Wrap gwmux with
// HTTPMiddleware before serving and admin REST callers will go through
// the same OIDC validation and SAR check as their gRPC peers.
type HTTPMiddleware struct {
	authn *MultiIssuerAuthenticator
	authz *authz.Authorizer
}

// NewHTTPMiddleware constructs the middleware with the admin pipeline's
// authenticator and authorizer. Both are required.
func NewHTTPMiddleware(authn *MultiIssuerAuthenticator, az *authz.Authorizer) *HTTPMiddleware {
	return &HTTPMiddleware{authn: authn, authz: az}
}

// Wrap returns an http.Handler that runs OIDC AuthN and SAR AuthZ before
// forwarding to next. Requests outside the admin REST surface
// ("/admin/v1/...") pass through unchanged so callers can compose the
// middleware around a mux that also serves non-admin paths.
func (m *HTTPMiddleware) Wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fullMethod, namespace, ok := AdminMethodFromHTTP(r)
		if !ok {
			// Not an admin route — let the gateway's own 404 handling
			// surface or pass to other muxes layered above this one.
			next.ServeHTTP(w, r)
			return
		}

		bearer := bearerFromHTTP(r)
		if bearer == "" {
			writeStatus(w, status.Error(codes.Unauthenticated, "missing bearer token"))
			return
		}
		// MultiIssuerAuthenticator reads the token from gRPC metadata,
		// so attach it the same way before invoking it.
		md := metadata.New(map[string]string{"authorization": "Bearer " + bearer})
		ctx := metadata.NewIncomingContext(r.Context(), md)
		ctx, err := m.authn.Authenticate(ctx)
		if err != nil {
			writeStatus(w, err)
			return
		}
		if err := m.authz.AuthorizeNamespace(ctx, fullMethod, namespace); err != nil {
			writeStatus(w, err)
			return
		}
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// AdminMethodFromHTTP recovers the gRPC FullMethod and the target
// namespace from an HTTP request whose path matches the JEP-0014 admin
// REST URL template ("/admin/v1/namespaces/{ns}/{plural}[/{id}][:watch]").
// Returns ok=false for any path outside that surface.
func AdminMethodFromHTTP(r *http.Request) (fullMethod, namespace string, ok bool) {
	const prefix = "/admin/v1/namespaces/"
	if !strings.HasPrefix(r.URL.Path, prefix) {
		return "", "", false
	}
	rest := r.URL.Path[len(prefix):]
	parts := strings.Split(rest, "/")
	if len(parts) < 2 {
		return "", "", false
	}
	namespace = parts[0]
	plural := parts[1]
	hasID := len(parts) >= 3 && parts[2] != "" && !strings.Contains(parts[2], ":")
	isWatch := strings.HasSuffix(plural, ":watch") ||
		(len(parts) >= 3 && strings.HasSuffix(parts[2], ":watch"))
	plural = strings.TrimSuffix(plural, ":watch")

	service, singular, ok := serviceForPlural(plural)
	if !ok {
		return "", "", false
	}

	var rpc string
	switch r.Method {
	case http.MethodGet:
		switch {
		case isWatch:
			rpc = "Watch" + capitalize(plural)
		case hasID:
			rpc = "Get" + singular
		default:
			rpc = "List" + capitalize(plural)
		}
	case http.MethodPost:
		rpc = "Create" + singular
	case http.MethodPatch, http.MethodPut:
		rpc = "Update" + singular
	case http.MethodDelete:
		rpc = "Delete" + singular
	default:
		return "", "", false
	}

	return "/jumpstarter.admin.v1." + service + "/" + rpc, namespace, true
}

// serviceForPlural maps the URL plural form ("leases" / "exporters" /
// "clients" / "webhooks") to the gRPC service name and the singular form
// used in RPC method names (LeaseService.GetLease, etc.).
func serviceForPlural(plural string) (service, singular string, ok bool) {
	switch plural {
	case "leases":
		return "LeaseService", "Lease", true
	case "exporters":
		return "ExporterService", "Exporter", true
	case "clients":
		return "ClientService", "Client", true
	case "webhooks":
		return "WebhookService", "Webhook", true
	}
	return "", "", false
}

func capitalize(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

// bearerFromHTTP extracts the JWT from a Bearer-format Authorization
// header. Returns empty string when absent or malformed.
func bearerFromHTTP(r *http.Request) string {
	h := r.Header.Get("Authorization")
	const prefix = "Bearer "
	if len(h) <= len(prefix) || !strings.EqualFold(h[:len(prefix)], prefix) {
		return ""
	}
	return h[len(prefix):]
}

// writeStatus translates a gRPC status error to its grpc-gateway HTTP
// equivalent. Keeps REST responses consistent with what the gateway would
// emit if the same error came back over a normal handler path.
func writeStatus(w http.ResponseWriter, err error) {
	st, ok := status.FromError(err)
	code := http.StatusInternalServerError
	msg := err.Error()
	if ok {
		code = httpCodeForGRPC(st.Code())
		msg = st.Message()
	}
	http.Error(w, msg, code)
}

func httpCodeForGRPC(c codes.Code) int {
	switch c {
	case codes.OK:
		return http.StatusOK
	case codes.Unauthenticated:
		return http.StatusUnauthorized
	case codes.PermissionDenied:
		return http.StatusForbidden
	case codes.NotFound:
		return http.StatusNotFound
	case codes.AlreadyExists:
		return http.StatusConflict
	case codes.InvalidArgument, codes.FailedPrecondition:
		return http.StatusBadRequest
	case codes.DeadlineExceeded:
		return http.StatusGatewayTimeout
	default:
		return http.StatusInternalServerError
	}
}
