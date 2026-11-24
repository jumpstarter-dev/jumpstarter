package authentication

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"strings"

	"github.com/golang-jwt/jwt/v5"
	api "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/util/validation"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

const LegacyTokenHashAnnotation = "jumpstarter.dev/legacy-token-hash"

// StoredTokenAuthenticator is a last-resort migration path for credentials issued
// before a signing-key change. It trusts exact credentials in Kubernetes, never
// signatures made with an old key. Cache rejects fabricated credentials without
// API-server reads. Reader must bypass the informer cache so a
// removed allowance cannot continue authenticating through a stale Secret.
type StoredTokenAuthenticator struct {
	Primary   authenticator.Token
	Cache     client.Reader
	Reader    client.Reader
	Namespace string
	Issuer    string
	Audience  string
	Prefix    string
}

func (a *StoredTokenAuthenticator) AuthenticateToken(ctx context.Context, token string) (*authenticator.Response, bool, error) {
	response, ok, err := a.Primary.AuthenticateToken(ctx, token)
	if err == nil && ok {
		return response, true, nil
	}
	secret, claims, matchErr := a.Match(ctx, token)
	if matchErr != nil {
		return nil, false, matchErr
	}
	if secret == nil {
		return response, false, err
	}
	log.FromContext(ctx).Info("WARNING: legacy credential accepted; update the local token from its Secret",
		"namespace", secret.Namespace, "secret", secret.Name)
	return &authenticator.Response{User: &user.DefaultInfo{Name: a.Prefix + claims.Subject}}, true, nil
}

// StoredTokenClaims checks the shape and lifetime of a candidate credential.
// Its claims are NOT authenticated until the exact token matches trusted storage.
func StoredTokenClaims(token, issuer, audience string) (*jwt.RegisteredClaims, error) {
	claims := &jwt.RegisteredClaims{}
	parsed, _, err := jwt.NewParser().ParseUnverified(token, claims)
	if err != nil {
		return nil, err
	}
	if parsed.Method.Alg() != jwt.SigningMethodES256.Alg() {
		return nil, fmt.Errorf("stored credential must use ES256")
	}
	if err := jwt.NewValidator(jwt.WithIssuer(issuer), jwt.WithAudience(audience),
		jwt.WithExpirationRequired(), jwt.WithIssuedAt()).Validate(claims); err != nil {
		return nil, err
	}
	parts := strings.Split(claims.Subject, ":")
	if len(parts) != 4 || (parts[0] != "client" && parts[0] != "exporter") || parts[3] == "" ||
		len(validation.IsDNS1123Label(parts[1])) != 0 || len(validation.IsDNS1123Subdomain(parts[2])) != 0 {
		return nil, fmt.Errorf("invalid internal credential subject")
	}
	return claims, nil
}

// Match returns the Secret only for an exact, unexpired stored credential.
// Namespace limits lookups to the controller's configured namespace. In
// cluster-wide mode, the subject supplies the namespace. After an exact match,
// the subject must still belong to the live resource, including its UID.
func (a *StoredTokenAuthenticator) Match(ctx context.Context, token string) (*corev1.Secret, *jwt.RegisteredClaims, error) {
	claims, err := StoredTokenClaims(token, a.Issuer, a.Audience)
	if err != nil {
		return nil, nil, nil
	}
	parts := strings.Split(claims.Subject, ":")
	namespace := a.Namespace
	if namespace == "" {
		namespace = parts[1]
	}
	var secret corev1.Secret
	digest := sha256.Sum256([]byte(token))
	for _, reader := range []client.Reader{a.Cache, a.Reader} {
		secret = corev1.Secret{}
		if err := reader.Get(ctx, client.ObjectKey{Namespace: namespace, Name: parts[2] + "-" + parts[0]}, &secret); err != nil {
			if apierrors.IsNotFound(err) {
				return nil, nil, nil
			}
			return nil, nil, fmt.Errorf("read stored credential: %w", err)
		}
		if subtle.ConstantTimeCompare([]byte(token), secret.Data["token"]) != 1 &&
			subtle.ConstantTimeCompare([]byte(hex.EncodeToString(digest[:])), []byte(secret.Annotations[LegacyTokenHashAnnotation])) != 1 {
			return nil, nil, nil
		}
	}
	var owner interface {
		client.Object
		InternalSubject() string
	} = &api.Client{}
	if parts[0] == "exporter" {
		owner = &api.Exporter{}
	}
	if err := a.Reader.Get(ctx, client.ObjectKey{Namespace: namespace, Name: parts[2]}, owner); err != nil {
		if apierrors.IsNotFound(err) {
			return nil, nil, nil
		}
		return nil, nil, fmt.Errorf("read credential owner: %w", err)
	}
	if owner.InternalSubject() != claims.Subject {
		return nil, nil, nil
	}
	return &secret, claims, nil
}
