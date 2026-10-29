package oidc

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

func newTestSigner(t *testing.T) *Signer {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return NewSigner(key, "https://test", "test-audience")
}

func parseTokenExpiry(t *testing.T, tokenStr string) time.Time {
	t.Helper()
	claims := &jwt.RegisteredClaims{}
	parser := jwt.NewParser(jwt.WithoutClaimsValidation())
	_, _, err := parser.ParseUnverified(tokenStr, claims)
	if err != nil {
		t.Fatal(err)
	}
	if claims.ExpiresAt == nil {
		t.Fatal("token has no expiry")
	}
	return claims.ExpiresAt.Time
}

func TestTokenDefaultLifetime(t *testing.T) {
	signer := newTestSigner(t)

	before := time.Now()
	token, err := signer.Token("test-subject")
	if err != nil {
		t.Fatal(err)
	}

	expiry := parseTokenExpiry(t, token)
	expectedMin := before.Add(defaultTokenLifetime - time.Minute)
	expectedMax := before.Add(defaultTokenLifetime + time.Minute)

	if expiry.Before(expectedMin) || expiry.After(expectedMax) {
		t.Errorf("expected expiry around %v, got %v", before.Add(defaultTokenLifetime), expiry)
	}
}

func TestTokenCustomLifetime(t *testing.T) {
	signer := newTestSigner(t)
	signer.SetTokenLifetime(2 * time.Hour)

	before := time.Now()
	token, err := signer.Token("test-subject")
	if err != nil {
		t.Fatal(err)
	}

	expiry := parseTokenExpiry(t, token)
	expectedMin := before.Add(2*time.Hour - time.Minute)
	expectedMax := before.Add(2*time.Hour + time.Minute)

	if expiry.Before(expectedMin) || expiry.After(expectedMax) {
		t.Errorf("expected expiry around %v, got %v", before.Add(2*time.Hour), expiry)
	}
}

func TestTokenValidateRoundTrip(t *testing.T) {
	signer := newTestSigner(t)

	token, err := signer.Token("test-subject")
	if err != nil {
		t.Fatal(err)
	}

	if err := signer.Validate(token); err != nil {
		t.Errorf("expected valid token, got error: %v", err)
	}
}

func TestSetTokenLifetimeOverridesDefault(t *testing.T) {
	signer := newTestSigner(t)

	signer.SetTokenLifetime(30 * 24 * time.Hour)

	before := time.Now()
	token, err := signer.Token("test-subject")
	if err != nil {
		t.Fatal(err)
	}

	expiry := parseTokenExpiry(t, token)
	diff := expiry.Sub(before)

	if diff < 29*24*time.Hour || diff > 31*24*time.Hour {
		t.Errorf("expected ~30 day lifetime, got %v", diff)
	}
}

func TestNewSignerFromSeedDeterministic(t *testing.T) {
	seed := []byte("test-seed-value")

	s1, err := NewSignerFromSeed(seed, "https://test", "aud")
	if err != nil {
		t.Fatal(err)
	}

	s2, err := NewSignerFromSeed(seed, "https://test", "aud")
	if err != nil {
		t.Fatal(err)
	}

	token1, err := s1.Token("subject")
	if err != nil {
		t.Fatal(err)
	}
	if err := s2.Validate(token1); err != nil {
		t.Error("tokens from same seed should validate against each other")
	}
}

func TestTokenLifetime(t *testing.T) {
	signer := newTestSigner(t)
	now := time.Now().Truncate(time.Second)
	for _, tc := range []struct {
		name      string
		claims    jwt.RegisteredClaims
		malformed bool
		want      time.Duration
	}{
		{name: "issued and expiry", claims: jwt.RegisteredClaims{IssuedAt: jwt.NewNumericDate(now), ExpiresAt: jwt.NewNumericDate(now.Add(time.Hour))}, want: time.Hour},
		{name: "missing issued", claims: jwt.RegisteredClaims{ExpiresAt: jwt.NewNumericDate(now.Add(time.Hour))}},
		{name: "missing expiry", claims: jwt.RegisteredClaims{IssuedAt: jwt.NewNumericDate(now)}},
		{name: "malformed", malformed: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			token := "invalid-token"
			if !tc.malformed {
				var err error
				token, err = jwt.NewWithClaims(jwt.SigningMethodHS256, tc.claims).SignedString([]byte("test-key"))
				if err != nil {
					t.Fatal(err)
				}
			}
			got, err := signer.TokenLifetime(token)
			if (err != nil) != tc.malformed {
				t.Fatalf("TokenLifetime error = %v, malformed = %v", err, tc.malformed)
			}
			if got != tc.want {
				t.Errorf("TokenLifetime = %v, want %v", got, tc.want)
			}
		})
	}
}
