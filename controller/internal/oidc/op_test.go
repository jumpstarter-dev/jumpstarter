package oidc

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	mathrand "math/rand"
	"testing"
	"time"

	"filippo.io/keygen"
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

func TestNewSignerFromSeedDoesNotUseTruncatedMathRandSeed(t *testing.T) {
	seed := []byte("test-seed-value")

	signer, err := NewSignerFromSeed(seed, "https://test", "aud")
	if err != nil {
		t.Fatal(err)
	}

	if signer.privatekey.Equal(truncatedMathRandSigningKey(t, seed)) {
		t.Fatal("signing key matches the math/rand derivation, whose seed space is only 2^31 states")
	}
}

func truncatedMathRandSigningKey(t *testing.T, seed []byte) *ecdsa.PrivateKey {
	t.Helper()
	hash := sha256.Sum256(seed)
	source := mathrand.NewSource(int64(binary.BigEndian.Uint64(hash[:8])))
	key, err := keygen.ECDSALegacy(elliptic.P256(), mathrand.New(source))
	if err != nil {
		t.Fatal(err)
	}
	return key
}

func TestNewSignerFromSeedKeepsDerivedKeyStable(t *testing.T) {
	signer, err := NewSignerFromSeed([]byte("test-seed-value"), "https://test", "aud")
	if err != nil {
		t.Fatal(err)
	}

	publicKey, err := signer.privatekey.PublicKey.ECDH()
	if err != nil {
		t.Fatal(err)
	}

	const want = "0419ac797993ef89bcaa96043d92f1628004460b60a986fea259cfe5833c07f6580eb9116e6f1d4ce286487bd21b6c5409e8088843f09498eaa2210fd44c470f38"
	if got := hex.EncodeToString(publicKey.Bytes()); got != want {
		t.Fatalf("derived public key changed, which invalidates every token the controller has issued: got %s", got)
	}
}
