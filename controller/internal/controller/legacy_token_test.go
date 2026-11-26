package controller

import (
	"context"
	"crypto/elliptic"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"math/rand"
	"testing"

	"filippo.io/keygen"
	api "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

func TestSigningKeyMigration(t *testing.T) {
	seed := []byte("migration-test-key")
	hash := sha256.Sum256(seed)
	key, err := keygen.ECDSALegacy(elliptic.P256(), rand.New(rand.NewSource(int64(binary.BigEndian.Uint64(hash[:8])))))
	if err != nil {
		t.Fatal(err)
	}
	legacy := oidc.NewSigner(key, "https://localhost:8085", "jumpstarter")
	fromSeed := func(seed []byte) *oidc.Signer {
		t.Helper()
		signer, err := oidc.NewSignerFromSeed(seed, legacy.Issuer(), legacy.Audience())
		if err != nil {
			t.Fatal(err)
		}
		return signer
	}
	updated := fromSeed(seed)
	rotated := fromSeed([]byte("rotated-controller-key"))
	for migration, initial := range map[string]*oidc.Signer{
		"derivation upgrade":      legacy,
		"CONTROLLER_KEY rotation": fromSeed([]byte("initial-controller-key")),
	} {
		for _, kind := range []string{"client", "exporter"} {
			for _, allowed := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/fallback=%t", migration, kind, allowed), func(t *testing.T) {
					ctx := t.Context()
					scheme := runtime.NewScheme()
					if err := errors.Join(corev1.AddToScheme(scheme), api.AddToScheme(scheme)); err != nil {
						t.Fatal(err)
					}
					objectMeta := metav1.ObjectMeta{Name: "device", Namespace: "lab", UID: "uid"}
					var owner client.Object = &api.Client{ObjectMeta: objectMeta}
					if kind == "exporter" {
						owner = &api.Exporter{ObjectMeta: objectMeta}
					}
					kc := fake.NewClientBuilder().WithScheme(scheme).WithStatusSubresource(owner).WithObjects(owner).Build()
					request := reconcile.Request{NamespacedName: client.ObjectKeyFromObject(owner)}
					secretKey := client.ObjectKey{Name: "device-" + kind, Namespace: "lab"}
					reconcileWith := func(signer *oidc.Signer) *corev1.Secret {
						t.Helper()
						var reconciler reconcile.Reconciler = &ClientReconciler{Client: kc, Scheme: scheme, Signer: signer}
						if kind == "exporter" {
							reconciler = &ExporterReconciler{Client: kc, Scheme: scheme, Signer: signer}
						}
						if _, err := reconciler.Reconcile(ctx, request); err != nil {
							t.Fatal(err)
						}
						secret := &corev1.Secret{}
						if err := kc.Get(ctx, secretKey, secret); err != nil {
							t.Fatal(err)
						}
						if err := signer.Validate(string(secret.Data[TokenKey])); err != nil {
							t.Fatal(err)
						}
						return secret
					}
					signer := initial
					primary := authenticator.TokenFunc(func(_ context.Context, token string) (*authenticator.Response, bool, error) {
						if err := signer.Validate(token); err != nil {
							return nil, false, err
						}
						claims, err := authentication.StoredTokenClaims(token, signer.Issuer(), signer.Audience())
						if err != nil {
							return nil, false, err
						}
						return &authenticator.Response{User: &user.DefaultInfo{Name: "internal:" + claims.Subject}}, true, nil
					})
					fallback := &authentication.StoredTokenAuthenticator{
						Primary: primary, Cache: kc, Reader: kc, Namespace: "lab",
						Issuer: signer.Issuer(), Audience: signer.Audience(), Prefix: "internal:",
					}
					var auth authenticator.Token = primary
					if allowed {
						auth = fallback
					}
					checkAuthentication := func(token string, want bool) {
						t.Helper()
						response, ok, err := auth.AuthenticateToken(ctx, token)
						if ok != want || (err == nil) != want {
							t.Fatalf("authentication ok=%t err=%v; want %t", ok, err, want)
						}
						if ok && response.User.GetName() != "internal:"+kind+":lab:device:uid" {
							t.Fatal("wrong authenticated identity")
						}
					}
					oldToken := string(reconcileWith(signer).Data[TokenKey])
					checkAuthentication(oldToken, true)
					signer = updated
					secret := reconcileWith(signer)
					replacement := string(secret.Data[TokenKey])
					if secret.Annotations[authentication.LegacyTokenHashAnnotation] != TokenHash(oldToken) {
						t.Fatal("previous token hash not retained at regeneration")
					}
					checkAuthentication(oldToken, allowed)
					checkAuthentication(replacement, true)
					// Disabling fallback affects authentication, never stored credentials.
					auth = primary
					for range 2 {
						secret = reconcileWith(signer)
						if string(secret.Data[TokenKey]) != replacement || secret.Annotations[authentication.LegacyTokenHashAnnotation] != TokenHash(oldToken) {
							t.Fatal("reconcile with fallback off changed the retained credential")
						}
						checkAuthentication(oldToken, false)
					}
					auth = fallback
					checkAuthentication(oldToken, true)
					// A second key change replaces the retained generation.
					signer = rotated
					secret = reconcileWith(signer)
					if secret.Annotations[authentication.LegacyTokenHashAnnotation] != TokenHash(replacement) {
						t.Fatal("second key change did not retain the immediately previous token")
					}
					checkAuthentication(oldToken, false)
					checkAuthentication(replacement, true)
					checkAuthentication(string(secret.Data[TokenKey]), true)
				})
			}
		}
	}
}
