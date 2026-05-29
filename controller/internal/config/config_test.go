package config

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"testing"
	"time"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	apiserverinstall "k8s.io/apiserver/pkg/apis/apiserver/install"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

// generateTestCertificate creates a minimal self-signed certificate for testing
func generateTestCertificate(t *testing.T) string {
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("failed to generate private key: %v", err)
	}

	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject: pkix.Name{
			Organization: []string{"Test"},
		},
		NotBefore: time.Now(),
		NotAfter:  time.Now().Add(time.Hour),
		KeyUsage:  x509.KeyUsageDigitalSignature,
	}

	certDER, err := x509.CreateCertificate(rand.Reader, &template, &template, &priv.PublicKey, priv)
	if err != nil {
		t.Fatalf("failed to create certificate: %v", err)
	}

	return string(pem.EncodeToMemory(&pem.Block{
		Type:  "CERTIFICATE",
		Bytes: certDER,
	}))
}

func TestLoadConfiguration(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatalf("failed to add corev1 to scheme: %v", err)
	}
	apiserverinstall.Install(scheme)

	// Create a mock OIDC signer for testing
	signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "test")
	if err != nil {
		t.Fatalf("failed to create OIDC signer: %v", err)
	}

	// Generate a test certificate
	testCert := generateTestCertificate(t)

	tests := []struct {
		name      string
		configMap *corev1.ConfigMap
		wantErr   bool
		errMsg    string
	}{
		{
			name: "valid config key only",
			configMap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-config",
					Namespace: "default",
				},
				Data: map[string]string{
					"router": `
default:
  endpoint: "router.example.com:443"
`,
					"config": `
authentication:
  internal:
    prefix: "internal:"
  jwt:
    - issuer:
        url: "https://dex.example.com"
        audiences: ["jumpstarter"]
      claimMappings:
        username:
          claim: "name"
          prefix: "dex:"
grpc:
  keepalive: {}
provisioning:
  enabled: false
leasePolicy: {}
`,
				},
			},
			wantErr: false,
		},
		{
			name: "legacy authentication key only should fail",
			configMap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-config",
					Namespace: "default",
				},
				Data: map[string]string{
					"router": `
default:
  endpoint: "router.example.com:443"
`,
					"authentication": `
jwt:
  - issuer:
      url: "https://dex.example.com"
      audiences: ["jumpstarter"]
`,
				},
			},
			wantErr: true,
			errMsg:  "missing config section",
		},
		{
			name: "both config and authentication keys present should use config only",
			configMap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-config",
					Namespace: "default",
				},
				Data: map[string]string{
					"router": `
default:
  endpoint: "router.example.com:443"
`,
					"config": `
authentication:
  internal:
    prefix: "internal:"
  jwt:
    - issuer:
        url: "https://dex.example.com"
        audiences: ["jumpstarter"]
      claimMappings:
        username:
          claim: "name"
          prefix: "dex:"
grpc:
  keepalive: {}
provisioning:
  enabled: false
leasePolicy: {}
`,
					"authentication": `
jwt:
  - issuer:
      url: "https://legacy.example.com"
      audiences: ["legacy"]
`,
				},
			},
			wantErr: false,
		},
		{
			name: "missing config key should fail",
			configMap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-config",
					Namespace: "default",
				},
				Data: map[string]string{
					"router": `
default:
  endpoint: "router.example.com:443"
`,
				},
			},
			wantErr: true,
			errMsg:  "missing config section",
		},
		{
			name: "missing router key should fail",
			configMap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-config",
					Namespace: "default",
				},
				Data: map[string]string{
					"config": `
authentication:
  internal:
    prefix: "internal:"
  jwt:
    - issuer:
        url: "https://dex.example.com"
        audiences: ["jumpstarter"]
      claimMappings:
        username:
          claim: "name"
          prefix: "dex:"
grpc:
  keepalive: {}
provisioning:
  enabled: false
leasePolicy: {}
`,
				},
			},
			wantErr: true,
			errMsg:  "missing router section",
		},
		{
			name: "invalid YAML in config should fail",
			configMap: &corev1.ConfigMap{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-config",
					Namespace: "default",
				},
				Data: map[string]string{
					"router": `
default:
  endpoint: "router.example.com:443"
`,
					"config": "invalid: yaml: content:",
				},
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Create fake client with the test ConfigMap
			fakeClient := fake.NewClientBuilder().
				WithScheme(scheme).
				WithObjects(tt.configMap).
				Build()

			key := client.ObjectKey{
				Namespace: tt.configMap.Namespace,
				Name:      tt.configMap.Name,
			}

			_, _, _, _, _, _, loadedConfig, err := LoadConfiguration(
				context.Background(),
				fakeClient,
				scheme,
				key,
				signer,
				testCert,
			)

			if tt.wantErr {
				if err == nil {
					t.Errorf("LoadConfiguration() expected error but got nil")
				} else if tt.errMsg != "" && !contains(err.Error(), tt.errMsg) {
					t.Errorf("LoadConfiguration() error = %v, want error containing %q", err, tt.errMsg)
				}
			} else {
				if err != nil {
					t.Errorf("LoadConfiguration() unexpected error = %v", err)
				}
				if loadedConfig == nil {
					t.Error("LoadConfiguration() returned nil config")
				}
			}
		})
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > len(substr) && stringContains(s, substr))
}

func stringContains(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
