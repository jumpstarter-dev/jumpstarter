/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

// Package e2e exercises the full JEP-0014 admin API surface end-to-end
// against an envtest-backed kube-apiserver and an in-process gRPC server
// wired with the production OIDC + SAR + impersonation pipeline. The REST
// (grpc-gateway) surface is also exercised, with a thin auth shim that
// mirrors what the gRPC interceptors do — production currently registers
// the gateway HandlerServer without an HTTP auth middleware, which is a
// known gap noted on the gateway tests.
package e2e

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	mathrand "math/rand"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"filippo.io/keygen"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"github.com/grpc-ecosystem/go-grpc-middleware/v2/interceptors/recovery"
	gwruntime "github.com/grpc-ecosystem/grpc-gateway/v2/runtime"
	"github.com/zitadel/oidc/v3/pkg/oidc"
	zitadelop "github.com/zitadel/oidc/v3/pkg/op"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"
	rbacv1 "k8s.io/api/rbac/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	apiserverinstall "k8s.io/apiserver/pkg/apis/apiserver/install"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	adminauth "github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/auth"
	adminauthz "github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/authz"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/impersonation"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	jsoidc "github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	adminv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	adminsvcv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/service/admin/v1"
)

// Suite-wide state. All variables are populated in BeforeSuite and torn
// down in AfterSuite; tests read them through helpers in this package.
var (
	suiteCtx    context.Context
	suiteCancel context.CancelFunc

	cfg         *rest.Config
	k8sClient   client.Client
	watchClient client.WithWatch
	testEnv     *envtest.Environment

	// signer is the OIDC issuer the suite signs JWTs with. Its public JWKS
	// is hosted on signerHTTP so the controller's k8s OIDC validator can
	// fetch keys to verify tokens.
	signer        *jsoidc.Signer
	signerKey     *ecdsa.PrivateKey
	signerHTTP    *httptest.Server
	signerCertPEM string

	// altSigner is a *different* signer with a *different* keypair, used
	// to mint tokens that should be rejected (signature mismatch under the
	// trusted issuer URL).
	altSigner    *jsoidc.Signer
	altSignerKey *ecdsa.PrivateKey

	adminAuthN *adminauth.MultiIssuerAuthenticator
	adminAuthZ *adminauthz.Authorizer
	impFactory *impersonation.Factory

	// gRPC plumbing — bufconn so we never bind a real port.
	grpcServer *grpc.Server
	grpcLis    *bufconn.Listener

	// REST plumbing — httptest with an auth shim wrapping the gateway mux.
	restServer *httptest.Server
)

func TestAdminE2E(t *testing.T) {
	RegisterFailHandler(Fail)
	RunSpecs(t, "Admin API E2E Suite")
}

var _ = BeforeSuite(func() {
	logf.SetLogger(zap.New(zap.WriteTo(GinkgoWriter), zap.UseDevMode(true)))

	suiteCtx, suiteCancel = context.WithCancel(context.Background())

	By("starting envtest with Jumpstarter CRDs")
	testEnv = &envtest.Environment{
		CRDDirectoryPaths: []string{
			filepath.Join("..", "..", "..", "deploy", "helm", "jumpstarter", "charts", "jumpstarter-controller", "templates", "crds"),
		},
		ErrorIfCRDPathMissing: true,
		BinaryAssetsDirectory: filepath.Join("..", "..", "..", "bin", "k8s",
			fmt.Sprintf("1.30.0-%s-%s", runtime.GOOS, runtime.GOARCH)),
	}
	var err error
	cfg, err = testEnv.Start()
	Expect(err).NotTo(HaveOccurred())
	Expect(cfg).NotTo(BeNil())

	Expect(jumpstarterdevv1alpha1.AddToScheme(scheme.Scheme)).To(Succeed())
	Expect(rbacv1.AddToScheme(scheme.Scheme)).To(Succeed())
	// LoadAuthenticationConfiguration converts apiserver.JWTAuthenticator
	// between versioned and internal forms; the conversion is registered by
	// the apiserver "install" package.
	apiserverinstall.Install(scheme.Scheme)

	k8sClient, err = client.New(cfg, client.Options{Scheme: scheme.Scheme})
	Expect(err).NotTo(HaveOccurred())
	watchClient, err = client.NewWithWatch(cfg, client.Options{Scheme: scheme.Scheme})
	Expect(err).NotTo(HaveOccurred())

	By("starting OIDC discovery + JWKS server")
	bringUpOIDCServer()

	By("loading multi-issuer authentication configuration")
	authn, _, err := jsoidc.LoadAuthenticationConfiguration(
		suiteCtx,
		scheme.Scheme,
		[]byte(authnConfigYAML),
		signer,
		signerCertPEM,
	)
	Expect(err).NotTo(HaveOccurred())

	bearer := authentication.NewBearerTokenAuthenticator(authn)
	adminAuthN = adminauth.NewMultiIssuerAuthenticator(bearer)
	// Production wiring: the namespace extractor lets namespace-scoped
	// RoleBindings authorize admin RPCs. The third argument used to be
	// nil, which forced every SAR to cluster scope.
	adminAuthZ = adminauthz.NewAuthorizer(k8sClient, "jumpstarter.dev", adminauthz.NamespaceFromAdminRequest)
	impFactory = impersonation.NewFactory(cfg, client.Options{Scheme: scheme.Scheme})

	By("starting in-process gRPC + REST gateway")
	bringUpServers()
})

var _ = AfterSuite(func() {
	if grpcServer != nil {
		grpcServer.GracefulStop()
	}
	if grpcLis != nil {
		_ = grpcLis.Close()
	}
	if restServer != nil {
		restServer.Close()
	}
	if signerHTTP != nil {
		signerHTTP.Close()
	}
	if suiteCancel != nil {
		suiteCancel()
	}
	if testEnv != nil {
		Expect(testEnv.Stop()).To(Succeed())
	}
})

// authnConfigYAML is the in-test AuthenticationConfiguration. We pass our
// `signer` as the second argument to LoadAuthenticationConfiguration so it
// is appended as the trusted internal issuer (username from "sub" with
// prefix "internal:"). No additional external issuers are needed for the
// admin pipeline tests — the existing authn glue is the same code path.
const authnConfigYAML = `
apiVersion: jumpstarter.dev/v1alpha1
kind: AuthenticationConfiguration
internal:
  prefix: "internal:"
jwt: []
`

// bringUpOIDCServer creates a fresh ECDSA signer, a self-signed TLS cert
// for "127.0.0.1", binds an httptest server on a free port, and exposes
// the discovery + JWKS endpoints the k8s OIDC validator probes. The cert
// PEM is later handed to LoadAuthenticationConfiguration so the validator
// trusts the in-test certificate authority.
func bringUpOIDCServer() {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	Expect(err).NotTo(HaveOccurred())
	addr := listener.Addr().String()
	issuer := "https://" + addr

	signerKey, err = newDeterministicECDSAKey([]byte("e2e-primary"))
	Expect(err).NotTo(HaveOccurred())
	signer = jsoidc.NewSigner(signerKey, issuer, "jumpstarter")

	altSignerKey, err = newDeterministicECDSAKey([]byte("e2e-alt"))
	Expect(err).NotTo(HaveOccurred())
	altSigner = jsoidc.NewSigner(altSignerKey, issuer, "jumpstarter")

	certPEM, keyPEM, err := makeSelfSignedCert("127.0.0.1")
	Expect(err).NotTo(HaveOccurred())
	signerCertPEM = string(certPEM)
	keyPair, err := tls.X509KeyPair(certPEM, keyPEM)
	Expect(err).NotTo(HaveOccurred())

	mux := http.NewServeMux()
	mux.HandleFunc("/.well-known/openid-configuration", func(w http.ResponseWriter, _ *http.Request) {
		zitadelop.Discover(w, &oidc.DiscoveryConfiguration{
			Issuer:  signer.Issuer(),
			JwksURI: signer.Issuer() + "/jwks",
		})
	})
	mux.HandleFunc("/jwks", func(w http.ResponseWriter, r *http.Request) {
		zitadelop.Keys(w, r, signer)
	})

	srv := &http.Server{
		Handler:   mux,
		TLSConfig: &tls.Config{Certificates: []tls.Certificate{keyPair}},
	}
	signerHTTP = &httptest.Server{
		Listener: listener,
		Config:   srv,
		URL:      issuer,
		TLS:      srv.TLSConfig,
	}
	go func() {
		_ = srv.ServeTLS(listener, "", "")
	}()

	// Block until discovery is reachable so the k8s OIDC validator (which
	// builds an HTTP client at construction time) sees a healthy endpoint.
	pool := x509.NewCertPool()
	Expect(pool.AppendCertsFromPEM(certPEM)).To(BeTrue())
	hc := &http.Client{
		Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool}},
		Timeout:   2 * time.Second,
	}
	Eventually(func() error {
		resp, err := hc.Get(issuer + "/.well-known/openid-configuration")
		if err != nil {
			return err
		}
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			return fmt.Errorf("status %d", resp.StatusCode)
		}
		return nil
	}, 5*time.Second, 100*time.Millisecond).Should(Succeed())
}

func bringUpServers() {
	leaseSvc := adminsvcv1.NewLeaseService(impFactory, watchClient, 10)
	exporterSvc := adminsvcv1.NewExporterService(impFactory, watchClient)
	clientSvc := adminsvcv1.NewClientService(impFactory, watchClient)
	webhookSvc := adminsvcv1.NewWebhookService(impFactory)

	grpcServer = grpc.NewServer(
		grpc.ChainUnaryInterceptor(
			recovery.UnaryServerInterceptor(),
			adminAuthN.UnaryServerInterceptor(),
			adminAuthZ.UnaryServerInterceptor(),
		),
		grpc.ChainStreamInterceptor(
			recovery.StreamServerInterceptor(),
			adminAuthN.StreamServerInterceptor(),
			adminAuthZ.StreamServerInterceptor(),
		),
	)
	adminv1pb.RegisterLeaseServiceServer(grpcServer, leaseSvc)
	adminv1pb.RegisterExporterServiceServer(grpcServer, exporterSvc)
	adminv1pb.RegisterClientServiceServer(grpcServer, clientSvc)
	adminv1pb.RegisterWebhookServiceServer(grpcServer, webhookSvc)

	grpcLis = bufconn.Listen(1 << 20)
	go func() {
		_ = grpcServer.Serve(grpcLis)
	}()

	gwmux := gwruntime.NewServeMux()
	Expect(adminv1pb.RegisterLeaseServiceHandlerServer(suiteCtx, gwmux, leaseSvc)).To(Succeed())
	Expect(adminv1pb.RegisterExporterServiceHandlerServer(suiteCtx, gwmux, exporterSvc)).To(Succeed())
	Expect(adminv1pb.RegisterClientServiceHandlerServer(suiteCtx, gwmux, clientSvc)).To(Succeed())
	Expect(adminv1pb.RegisterWebhookServiceHandlerServer(suiteCtx, gwmux, webhookSvc)).To(Succeed())

	// Use the production HTTP middleware, the same one controller_service.go
	// wraps gwmux with at startup.
	restServer = httptest.NewServer(adminauth.NewHTTPMiddleware(adminAuthN, adminAuthZ).Wrap(gwmux))
}

// newDeterministicECDSAKey produces an ECDSA P-256 key derived
// deterministically from seed. Mirrors NewSignerFromSeed's derivation so
// tests stay reproducible (and reuse filippo.io/keygen the production
// signer already depends on).
func newDeterministicECDSAKey(seed []byte) (*ecdsa.PrivateKey, error) {
	hash := newHash(seed)
	src := mathrand.NewSource(int64(hash))
	r := mathrand.New(src)
	return keygen.ECDSALegacy(elliptic.P256(), r)
}

// newHash collapses seed into an int64 by xoring 8-byte chunks.
func newHash(seed []byte) uint64 {
	h := uint64(1469598103934665603)
	for _, b := range seed {
		h ^= uint64(b)
		h *= 1099511628211
	}
	return h
}

// makeSelfSignedCert produces a PEM-encoded self-signed RSA leaf cert valid
// for "host" and 127.0.0.1 with 24h lifetime. RSA over ECDSA only because
// some ancient http clients in the dependency tree still struggle with ECDSA
// SANs; the OIDC signer keypair is independent.
func makeSelfSignedCert(host string) ([]byte, []byte, error) {
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, nil, err
	}
	tpl := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: host},
		NotBefore:             time.Now().Add(-1 * time.Minute),
		NotAfter:              time.Now().Add(24 * time.Hour),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
		DNSNames:              []string{host, "localhost"},
		IPAddresses:           []net.IP{net.IPv4(127, 0, 0, 1), net.IPv6loopback},
	}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &priv.PublicKey, priv)
	if err != nil {
		return nil, nil, err
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyDER, err := x509.MarshalPKCS8PrivateKey(priv)
	if err != nil {
		return nil, nil, err
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})
	return certPEM, keyPEM, nil
}

// applyClusterRoleBinding grants verbs on the named jumpstarter.dev
// resources to the user. The role name embeds a hash of the (verbs,
// resources) tuple so multiple specs can grant *different* permissions to
// the same user without colliding on a single ClusterRole.
func applyClusterRoleBinding(ctx context.Context, user string, verbs, resources []string) {
	suffix := ruleSuffix(verbs, resources)
	roleName := "jse2e-cr-" + safeSubjectName(user) + "-" + suffix
	bindingName := roleName + "-bind"

	cr := &rbacv1.ClusterRole{
		ObjectMeta: metav1.ObjectMeta{Name: roleName},
		Rules: []rbacv1.PolicyRule{{
			APIGroups: []string{"jumpstarter.dev"},
			Resources: resources,
			Verbs:     verbs,
		}},
	}
	Expect(k8sClient.Create(ctx, cr)).To(Or(Succeed(), MatchError(ContainSubstring("already exists"))))

	crb := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: bindingName},
		Subjects:   []rbacv1.Subject{{Kind: "User", APIGroup: rbacv1.GroupName, Name: user}},
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: roleName},
	}
	Expect(k8sClient.Create(ctx, crb)).To(Or(Succeed(), MatchError(ContainSubstring("already exists"))))
}

// applyNamespaceRoleBinding grants verbs on resources in a single namespace.
//
// NOTE: production admin AuthZ runs SubjectAccessReview with namespace=""
// (the Authorizer is constructed with no NamespaceFromRequest mapping in
// main.go). That means a namespace-scoped RoleBinding alone is NOT enough
// to authorize an admin RPC — the SAR is evaluated at cluster scope.
// Tests using this helper expect deny outcomes and document the gap.
func applyNamespaceRoleBinding(ctx context.Context, ns, user string, verbs, resources []string) {
	suffix := ruleSuffix(verbs, resources)
	roleName := "jse2e-r-" + safeSubjectName(user) + "-" + suffix
	bindingName := roleName + "-bind"

	role := &rbacv1.Role{
		ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: roleName},
		Rules: []rbacv1.PolicyRule{{
			APIGroups: []string{"jumpstarter.dev"},
			Resources: resources,
			Verbs:     verbs,
		}},
	}
	Expect(k8sClient.Create(ctx, role)).To(Or(Succeed(), MatchError(ContainSubstring("already exists"))))

	rb := &rbacv1.RoleBinding{
		ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: bindingName},
		Subjects:   []rbacv1.Subject{{Kind: "User", APIGroup: rbacv1.GroupName, Name: user}},
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "Role", Name: roleName},
	}
	Expect(k8sClient.Create(ctx, rb)).To(Or(Succeed(), MatchError(ContainSubstring("already exists"))))
}

// ruleSuffix produces a short hex digest of the verbs+resources tuple.
// Unique per logical permission set; stable across tests so a re-run picks
// up the existing ClusterRole rather than re-creating it.
func ruleSuffix(verbs, resources []string) string {
	h := newHash([]byte(strings.Join(verbs, ",") + "|" + strings.Join(resources, ",")))
	return fmt.Sprintf("%x", h)[:10]
}

// safeSubjectName turns a username (which can contain ":", "@") into a
// kube-name-friendly slug.
func safeSubjectName(s string) string {
	r := strings.NewReplacer(":", "-", "@", "-at-", ".", "-")
	out := strings.ToLower(r.Replace(s))
	if len(out) > 50 {
		out = out[:50]
	}
	return out
}

// dialAdmin returns a gRPC ClientConn backed by the bufconn listener.
// `token` is sent in every RPC's "authorization: bearer" metadata.
func dialAdmin(token string) *grpc.ClientConn {
	creds := bearerCreds{token: token}
	conn, err := grpc.NewClient("passthrough://bufconn",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return grpcLis.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithPerRPCCredentials(creds),
	)
	Expect(err).NotTo(HaveOccurred())
	return conn
}

// dialAdminNoToken returns a gRPC ClientConn that sends NO authorization
// metadata, used to assert Unauthenticated rejection paths.
func dialAdminNoToken() *grpc.ClientConn {
	conn, err := grpc.NewClient("passthrough://bufconn",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) {
			return grpcLis.DialContext(ctx)
		}),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	Expect(err).NotTo(HaveOccurred())
	return conn
}

// restURL formats a full URL against the in-test REST server. Splits any
// "?" query string out of path so url.URL.String() does not percent-encode
// it back into the path component.
func restURL(path string) string {
	u, _ := url.Parse(restServer.URL)
	if i := strings.Index(path, "?"); i >= 0 {
		u.Path = path[:i]
		u.RawQuery = path[i+1:]
	} else {
		u.Path = path
	}
	return u.String()
}
