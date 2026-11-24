package config

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"encoding/pem"
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
	api "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	apiserverinstall "k8s.io/apiserver/pkg/apis/apiserver/install"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func internalBearerAuthenticator(t *testing.T, signer *oidc.Signer, certificateAuthority string) *authentication.BearerTokenAuthenticator {
	t.Helper()
	scheme := runtime.NewScheme()
	apiserverinstall.Install(scheme)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	authn, _, err := LoadAuthenticationConfiguration(ctx, scheme, Authentication{}, signer, certificateAuthority)
	if err != nil {
		t.Fatal(err)
	}
	return authentication.NewBearerTokenAuthenticator(authn)
}

func authenticateBearer(auth *authentication.BearerTokenAuthenticator, token string) (bool, error) {
	ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs("authorization", "Bearer "+token))
	_, ok, err := auth.AuthenticateContext(ctx)
	return ok, err
}

func signedToken(t *testing.T, key *ecdsa.PrivateKey, issuer, audience string, expiresAt time.Time) string {
	t.Helper()
	token, err := jwt.NewWithClaims(jwt.SigningMethodES256, jwt.RegisteredClaims{
		Issuer:    issuer,
		Subject:   "exporter:default:test:uid",
		Audience:  []string{audience},
		IssuedAt:  jwt.NewNumericDate(expiresAt.Add(-time.Hour)),
		ExpiresAt: jwt.NewNumericDate(expiresAt),
	}).SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return token
}

func generateKey(t *testing.T) *ecdsa.PrivateKey {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return key
}

// The internal issuer is verified through the same OIDC authenticator as
// external issuers; its errors decide whether callers retry or re-authenticate.
func TestInternalTokenAuthenticationErrorCodes(t *testing.T) {
	gin.SetMode(gin.TestMode)
	engine := gin.New()
	server := httptest.NewTLSServer(engine)
	t.Cleanup(server.Close)
	key := generateKey(t)
	signer := oidc.NewSigner(key, server.URL, "jumpstarter")
	signer.Register(engine)
	certificateAuthority := string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}))
	auth := internalBearerAuthenticator(t, signer, certificateAuthority)

	now := time.Now()
	valid := signedToken(t, key, signer.Issuer(), signer.Audience(), now.Add(time.Hour))
	// Discovery runs in the background after the authenticator is created.
	deadline := time.Now().Add(5 * time.Second)
	for {
		ok, err := authenticateBearer(auth, valid)
		if ok && err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("authenticator did not initialize: %v", err)
		}
		time.Sleep(10 * time.Millisecond)
	}

	ok, err := authenticateBearer(auth, signedToken(t, key, signer.Issuer(), signer.Audience(), now.Add(-time.Minute)))
	if ok || status.Code(err) != codes.Unauthenticated || !strings.Contains(err.Error(), "token is expired") {
		t.Fatalf("expired token: ok=%v err=%v, want Unauthenticated naming the expiry", ok, err)
	}

	// Telemetry tokens are signed with the controller key for another audience;
	// they must not work as controller credentials.
	telemetryToken, err := signer.WithAudience(oidc.TelemetryAudience).Token("exporter:default:test:uid")
	if err != nil {
		t.Fatal(err)
	}
	if ok, err := authenticateBearer(auth, telemetryToken); ok || err == nil {
		t.Fatalf("telemetry token: ok=%v err=%v, want rejection", ok, err)
	}

	// A signature failure can also mean the issuer's keys could not be fetched.
	ok, err = authenticateBearer(auth, signedToken(t, generateKey(t), signer.Issuer(), signer.Audience(), now.Add(time.Hour)))
	if ok || err == nil {
		t.Fatalf("foreign key: ok=%v err=%v, want rejection", ok, err)
	}
	if _, isStatus := status.FromError(err); isStatus {
		t.Fatalf("foreign key: got status %v, want the authenticator's own error", err)
	}
}

func TestInternalTokenAuthenticationBeforeDiscovery(t *testing.T) {
	key := generateKey(t)
	// Nothing listens here, so discovery cannot complete during the test.
	signer := oidc.NewSigner(key, "https://127.0.0.1:1", "jumpstarter")
	auth := internalBearerAuthenticator(t, signer, "")
	now := time.Now()

	ok, err := authenticateBearer(auth, signedToken(t, key, signer.Issuer(), signer.Audience(), now.Add(time.Hour)))
	if ok || status.Code(err) != codes.Unavailable {
		t.Fatalf("valid token before discovery: ok=%v err=%v, want Unavailable", ok, err)
	}
	ok, err = authenticateBearer(auth, signedToken(t, key, signer.Issuer(), signer.Audience(), now.Add(-time.Minute)))
	if ok || status.Code(err) != codes.Unauthenticated {
		t.Fatalf("expired token before discovery: ok=%v err=%v, want Unauthenticated", ok, err)
	}
}

func TestStoredTokenFallbackDefault(t *testing.T) {
	scheme := runtime.NewScheme()
	apiserverinstall.Install(scheme)
	if err := api.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	signer := oidc.NewSigner(generateKey(t), "https://127.0.0.1:1", "jumpstarter")
	for _, format := range []string{"config", "authentication"} {
		for _, setting := range []string{"", "allowStoredTokenFallback: false", "allowStoredTokenFallback: true"} {
			t.Run(format+"/"+setting, func(t *testing.T) {
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				raw := fmt.Sprintf("authentication:\n  internal:\n    prefix: 'internal:'\n    %s\n", setting)
				if format == "authentication" {
					raw = fmt.Sprintf("apiVersion: %s\nkind: AuthenticationConfiguration\ninternal:\n  prefix: 'internal:'\n  %s\n", api.GroupVersion, setting)
				}
				cm := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "controller", Namespace: "lab"},
					Data: map[string]string{"router": "{}", format: raw}}
				kc := fake.NewClientBuilder().WithScheme(scheme).WithObjects(cm).Build()
				loaded, err := LoadConfiguration(ctx, kc, scheme, client.ObjectKeyFromObject(cm), signer, "")
				if err != nil {
					t.Fatal(err)
				}
				if loaded.AllowStoredTokenFallback != (setting != "allowStoredTokenFallback: false") {
					t.Fatal("wrong stored-token fallback default or explicit override")
				}
			})
		}
	}
}
