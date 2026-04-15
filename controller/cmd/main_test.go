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

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestExtractOIDCConfigs(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatalf("failed to add corev1 to scheme: %v", err)
	}

	tests := []struct {
		name      string
		configmap *corev1.ConfigMap
		wantCount int
		wantNil   bool
	}{
		{
			name: "valid config key with JWT authenticators",
			configmap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter-controller",
					Namespace: "default",
				},
				Data: map[string]string{
					"config": `
authentication:
  internal:
    prefix: "internal:"
    tokenLifetime: "43800h"
  jwt:
    - issuer:
        url: "https://dex.example.com"
        audiences:
          - "jumpstarter"
      claimMappings:
        username:
          claim: "email"
          prefix: ""
`,
				},
			},
			wantCount: 1,
		},
		{
			name: "valid config key with multiple JWT authenticators including localhost",
			configmap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter-controller",
					Namespace: "default",
				},
				Data: map[string]string{
					"config": `
authentication:
  internal:
    prefix: "internal:"
    tokenLifetime: "43800h"
  jwt:
    - issuer:
        url: "https://localhost:8085"
        audiences:
          - "jumpstarter"
      claimMappings:
        username:
          claim: "sub"
          prefix: ""
    - issuer:
        url: "https://dex.example.com"
        audiences:
          - "jumpstarter"
      claimMappings:
        username:
          claim: "email"
          prefix: ""
`,
				},
			},
			wantCount: 1, // localhost issuer should be skipped
		},
		{
			name: "legacy authentication key only should NOT produce OIDC configs",
			configmap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter-controller",
					Namespace: "default",
				},
				Data: map[string]string{
					"authentication": `
jwt:
  - issuer:
      url: "https://dex.example.com"
      audiences:
        - "jumpstarter"
    claimMappings:
      username:
        claim: "email"
        prefix: ""
`,
				},
			},
			wantNil: true,
		},
		{
			name: "missing config key returns nil",
			configmap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter-controller",
					Namespace: "default",
				},
				Data: map[string]string{
					"router": `default: {endpoint: "router.example.com:443"}`,
				},
			},
			wantNil: true,
		},
		{
			name: "invalid YAML in config key returns nil",
			configmap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "jumpstarter-controller",
					Namespace: "default",
				},
				Data: map[string]string{
					"config": `{{{invalid yaml`,
				},
			},
			wantNil: true,
		},
		{
			name:      "missing configmap returns nil",
			configmap: nil,
			wantNil:   true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			builder := fake.NewClientBuilder().WithScheme(scheme)
			if tt.configmap != nil {
				builder = builder.WithObjects(tt.configmap)
			}
			fakeClient := builder.Build()

			configs := extractOIDCConfigs(fakeClient.(client.Reader), "default")

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
		})
	}
}

func TestExtractOIDCConfigsContent(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatalf("failed to add corev1 to scheme: %v", err)
	}

	configmap := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "jumpstarter-controller",
			Namespace: "default",
		},
		Data: map[string]string{
			"config": `
authentication:
  internal:
    prefix: "internal:"
    tokenLifetime: "43800h"
  jwt:
    - issuer:
        url: "https://dex.example.com"
        audiences:
          - "jumpstarter"
          - "jumpstarter-cli"
      claimMappings:
        username:
          claim: "email"
          prefix: ""
`,
		},
	}

	fakeClient := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(configmap).
		Build()

	configs := extractOIDCConfigs(fakeClient.(client.Reader), "default")

	if len(configs) != 1 {
		t.Fatalf("expected 1 config, got %d", len(configs))
	}

	cfg := configs[0]
	if cfg.Issuer != "https://dex.example.com" {
		t.Errorf("expected issuer 'https://dex.example.com', got %q", cfg.Issuer)
	}
	if cfg.ClientID != "jumpstarter-cli" {
		t.Errorf("expected clientID 'jumpstarter-cli', got %q", cfg.ClientID)
	}
	if len(cfg.Audiences) != 2 {
		t.Errorf("expected 2 audiences, got %d", len(cfg.Audiences))
	}
}

// TestExtractOIDCConfigsIgnoresLegacyKey is a dedicated regression test
// ensuring that a ConfigMap containing only the legacy "authentication" key
// (without the "config" key) does NOT produce any OIDC configurations.
// This prevents accidental reintroduction of the legacy fallback removed in
// this PR.
func TestExtractOIDCConfigsIgnoresLegacyKey(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatalf("failed to add corev1 to scheme: %v", err)
	}

	configmap := &corev1.ConfigMap{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "jumpstarter-controller",
			Namespace: "default",
		},
		Data: map[string]string{
			// Only the legacy key -- this must NOT be read
			"authentication": `
jwt:
  - issuer:
      url: "https://dex.example.com"
      audiences:
        - "jumpstarter"
    claimMappings:
      username:
        claim: "email"
        prefix: ""
`,
		},
	}

	fakeClient := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(configmap).
		Build()

	configs := extractOIDCConfigs(fakeClient.(client.Reader), "default")

	if configs != nil {
		t.Errorf("expected nil when only legacy 'authentication' key is present, got %v", configs)
	}
}
