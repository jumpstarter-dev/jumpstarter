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

import "testing"

func TestLokiLogTargetKeepsSchemeAndHost(t *testing.T) {
	got := lokiLogTarget("http://loki:3100/loki/api/v1/push")
	if got != "http://loki:3100" {
		t.Fatalf("got %q, want scheme and host", got)
	}
}

func TestLokiLogTargetRedactsUserinfoAndQuery(t *testing.T) {
	raw := "https://user:s3cret@loki.example:3100/loki/api/v1/push?token=abc#frag"
	got := lokiLogTarget(raw)
	if got != "https://loki.example:3100" {
		t.Fatalf("got %q, want scheme and host only", got)
	}
	if got == raw {
		t.Fatal("startup log target must not be the raw Loki URL")
	}
}

func TestLokiLogTargetOmitsUnusableURL(t *testing.T) {
	for _, raw := range []string{"", "://", "not a url", "http://"} {
		if got := lokiLogTarget(raw); got != "" {
			t.Errorf("lokiLogTarget(%q) = %q, want empty", raw, got)
		}
	}
}
