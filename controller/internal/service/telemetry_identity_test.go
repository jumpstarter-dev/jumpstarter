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
	"testing"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func TestAuthenticateExporter_InvalidTokenDoesNotLeakParseError(t *testing.T) {
	hub := testSigner(t)
	expired := testSigner(t)
	expired.SetTokenLifetime(-time.Hour)
	expiredTok, err := expired.Token("exporter:jumpstarter:alice:uid1")
	if err != nil {
		t.Fatalf("expired token: %v", err)
	}
	other, err := oidc.NewSignerFromSeed([]byte("other-seed"), hub.Issuer(), hub.Audience())
	if err != nil {
		t.Fatalf("other signer: %v", err)
	}
	foreignTok, err := other.Token("exporter:jumpstarter:alice:uid1")
	if err != nil {
		t.Fatalf("foreign token: %v", err)
	}

	cases := []struct {
		name  string
		token string
		leak  string
	}{
		{name: "malformed", token: "not-a-real-token", leak: "malformed"},
		{name: "expired", token: expiredTok, leak: "expired"},
		{name: "wrong key", token: foreignTok, leak: "signature"},
	}

	svc := &TelemetryService{Signer: hub}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ctx := metadata.NewIncomingContext(
				context.Background(),
				metadata.Pairs("authorization", "Bearer "+tc.token),
			)
			_, err := svc.authenticateExporter(ctx)
			if err == nil {
				t.Fatal("expected Unauthenticated")
			}
			st := status.Convert(err)
			if st.Code() != codes.Unauthenticated {
				t.Fatalf("code = %v, want Unauthenticated (%v)", st.Code(), err)
			}
			msg := st.Message()
			if msg != "invalid token" {
				t.Fatalf("client message = %q, want %q (must not wrap jwt parse error)", msg, "invalid token")
			}
			if strings.Contains(strings.ToLower(msg), tc.leak) {
				t.Fatalf("client message %q leaked jwt detail %q", msg, tc.leak)
			}
		})
	}
}
