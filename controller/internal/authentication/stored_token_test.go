package authentication_test

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	api "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
)

func TestStoredTokenAuthentication(t *testing.T) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	claims := jwt.RegisteredClaims{
		Issuer: "https://localhost:8085", Audience: []string{"jumpstarter"},
		Subject: "exporter:lab:board:uid", IssuedAt: jwt.NewNumericDate(time.Now()),
		ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Hour)),
	}
	sign := func(claims jwt.RegisteredClaims) string {
		t.Helper()
		token, err := jwt.NewWithClaims(jwt.SigningMethodES256, claims).SignedString(key)
		if err != nil {
			t.Fatal(err)
		}
		return token
	}
	stored := sign(claims)
	digest := sha256.Sum256([]byte(stored))
	primaryErr := errors.New("signature rejected")
	primaryResponse := &authenticator.Response{User: &user.DefaultInfo{Name: "external:user"}}
	for _, tc := range []struct {
		name                                     string
		primaryOK, retained, missing, readError  bool
		change                                   func(*jwt.RegisteredClaims)
		resign, malformed                        bool
		missingOwner, replacedOwner, clusterWide bool
		wantOK                                   bool
		wantReads                                int
	}{
		{name: "normal authentication never reads a Secret", primaryOK: true, readError: true, wantOK: true},
		{name: "exact token before reconciliation", wantOK: true, wantReads: 2},
		{name: "exact token after reconciliation", retained: true, wantOK: true, wantReads: 2},
		{name: "cluster-wide controller", clusterWide: true, wantOK: true, wantReads: 2},
		{name: "missing resource", missingOwner: true, retained: true, wantReads: 2},
		{name: "recreated resource", replacedOwner: true, retained: true, wantReads: 2},
		{name: "same claims signed again are not the stored token", resign: true, retained: true, wantReads: 1},
		{name: "changed identity with the same signing key", change: func(c *jwt.RegisteredClaims) { c.Subject = "exporter:lab:other:uid" }, wantReads: 1},
		{name: "wrong audience", change: func(c *jwt.RegisteredClaims) { c.Audience = []string{"telemetry"} }},
		{name: "wrong issuer", change: func(c *jwt.RegisteredClaims) { c.Issuer = "https://external" }},
		{name: "expired", change: func(c *jwt.RegisteredClaims) { c.ExpiresAt = jwt.NewNumericDate(time.Now().Add(-time.Minute)) }},
		{name: "expiry required", change: func(c *jwt.RegisteredClaims) { c.ExpiresAt = nil }},
		{name: "not yet valid", change: func(c *jwt.RegisteredClaims) { c.NotBefore = jwt.NewNumericDate(time.Now().Add(time.Hour)) }},
		{name: "invalid resource name", change: func(c *jwt.RegisteredClaims) { c.Subject = "exporter:lab:../other:uid" }},
		{name: "malformed", malformed: true},
		{name: "missing secret", missing: true, wantReads: 1},
		{name: "API failure is not acceptance", readError: true, wantReads: 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			presented := stored
			if tc.change != nil || tc.resign {
				modified := claims
				if tc.change != nil {
					tc.change(&modified)
				}
				presented = sign(modified)
			}
			if tc.malformed {
				presented = "not-a-token"
			}
			scheme := runtime.NewScheme()
			if err := corev1.AddToScheme(scheme); err != nil {
				t.Fatal(err)
			}
			if err := api.AddToScheme(scheme); err != nil {
				t.Fatal(err)
			}
			secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "board-exporter", Namespace: "lab"}, Data: map[string][]byte{"token": []byte(stored)}}
			if tc.retained {
				secret.Data["token"] = []byte("replacement")
				secret.Annotations = map[string]string{authentication.LegacyTokenHashAnnotation: hex.EncodeToString(digest[:])}
			}
			reads := 0
			builder := fake.NewClientBuilder().WithScheme(scheme).WithInterceptorFuncs(interceptor.Funcs{
				Get: func(ctx context.Context, c client.WithWatch, key client.ObjectKey, obj client.Object, opts ...client.GetOption) error {
					reads++
					if tc.readError {
						return errors.New("API unavailable")
					}
					return c.Get(ctx, key, obj, opts...)
				},
			})
			if !tc.missing {
				builder.WithObjects(secret)
			}
			if !tc.missingOwner {
				owner := &api.Exporter{ObjectMeta: metav1.ObjectMeta{Name: "board", Namespace: "lab", UID: "uid"}}
				if tc.replacedOwner {
					owner.UID = "new-uid"
				}
				builder.WithObjects(owner)
			}
			a := &authentication.StoredTokenAuthenticator{
				Reader: builder.Build(), Namespace: "lab", Issuer: claims.Issuer, Audience: "jumpstarter", Prefix: "internal:",
				Primary: authenticator.TokenFunc(func(context.Context, string) (*authenticator.Response, bool, error) {
					if tc.primaryOK {
						return primaryResponse, true, nil
					}
					return nil, false, primaryErr
				}),
			}
			if tc.clusterWide {
				a.Namespace = ""
			}
			response, ok, err := a.AuthenticateToken(context.Background(), presented)
			if ok != tc.wantOK || (err == nil) != tc.wantOK || reads != tc.wantReads {
				t.Fatalf("ok=%v err=%v reads=%d; want ok=%v reads=%d", ok, err, reads, tc.wantOK, tc.wantReads)
			}
			if tc.primaryOK && response != primaryResponse {
				t.Fatal("normal authentication response changed")
			}
			if ok && !tc.primaryOK && response.User.GetName() != "internal:"+claims.Subject {
				t.Fatal("wrong fallback identity")
			}
		})
	}
}
