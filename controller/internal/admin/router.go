/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package admin wires the package-aware interceptor dispatch that lets
// jumpstarter.admin.v1.* RPCs run through the OIDC + SAR + Impersonate
// pipeline while jumpstarter.client.v1.* and jumpstarter.v1.* RPCs continue
// through the existing object-token authentication path.
package admin

import (
	"context"
	"strings"

	"google.golang.org/grpc"
)

const adminPackagePrefix = "/jumpstarter.admin.v1."

// IsAdminMethod reports whether the gRPC FullMethod (e.g. "/jumpstarter.admin.v1.LeaseService/GetLease")
// belongs to the admin pipeline.
func IsAdminMethod(fullMethod string) bool {
	return strings.HasPrefix(fullMethod, adminPackagePrefix)
}

// UnaryRouter dispatches each unary call to either adminInterceptor or
// passThrough based on the method's proto package. Both interceptors are
// expected to follow the standard grpc.UnaryServerInterceptor signature.
//
// adminInterceptor should already be a chain of (authn, authz, logging…).
// passThrough is the existing controller chain (logging, recovery).
func UnaryRouter(adminInterceptor, passThrough grpc.UnaryServerInterceptor) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		if IsAdminMethod(info.FullMethod) {
			return adminInterceptor(ctx, req, info, handler)
		}
		return passThrough(ctx, req, info, handler)
	}
}

// StreamRouter is the streaming counterpart of UnaryRouter.
func StreamRouter(adminInterceptor, passThrough grpc.StreamServerInterceptor) grpc.StreamServerInterceptor {
	return func(srv any, ss grpc.ServerStream, info *grpc.StreamServerInfo, handler grpc.StreamHandler) error {
		if IsAdminMethod(info.FullMethod) {
			return adminInterceptor(srv, ss, info, handler)
		}
		return passThrough(srv, ss, info, handler)
	}
}
