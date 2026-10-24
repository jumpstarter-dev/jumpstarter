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

package main

import (
	"context"
	"fmt"
	"os"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/yaml"
	apiserverinstall "k8s.io/apiserver/pkg/apis/apiserver/install"
)

// loadClientAuthenticator reads AUTHENTICATION_CONFIG, the controller ConfigMap
// mounted into the telemetry pod, and builds authenticators for external OIDC
// issuers. Unset means internal controller tokens only.
func loadClientAuthenticator(ctx context.Context) (authentication.ContextAuthenticator, error) {
	path := os.Getenv("AUTHENTICATION_CONFIG")
	if path == "" {
		return nil, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read authentication config: %w", err)
	}
	var cfg config.Config
	if err := yaml.Unmarshal(raw, &cfg); err != nil {
		return nil, fmt.Errorf("parse authentication config: %w", err)
	}
	scheme := runtime.NewScheme()
	apiserverinstall.Install(scheme)
	tokenAuth, err := config.LoadExternalJWTAuthenticators(ctx, scheme, cfg.Authentication)
	if err != nil {
		return nil, err
	}
	if tokenAuth == nil {
		return nil, nil
	}
	return authentication.NewBearerTokenAuthenticator(tokenAuth), nil
}
