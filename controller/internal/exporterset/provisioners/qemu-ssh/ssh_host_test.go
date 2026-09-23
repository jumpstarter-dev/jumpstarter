/*
Copyright 2026 The Jumpstarter Authors

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

package qemussh

import (
	"testing"
)

func TestSanitizeDiff_redactsTokens(t *testing.T) {
	cases := []struct {
		name  string
		input string
		want  string
	}{
		{
			name:  "token field",
			input: `token: eyJhbGciOiJSUzI1NiJ9.abc`,
			want:  `token: [REDACTED]`,
		},
		{
			name:  "password field",
			input: `password: s3cret123`,
			want:  `password: [REDACTED]`,
		},
		{
			name:  "key equals",
			input: `SECRET_KEY=abcdef12345`,
			want:  `SECRET_KEY=[REDACTED]`,
		},
		{
			name:  "mixed case credential",
			input: `Credential: some-value`,
			want:  `Credential: [REDACTED]`,
		},
		{
			name:  "non-sensitive unchanged",
			input: `endpoint: https://example.com`,
			want:  `endpoint: https://example.com`,
		},
		{
			name:  "multiline with token",
			input: "endpoint: https://example.com\ntoken: abc123\nname: test",
			want:  "endpoint: https://example.com\ntoken: [REDACTED]\nname: test",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := SanitizeDiff(tc.input)
			if got != tc.want {
				t.Errorf("SanitizeDiff(%q) = %q, want %q", tc.input, got, tc.want)
			}
		})
	}
}

func TestSSHConnectConfig_defaultPort(t *testing.T) {
	cfg := SSHConnectConfig{
		Host: "example.com",
		User: "jumpstarter",
	}
	if cfg.Port != 0 {
		t.Errorf("Port = %d, want 0 (defaulted in Connect())", cfg.Port)
	}
}
