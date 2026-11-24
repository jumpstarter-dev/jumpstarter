package config

import (
	"os"
	"path/filepath"
	"testing"
)

func writeRouterConfiguration(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("failed to write router configuration: %v", err)
	}
	return path
}

func TestLoadRouterConfigurationReadsTheGrpcSettingsFromAFile(t *testing.T) {
	path := writeRouterConfiguration(t, `
grpc:
  keepalive:
    minTime: 1s
    permitWithoutStream: true
    timeout: 30s
`)

	options, err := LoadRouterConfiguration(path)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(options) != 2 {
		t.Fatalf("expected 2 server options (enforcement policy + server parameters), got %d", len(options))
	}
}
