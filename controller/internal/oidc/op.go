package oidc

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/sha256"
	"encoding/binary"
	"math/rand"
	"strings"
	"time"

	"filippo.io/keygen"
	"github.com/gin-gonic/gin"
	"github.com/go-jose/go-jose/v4"
	"github.com/golang-jwt/jwt/v5"
	"github.com/zitadel/oidc/v3/pkg/oidc"
	"github.com/zitadel/oidc/v3/pkg/op"
)

type Signer struct {
	privatekey *ecdsa.PrivateKey
	issuer     string
	audience   string
	prefix     string
}

func NewSigner(privateKey *ecdsa.PrivateKey, issuer, audience, prefix string) *Signer {
	return &Signer{
		privatekey: privateKey,
		issuer:     issuer,
		audience:   audience,
		prefix:     prefix,
	}
}

func NewSignerFromSeed(seed []byte, issuer, audience, prefix string) (*Signer, error) {
	hash := sha256.Sum256(seed)
	source := rand.NewSource(int64(binary.BigEndian.Uint64(hash[:8])))
	reader := rand.New(source)
	key, err := keygen.ECDSALegacy(elliptic.P256(), reader)
	if err != nil {
		return nil, err
	}
	return NewSigner(key, issuer, audience, prefix), nil
}

func (k *Signer) Issuer() string {
	return k.issuer
}

func (k *Signer) Audience() string {
	return k.audience
}

func (k *Signer) Prefix() string {
	return k.prefix
}

func (k *Signer) ID() string {
	return "default"
}

func (k *Signer) Algorithm() jose.SignatureAlgorithm {
	return jose.ES256
}

func (k *Signer) Use() string {
	return "sig"
}

func (k *Signer) Key() any {
	return k.privatekey.Public()
}

func (k *Signer) KeySet(context.Context) ([]op.Key, error) {
	return []op.Key{k}, nil
}

func (k *Signer) Register(group gin.IRoutes) {
	group.GET("/.well-known/openid-configuration", func(c *gin.Context) {
		op.Discover(c.Writer, &oidc.DiscoveryConfiguration{
			Issuer:  k.issuer,
			JwksURI: k.issuer + "/jwks",
		})
	})

	group.GET("/jwks", func(c *gin.Context) {
		op.Keys(c.Writer, c.Request, k)
	})
}

func (k *Signer) Token(
	subject string,
) (string, error) {
	if !strings.HasPrefix(subject, k.prefix) {
		return "placeholder for external OIDC provider access token", nil
	}
	return jwt.NewWithClaims(jwt.SigningMethodES256, jwt.RegisteredClaims{
		Issuer:    k.issuer,
		Subject:   strings.TrimPrefix(subject, k.prefix),
		Audience:  []string{k.audience},
		IssuedAt:  jwt.NewNumericDate(time.Now()),
		ExpiresAt: jwt.NewNumericDate(time.Now().Add(365 * 24 * time.Hour)), // FIXME: rotate keys on expiration
	}).SignedString(k.privatekey)
}
