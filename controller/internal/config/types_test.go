package config

import (
	"testing"

	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
	"sigs.k8s.io/yaml"
)

func TestConfigRoundTrip(t *testing.T) {
	// Create a config struct
	original := Config{
		Authentication: Authentication{
			Internal: Internal{
				Prefix:        "internal:",
				TokenLifetime: "43800h",
			},
			K8s: K8s{
				Enabled: true,
			},
			JWT: []apiserverv1beta1.JWTAuthenticator{}, // Empty array
		},
		Provisioning: Provisioning{
			Enabled: false,
		},
		Grpc: Grpc{
			Keepalive: Keepalive{
				MinTime:             "1s",
				PermitWithoutStream: true,
				Timeout:             "180s",
				IntervalTime:        "10s",
			},
		},
	}

	// Marshal to YAML
	yamlData, err := yaml.Marshal(original)
	if err != nil {
		t.Fatalf("Failed to marshal config: %v", err)
	}

	// Unmarshal back to struct
	var parsed Config
	err = yaml.Unmarshal(yamlData, &parsed)
	if err != nil {
		t.Fatalf("Failed to unmarshal config: %v", err)
	}

	// Verify key fields
	if parsed.Authentication.Internal.Prefix != original.Authentication.Internal.Prefix {
		t.Errorf("Internal prefix mismatch: got %s, want %s",
			parsed.Authentication.Internal.Prefix, original.Authentication.Internal.Prefix)
	}

	if parsed.Grpc.Keepalive.MinTime != original.Grpc.Keepalive.MinTime {
		t.Errorf("Keepalive minTime mismatch: got %s, want %s",
			parsed.Grpc.Keepalive.MinTime, original.Grpc.Keepalive.MinTime)
	}

	if parsed.Grpc.Keepalive.PermitWithoutStream != original.Grpc.Keepalive.PermitWithoutStream {
		t.Errorf("Keepalive permitWithoutStream mismatch: got %v, want %v",
			parsed.Grpc.Keepalive.PermitWithoutStream, original.Grpc.Keepalive.PermitWithoutStream)
	}
}

func TestRouterRoundTrip(t *testing.T) {
	// Create a router config
	original := Router{
		"default": RouterEntry{
			Endpoint: "router-0.example.com:443",
		},
		"router-1": RouterEntry{
			Endpoint: "router-1.example.com:443",
			Labels: map[string]string{
				"router-index": "1",
			},
		},
		"router-2": RouterEntry{
			Endpoint: "router-2.example.com:443",
			Labels: map[string]string{
				"router-index": "2",
				"zone":         "us-east",
			},
		},
	}

	// Marshal to YAML
	yamlData, err := yaml.Marshal(original)
	if err != nil {
		t.Fatalf("Failed to marshal router: %v", err)
	}

	t.Logf("Generated YAML:\n%s", string(yamlData))

	// Unmarshal back to struct
	var parsed Router
	err = yaml.Unmarshal(yamlData, &parsed)
	if err != nil {
		t.Fatalf("Failed to unmarshal router: %v", err)
	}

	// Verify all routers exist
	if len(parsed) != len(original) {
		t.Errorf("Router count mismatch: got %d, want %d", len(parsed), len(original))
	}

	// Verify default router
	if entry, exists := parsed["default"]; !exists {
		t.Error("Missing 'default' router")
	} else if entry.Endpoint != original["default"].Endpoint {
		t.Errorf("Default router endpoint mismatch: got %s, want %s",
			entry.Endpoint, original["default"].Endpoint)
	}

	// Verify router-1
	if entry, exists := parsed["router-1"]; !exists {
		t.Error("Missing 'router-1' router")
	} else {
		if entry.Endpoint != original["router-1"].Endpoint {
			t.Errorf("Router-1 endpoint mismatch: got %s, want %s",
				entry.Endpoint, original["router-1"].Endpoint)
		}
		if entry.Labels["router-index"] != "1" {
			t.Errorf("Router-1 index label mismatch: got %s, want 1",
				entry.Labels["router-index"])
		}
	}

	// Verify router-2 labels
	if entry, exists := parsed["router-2"]; !exists {
		t.Error("Missing 'router-2' router")
	} else {
		if len(entry.Labels) != 2 {
			t.Errorf("Router-2 label count mismatch: got %d, want 2", len(entry.Labels))
		}
	}
}

func TestParseYAMLToRouter(t *testing.T) {
	// Test parsing actual YAML string (like from ConfigMap)
	yamlInput := `
default:
  endpoint: router.example.com:443
router-1:
  endpoint: router-1.example.com:443
  labels:
    router-index: "1"
router-2:
  endpoint: router-2.example.com:443
  labels:
    router-index: "2"
`

	var router Router
	err := yaml.Unmarshal([]byte(yamlInput), &router)
	if err != nil {
		t.Fatalf("Failed to unmarshal YAML: %v", err)
	}

	// Verify structure
	if len(router) != 3 {
		t.Errorf("Expected 3 routers, got %d", len(router))
	}

	// Verify default has no labels
	if defaultEntry, exists := router["default"]; exists {
		if len(defaultEntry.Labels) != 0 {
			t.Errorf("Default router should have no labels, got %d", len(defaultEntry.Labels))
		}
	}

	// Verify router-1 has labels
	if router1, exists := router["router-1"]; exists {
		if len(router1.Labels) == 0 {
			t.Error("Router-1 should have labels")
		}
	}
}

func TestParseDuration(t *testing.T) {
	tests := []struct {
		input    string
		wantErr  bool
		expected string
	}{
		{"1s", false, "1s"},
		{"10s", false, "10s"},
		{"1m", false, "1m0s"},
		{"1h", false, "1h0m0s"},
		{"", false, "0s"}, // empty string returns 0
		{"invalid", true, ""},
	}

	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			duration, err := ParseDuration(tt.input)
			if (err != nil) != tt.wantErr {
				t.Errorf("ParseDuration(%q) error = %v, wantErr %v", tt.input, err, tt.wantErr)
				return
			}
			if !tt.wantErr && duration.String() != tt.expected {
				t.Errorf("ParseDuration(%q) = %v, want %v", tt.input, duration, tt.expected)
			}
		})
	}
}
