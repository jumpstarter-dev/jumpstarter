/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package service

import (
	"context"
	"strings"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authorization"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// exporterIdentity is the exporter CRD namespace/name claimed by a bearer token.
type exporterIdentity struct {
	namespace string
	name      string
}

func (id exporterIdentity) key() string {
	return id.namespace + "/" + id.name
}

func parseExporterSubject(subject string) (exporterIdentity, error) {
	parts := strings.SplitN(subject, ":", 4)
	if len(parts) != 4 || parts[0] != "exporter" {
		return exporterIdentity{}, status.Errorf(codes.PermissionDenied, "token is not an exporter token")
	}
	if parts[1] == "" || parts[2] == "" {
		return exporterIdentity{}, status.Errorf(codes.PermissionDenied, "token has incomplete exporter identity")
	}
	return exporterIdentity{namespace: parts[1], name: parts[2]}, nil
}

func (s *TelemetryService) authenticateExporter(ctx context.Context) (exporterIdentity, error) {
	token, err := authentication.BearerTokenFromContext(ctx)
	if err != nil {
		return exporterIdentity{}, err
	}
	if s.Signer == nil {
		return exporterIdentity{}, status.Error(codes.Internal, "telemetry signer is not configured")
	}
	subject, err := s.Signer.ParseSubject(token)
	if err != nil {
		// Do not wrap the jwt parse error into the gRPC status: clients
		// would learn why verification failed (expired vs bad signature vs
		// malformed). RouterService likewise returns a generic client message.
		log.FromContext(ctx).V(1).Info("telemetry token parse failed", "error", err.Error())
		return exporterIdentity{}, status.Error(codes.Unauthenticated, "invalid token")
	}
	return parseExporterSubject(subject)
}

type telemetryIdentity struct {
	kind      string // "exporter" or "client"
	namespace string
	name      string
}

func parseClientSubject(subject string) (telemetryIdentity, error) {
	parts := strings.SplitN(subject, ":", 4)
	if len(parts) != 4 || parts[0] != "client" {
		return telemetryIdentity{}, status.Errorf(codes.PermissionDenied, "token is not a client or exporter token")
	}
	if parts[1] == "" || parts[2] == "" {
		return telemetryIdentity{}, status.Errorf(codes.PermissionDenied, "token has incomplete client identity")
	}
	return telemetryIdentity{kind: "client", namespace: parts[1], name: parts[2]}, nil
}

func (s *TelemetryService) authenticatePushLogs(ctx context.Context) (telemetryIdentity, error) {
	token, err := authentication.BearerTokenFromContext(ctx)
	if err != nil {
		return telemetryIdentity{}, err
	}
	if s.Signer == nil {
		return telemetryIdentity{}, status.Error(codes.Internal, "telemetry signer is not configured")
	}
	subject, err := s.Signer.ParseSubject(token)
	if err != nil {
		// External OIDC access tokens are not signed by the controller.
		// Fall through to the same authenticator and identity mapping the
		// controller uses for client RPCs.
		if s.ClientAuthn != nil {
			return s.authenticateExternalClient(ctx)
		}
		// Same generic status as authenticateExporter: do not wrap the jwt
		// parse error into the client-visible message.
		log.FromContext(ctx).V(1).Info("telemetry token parse failed", "error", err.Error())
		return telemetryIdentity{}, status.Error(codes.Unauthenticated, "invalid token")
	}
	if strings.HasPrefix(subject, "exporter:") {
		id, err := parseExporterSubject(subject)
		if err != nil {
			return telemetryIdentity{}, err
		}
		return telemetryIdentity{kind: "exporter", namespace: id.namespace, name: id.name}, nil
	}
	if strings.HasPrefix(subject, "client:") {
		return parseClientSubject(subject)
	}
	return telemetryIdentity{}, status.Errorf(codes.PermissionDenied, "token is not a client or exporter token")
}

// authenticateExternalClient maps an already-present bearer token through the
// controller's client authenticator. jumpstarter-kind, jumpstarter-namespace,
// and jumpstarter-name metadata use the same rules as controller RPCs.
func (s *TelemetryService) authenticateExternalClient(ctx context.Context) (telemetryIdentity, error) {
	resp, ok, err := s.ClientAuthn.AuthenticateContext(ctx)
	if err != nil || !ok || resp == nil || resp.User == nil {
		if err != nil {
			log.FromContext(ctx).V(1).Info("external client token rejected", "error", err.Error())
		}
		return telemetryIdentity{}, status.Error(codes.Unauthenticated, "invalid token")
	}
	attrs, err := authorization.NewMetadataAttributesGetter(authorization.MetadataAttributesGetterConfig{
		NamespaceKey: "jumpstarter-namespace",
		ResourceKey:  "jumpstarter-kind",
		NameKey:      "jumpstarter-name",
	}).ContextAttributes(ctx, resp.User)
	if err != nil {
		return telemetryIdentity{}, err
	}
	if attrs.GetResource() != "Client" {
		return telemetryIdentity{}, status.Error(codes.PermissionDenied, "token is not a client token")
	}
	if attrs.GetNamespace() == "" || attrs.GetName() == "" {
		return telemetryIdentity{}, status.Error(codes.PermissionDenied, "token has incomplete client identity")
	}
	return telemetryIdentity{kind: "client", namespace: attrs.GetNamespace(), name: attrs.GetName()}, nil
}
