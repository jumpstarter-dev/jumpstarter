package oidc

import (
	"testing"
)

func TestNewSignerWithRandomKeySignsAndValidatesTokens(t *testing.T) {
	signer, err := NewSignerWithRandomKey("https://example.com", "test-audience")
	if err != nil {
		t.Fatalf("NewSignerWithRandomKey returned error: %v", err)
	}

	token, err := signer.Token("test-subject")
	if err != nil {
		t.Fatalf("Token returned error: %v", err)
	}

	if token == "" {
		t.Fatal("Token returned empty string")
	}

	err = signer.Validate(token)
	if err != nil {
		t.Fatalf("Validate returned error for valid token: %v", err)
	}
}

func TestNewSignerWithRandomKeyProducesDifferentKeys(t *testing.T) {
	signer1, err := NewSignerWithRandomKey("https://example.com", "test-audience")
	if err != nil {
		t.Fatalf("first NewSignerWithRandomKey returned error: %v", err)
	}

	signer2, err := NewSignerWithRandomKey("https://example.com", "test-audience")
	if err != nil {
		t.Fatalf("second NewSignerWithRandomKey returned error: %v", err)
	}

	if signer1.privatekey.Equal(signer2.privatekey) {
		t.Fatal("two calls to NewSignerWithRandomKey produced identical private keys")
	}
}
