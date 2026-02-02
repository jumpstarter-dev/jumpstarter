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

package login

import (
	"context"
	"embed"
	"html/template"
	"net/http"
	"os"

	"github.com/gin-gonic/gin"
	ctrl "sigs.k8s.io/controller-runtime"
)

//go:embed templates/*
var fs embed.FS

const (
	defaultPort = ":8086"
)

// OIDCConfig represents a single OIDC provider configuration
type OIDCConfig struct {
	Issuer    string   `json:"issuer"`
	ClientID  string   `json:"clientId"`
	Audiences []string `json:"audiences,omitempty"`
}

// AuthConfig is the response structure for /v1/auth/config
type AuthConfig struct {
	GRPCEndpoint   string       `json:"grpcEndpoint"`
	RouterEndpoint string       `json:"routerEndpoint,omitempty"`
	Namespace      string       `json:"namespace"`
	CABundle       string       `json:"caBundle,omitempty"`
	OIDC           []OIDCConfig `json:"oidc,omitempty"`
}

// Config holds the configuration for the login service
type Config struct {
	// GRPCEndpoint is the main gRPC controller endpoint
	GRPCEndpoint string
	// RouterEndpoint is the router endpoint (optional)
	RouterEndpoint string
	// Namespace is the default namespace for clients
	Namespace string
	// CABundle is the PEM-encoded CA certificate bundle
	CABundle string
	// OIDC contains the OIDC provider configurations
	OIDC []OIDCConfig
}

// Service provides the login API for simplified CLI login
type Service struct {
	config Config
}

// NewService creates a new login service with the given configuration
func NewService(config Config) *Service {
	return &Service{
		config: config,
	}
}

// NewServiceFromEnv creates a new login service with configuration from environment variables
func NewServiceFromEnv() *Service {
	return &Service{
		config: Config{
			GRPCEndpoint:   getEnvOrDefault("GRPC_ENDPOINT", "localhost:8082"),
			RouterEndpoint: os.Getenv("GRPC_ROUTER_ENDPOINT"),
			Namespace:      os.Getenv("NAMESPACE"),
			CABundle:       os.Getenv("CA_BUNDLE_PEM"),
			// OIDC config will be set via SetOIDCConfig
		},
	}
}

// SetOIDCConfig sets the OIDC configuration
func (s *Service) SetOIDCConfig(oidc []OIDCConfig) {
	s.config.OIDC = oidc
}

// Start implements manager.Runnable and starts the HTTP server
func (s *Service) Start(ctx context.Context) error {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery())

	// Parse and set HTML templates
	r.SetHTMLTemplate(template.Must(template.ParseFS(fs, "templates/*")))

	// Landing page with login instructions
	r.GET("/", s.handleLandingPage)

	// Auth config API endpoint
	r.GET("/v1/auth/config", s.handleAuthConfig)

	// Health check endpoint
	r.GET("/healthz", func(c *gin.Context) {
		c.String(http.StatusOK, "ok")
	})

	port := getEnvOrDefault("LOGIN_SERVICE_PORT", defaultPort)

	server := &http.Server{
		Addr:    port,
		Handler: r,
	}

	// Start server in a goroutine
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			ctrl.Log.WithName("login-service").Error(err, "server error")
		}
	}()

	// Wait for context cancellation
	<-ctx.Done()

	// Graceful shutdown
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5)
	defer cancel()

	return server.Shutdown(shutdownCtx)
}

// NeedLeaderElection returns false since this service can run on all replicas
func (s *Service) NeedLeaderElection() bool {
	return false
}

// SetupWithManager registers the service with the controller manager
func (s *Service) SetupWithManager(mgr ctrl.Manager) error {
	return mgr.Add(s)
}

// handleLandingPage serves the landing page with login instructions
func (s *Service) handleLandingPage(c *gin.Context) {
	c.HTML(http.StatusOK, "index.html", map[string]interface{}{
		"GRPCEndpoint":   s.config.GRPCEndpoint,
		"RouterEndpoint": s.config.RouterEndpoint,
		"Namespace":      s.config.Namespace,
		"HasOIDC":        len(s.config.OIDC) > 0,
		"OIDC":           s.config.OIDC,
	})
}

// handleAuthConfig returns the authentication configuration as JSON
func (s *Service) handleAuthConfig(c *gin.Context) {
	response := AuthConfig{
		GRPCEndpoint:   s.config.GRPCEndpoint,
		RouterEndpoint: s.config.RouterEndpoint,
		Namespace:      s.config.Namespace,
		CABundle:       s.config.CABundle,
		OIDC:           s.config.OIDC,
	}

	c.JSON(http.StatusOK, response)
}

// getEnvOrDefault returns the environment variable value or a default
func getEnvOrDefault(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}
