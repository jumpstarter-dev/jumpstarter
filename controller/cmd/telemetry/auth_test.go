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
	"os"
	"path/filepath"
	"testing"
)

func TestLoadClientAuthenticatorUnset(t *testing.T) {
	t.Setenv("AUTHENTICATION_CONFIG", "")
	authn, err := loadClientAuthenticator(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if authn != nil {
		t.Fatalf("authenticator = %#v, want nil", authn)
	}
}

func TestLoadClientAuthenticatorEmptyJWT(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(path, []byte("authentication:\n  jwt: []\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AUTHENTICATION_CONFIG", path)
	authn, err := loadClientAuthenticator(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if authn != nil {
		t.Fatalf("authenticator = %#v, want nil", authn)
	}
}
