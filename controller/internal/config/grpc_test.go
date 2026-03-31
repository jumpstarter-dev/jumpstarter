package config

import (
	"strings"
	"testing"
)

func TestLoadGrpcConfigurationMinimalConfigReturnsSlice(t *testing.T) {
	cfg := Grpc{
		Keepalive: Keepalive{
			MinTime:             "5s",
			PermitWithoutStream: true,
		},
	}

	options, err := LoadGrpcConfiguration(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(options) < 1 {
		t.Fatalf("expected at least 1 server option, got %d", len(options))
	}
}

func TestLoadGrpcConfigurationWithTimeoutAndIntervalTime(t *testing.T) {
	cfg := Grpc{
		Keepalive: Keepalive{
			MinTime:             "1s",
			PermitWithoutStream: true,
			Timeout:             "30s",
			IntervalTime:        "5s",
		},
	}

	options, err := LoadGrpcConfiguration(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(options) != 2 {
		t.Fatalf("expected 2 server options (enforcement policy + server parameters), got %d", len(options))
	}
}

func TestLoadGrpcConfigurationInvalidTimeoutReturnsError(t *testing.T) {
	cfg := Grpc{
		Keepalive: Keepalive{
			MinTime: "1s",
			Timeout: "abc",
		},
	}

	_, err := LoadGrpcConfiguration(cfg)
	if err == nil {
		t.Fatal("expected error for invalid timeout, got nil")
	}

	if !strings.Contains(err.Error(), "timeout") {
		t.Fatalf("expected error mentioning 'timeout', got: %v", err)
	}
}

func TestLoadGrpcConfigurationInvalidIntervalTimeReturnsError(t *testing.T) {
	cfg := Grpc{
		Keepalive: Keepalive{
			MinTime:      "1s",
			IntervalTime: "xyz",
		},
	}

	_, err := LoadGrpcConfiguration(cfg)
	if err == nil {
		t.Fatal("expected error for invalid intervalTime, got nil")
	}

	if !strings.Contains(err.Error(), "intervalTime") {
		t.Fatalf("expected error mentioning 'intervalTime', got: %v", err)
	}
}

func TestLoadGrpcConfigurationWithConnectionLifetimeFields(t *testing.T) {
	cfg := Grpc{
		Keepalive: Keepalive{
			MinTime:               "1s",
			PermitWithoutStream:   true,
			MaxConnectionIdle:     "5m",
			MaxConnectionAge:      "30m",
			MaxConnectionAgeGrace: "10s",
		},
	}

	options, err := LoadGrpcConfiguration(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(options) != 2 {
		t.Fatalf("expected 2 server options (enforcement policy + server parameters), got %d", len(options))
	}
}

func TestLoadGrpcConfigurationInvalidConnectionLifetimeFields(t *testing.T) {
	tests := []struct {
		name      string
		cfg       Grpc
		wantInErr string
	}{
		{
			name: "invalid maxConnectionIdle",
			cfg: Grpc{
				Keepalive: Keepalive{
					MinTime:           "1s",
					MaxConnectionIdle: "bad",
				},
			},
			wantInErr: "maxConnectionIdle",
		},
		{
			name: "invalid maxConnectionAge",
			cfg: Grpc{
				Keepalive: Keepalive{
					MinTime:          "1s",
					MaxConnectionAge: "bad",
				},
			},
			wantInErr: "maxConnectionAge",
		},
		{
			name: "invalid maxConnectionAgeGrace",
			cfg: Grpc{
				Keepalive: Keepalive{
					MinTime:               "1s",
					MaxConnectionAgeGrace: "bad",
				},
			},
			wantInErr: "maxConnectionAgeGrace",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := LoadGrpcConfiguration(tt.cfg)
			if err == nil {
				t.Fatalf("expected error for %s, got nil", tt.wantInErr)
			}
			if !strings.Contains(err.Error(), tt.wantInErr) {
				t.Fatalf("expected error mentioning %q, got: %v", tt.wantInErr, err)
			}
		})
	}
}

func TestLoadGrpcConfigurationEmptyKeepaliveFieldsReturnZeroValueParams(t *testing.T) {
	cfg := Grpc{
		Keepalive: Keepalive{
			MinTime:               "1s",
			PermitWithoutStream:   true,
			Timeout:               "",
			IntervalTime:          "",
			MaxConnectionIdle:     "",
			MaxConnectionAge:      "",
			MaxConnectionAgeGrace: "",
		},
	}

	options, err := LoadGrpcConfiguration(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(options) != 1 {
		t.Fatalf("expected 1 server option (enforcement policy only, no server parameters for zero values), got %d", len(options))
	}
}

func TestLoadGrpcConfigurationEmptyKeepaliveStructUsesDefaults(t *testing.T) {
	cfg := Grpc{
		Keepalive: Keepalive{},
	}

	options, err := LoadGrpcConfiguration(cfg)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(options) != 1 {
		t.Fatalf("expected 1 server option (enforcement policy with default 1s MinTime), got %d", len(options))
	}
}
