/*
Copyright 2024.

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

package main

import (
	"testing"

	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
)

func TestJwtAuthenticatorsToOIDCConfigs(t *testing.T) {
	tests := []struct {
		name           string
		authenticators []apiserverv1beta1.JWTAuthenticator
		wantCount      int
		wantNil        bool
		wantIssuer     string
	}{
		{
			name: "single JWT authenticator",
			authenticators: []apiserverv1beta1.JWTAuthenticator{
				{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://dex.example.com",
						Audiences: []string{"jumpstarter"},
					},
				},
			},
			wantCount: 1,
		},
		{
			name: "multiple JWT authenticators including localhost",
			authenticators: []apiserverv1beta1.JWTAuthenticator{
				{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://localhost:8085",
						Audiences: []string{"jumpstarter"},
					},
				},
				{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://dex.example.com",
						Audiences: []string{"jumpstarter"},
					},
				},
			},
			wantCount: 1, // localhost issuer should be skipped
		},
		{
			name:           "nil authenticators returns nil",
			authenticators: nil,
			wantNil:        true,
		},
		{
			name:           "empty authenticators returns nil",
			authenticators: []apiserverv1beta1.JWTAuthenticator{},
			wantNil:        true,
		},
		{
			name: "only localhost authenticator returns nil",
			authenticators: []apiserverv1beta1.JWTAuthenticator{
				{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://localhost:8085",
						Audiences: []string{"jumpstarter"},
					},
				},
			},
			wantNil: true,
		},
		{
			name: "multiple external authenticators",
			authenticators: []apiserverv1beta1.JWTAuthenticator{
				{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://dex.example.com",
						Audiences: []string{"jumpstarter"},
					},
				},
				{
					Issuer: apiserverv1beta1.Issuer{
						URL:       "https://keycloak.example.com",
						Audiences: []string{"jumpstarter", "jumpstarter-cli"},
					},
				},
			},
			wantCount:  2,
			wantIssuer: "https://dex.example.com",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			configs := jwtAuthenticatorsToOIDCConfigs(tt.authenticators)

			if tt.wantNil {
				if configs != nil {
					t.Errorf("expected nil, got %v", configs)
				}
				return
			}

			if configs == nil {
				t.Fatal("expected non-nil configs, got nil")
			}

			if len(configs) != tt.wantCount {
				t.Errorf("expected %d configs, got %d", tt.wantCount, len(configs))
			}

			if tt.wantIssuer != "" && len(configs) > 0 {
				if configs[0].Issuer != tt.wantIssuer {
					t.Errorf("expected issuer %q, got %q", tt.wantIssuer, configs[0].Issuer)
				}
			}
		})
	}
}

func TestJwtAuthenticatorsToOIDCConfigsContent(t *testing.T) {
	authenticators := []apiserverv1beta1.JWTAuthenticator{
		{
			Issuer: apiserverv1beta1.Issuer{
				URL:       "https://dex.example.com",
				Audiences: []string{"jumpstarter", "jumpstarter-cli"},
			},
		},
	}

	configs := jwtAuthenticatorsToOIDCConfigs(authenticators)

	if len(configs) != 1 {
		t.Fatalf("expected 1 config, got %d", len(configs))
	}

	cfg := configs[0]
	if cfg.Issuer != "https://dex.example.com" {
		t.Errorf("expected issuer 'https://dex.example.com', got %q", cfg.Issuer)
	}
	if len(cfg.Audiences) != 2 {
		t.Errorf("expected 2 audiences, got %d", len(cfg.Audiences))
	}
}
