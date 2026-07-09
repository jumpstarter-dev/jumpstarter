// Command conformance is a thin gRPC server harness that stands up the REAL Go
// ControllerService + ClientService against an already-running Kubernetes API
// server (envtest), so the Rust jumpstarter-conformance crate can run its
// black-box case suite against BOTH implementations and machine-check that the
// (code, message) wire behavior is identical.
//
// It deliberately mirrors the production wiring in controller/cmd/main.go:
//
//   - the internal ES256 signer is derived from CONTROLLER_KEY exactly as
//     production does (oidc.NewSignerFromSeed(seed, "https://localhost:8085",
//     "jumpstarter")), so a token minted once by either implementation
//     authenticates against both — this doubles as the cross-impl token-compat
//     proof;
//   - the OIDC discovery + JWKS endpoint is served on 127.0.0.1:8085 over TLS
//     with a self-signed "localhost" cert, and that cert's PEM is handed to the
//     k8s structured-authn OIDC authenticator as its CA, so the authenticator
//     validates internal tokens through the same path as production;
//   - ControllerService/ClientService are constructed with the same
//     Authn/Authz/Attr/Router/Signer as cmd/main.go and served (plaintext, for
//     a simple shared client) with the recovery interceptor.
//
// Unlike cmd/main.go it does NOT run the controller-runtime manager, reconcilers,
// leader election, metrics, dashboard or login services — the conformance suite
// arranges CR state directly via the API server and only exercises the gRPC
// handlers.
//
// Usage (spawned by rust/jumpstarter-conformance's differential test):
//
//	CONTROLLER_KEY=conformance-fixed-signer-key \
//	ROUTER_KEY=conformance-fixed-router-key \
//	KUBECONFIG=/path/to/envtest.kubeconfig \
//	conformance -grpc-addr 127.0.0.1:12345 -router-endpoint grpc://router-0.jumpstarter.example:443
//
// It prints "CONFORMANCE-SERVER-READY <addr>" to stdout once the gRPC server is
// accepting and the OIDC authenticator has loaded its JWKS (so tokens validate),
// then serves until SIGINT/SIGTERM or ctx cancellation.
package main

import (
	"context"
	"crypto/tls"
	"encoding/pem"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/grpc-ecosystem/go-grpc-middleware/v2/interceptors/recovery"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/reflection"

	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	apiserverinstall "k8s.io/apiserver/pkg/apis/apiserver/install"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/clientcmd"
	"sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authorization"
	jconfig "github.com/jumpstarter-dev/jumpstarter-controller/internal/config"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	cpb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service/auth"
	clientsvcv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/service/client/v1"
)

// Matches cmd/main.go:214-215 and rust jumpstarter-controller-auth signer
// INTERNAL_ISSUER / INTERNAL_AUDIENCE.
const (
	internalIssuer   = "https://localhost:8085"
	internalAudience = "jumpstarter"
)

func buildScheme() *runtime.Scheme {
	scheme := runtime.NewScheme()
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(jumpstarterdevv1alpha1.AddToScheme(scheme))
	// Required by internal/config/oidc.go newJWTAuthenticator's
	// scheme.Convert(v1beta1.JWTAuthenticator -> apiserver.JWTAuthenticator).
	apiserverinstall.Install(scheme)
	return scheme
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "conformance-server: "+format+"\n", args...)
	os.Exit(1)
}

func main() {
	grpcAddr := flag.String("grpc-addr", "127.0.0.1:0", "address to serve gRPC on")
	// NB: do NOT define a -kubeconfig flag — controller-runtime's client/config
	// package registers a global one in init() and flag.String would panic with
	// "flag redefined: kubeconfig". Read the KUBECONFIG env var directly.
	kubeconfig := os.Getenv("KUBECONFIG")
	controllerKey := flag.String("controller-key", os.Getenv("CONTROLLER_KEY"), "internal signer seed (must match the Rust harness)")
	routerKey := flag.String("router-key", os.Getenv("ROUTER_KEY"), "HS256 router token key (must match the Rust harness)")
	routerEndpoint := flag.String("router-endpoint", "grpc://router-0.jumpstarter.example:443", "single-router endpoint (must match the Rust harness ROUTER_ENDPOINT)")
	readyTimeout := flag.Duration("ready-timeout", 45*time.Second, "bound on OIDC authenticator readiness")
	flag.Parse()

	gin.SetMode(gin.ReleaseMode)

	// The Go ControllerService signs router HS256 tokens with os.Getenv("ROUTER_KEY")
	// directly (controller_service.go:874). Honor the flag by exporting it.
	if *routerKey != "" {
		_ = os.Setenv("ROUTER_KEY", *routerKey)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	scheme := buildScheme()

	if kubeconfig == "" {
		fatal("no kubeconfig provided (set KUBECONFIG)")
	}
	restCfg, err := clientcmd.BuildConfigFromFlags("", kubeconfig)
	if err != nil {
		fatal("building rest config from %q: %v", kubeconfig, err)
	}
	k8sClient, err := client.NewWithWatch(restCfg, client.Options{Scheme: scheme})
	if err != nil {
		fatal("building kube client: %v", err)
	}

	// --- internal signer, exactly as cmd/main.go:212-216 --------------------
	signer, err := oidc.NewSignerFromSeed([]byte(*controllerKey), internalIssuer, internalAudience)
	if err != nil {
		fatal("creating internal oidc signer: %v", err)
	}

	// --- OIDC discovery/JWKS on 127.0.0.1:8085 (cmd/main.go:206 + OIDCService)
	oidcCert, err := service.NewSelfSignedCertificate("jumpstarter oidc", []string{"localhost"}, nil)
	if err != nil {
		fatal("generating oidc certificate: %v", err)
	}
	certPEM := string(pem.EncodeToMemory(&pem.Block{
		Type:  "CERTIFICATE",
		Bytes: oidcCert.Certificate[0],
	}))
	// Serve the discovery/JWKS endpoint on BOTH loopback stacks. The k8s OIDC
	// authenticator dials the issuer host "localhost", which on many systems
	// resolves to IPv6 [::1] first; a listener bound to 127.0.0.1 only would
	// leave those connections resetting. Bind 127.0.0.1 AND [::1] (tolerating a
	// missing IPv6 stack) and serve the same gin handler on each.
	oidcEngine := gin.New()
	signer.Register(oidcEngine)
	tlsConf := &tls.Config{Certificates: []tls.Certificate{*oidcCert}}
	bound := 0
	for _, host := range []string{"127.0.0.1:8085", "[::1]:8085"} {
		lis, lerr := net.Listen("tcp", host)
		if lerr != nil {
			fmt.Fprintf(os.Stderr, "conformance-server: oidc listen %s failed (continuing): %v\n", host, lerr)
			continue
		}
		bound++
		tlsLis := tls.NewListener(lis, tlsConf)
		srv := &http.Server{Handler: oidcEngine}
		go func(host string) {
			if serr := srv.Serve(tlsLis); serr != nil && ctx.Err() == nil {
				fmt.Fprintf(os.Stderr, "conformance-server: oidc serve %s stopped: %v\n", host, serr)
			}
		}(host)
	}
	if bound == 0 {
		fatal("could not bind any oidc discovery listener on :8085 (port in use?)")
	}

	// --- authenticator via the production loader (internal/config/oidc.go) ---
	authenticator, prefix, err := jconfig.LoadAuthenticationConfiguration(
		ctx,
		scheme,
		jconfig.Authentication{}, // Internal.Prefix defaults to "internal:"
		signer,
		certPEM,
	)
	if err != nil {
		fatal("loading authentication configuration: %v", err)
	}

	// Wait for the OIDC authenticator to load JWKS from the discovery endpoint;
	// until then it returns "authenticator not initialized". Probe with a real
	// signer-minted token so we detect readiness by a successful authentication.
	if err := waitAuthReady(ctx, signer, authenticator, *readyTimeout); err != nil {
		fatal("oidc authenticator did not become ready: %v", err)
	}
	fmt.Fprintln(os.Stderr, "conformance-server: oidc authenticator ready")

	// --- controller + client service, mirroring cmd/main.go:274-296 ---------
	router := jconfig.Router{
		"router-0": jconfig.RouterEntry{Endpoint: *routerEndpoint},
	}
	attr := authorization.NewMetadataAttributesGetter(authorization.MetadataAttributesGetterConfig{
		NamespaceKey: "jumpstarter-namespace",
		ResourceKey:  "jumpstarter-kind",
		NameKey:      "jumpstarter-name",
	})
	authz := authorization.NewBasicAuthorizer(k8sClient, prefix, false)
	authn := authentication.NewBearerTokenAuthenticator(authenticator)

	controllerSvc := &service.ControllerService{
		Client: k8sClient,
		Scheme: scheme,
		Authn:  authn,
		Authz:  authz,
		Attr:   attr,
		Router: router,
		Signer: signer,
	}
	clientSvc := clientsvcv1.NewClientService(
		k8sClient,
		*auth.NewAuth(k8sClient, authn, authz, attr),
		64, // defaultMaxTags in the Go service; conformance never hits the cap
		signer,
	)

	grpcServer := grpc.NewServer(
		grpc.ChainUnaryInterceptor(recovery.UnaryServerInterceptor()),
		grpc.ChainStreamInterceptor(recovery.StreamServerInterceptor()),
	)
	pb.RegisterControllerServiceServer(grpcServer, controllerSvc)
	cpb.RegisterClientServiceServer(grpcServer, clientSvc)
	hs := health.NewServer()
	healthpb.RegisterHealthServer(grpcServer, hs)
	hs.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)
	reflection.Register(grpcServer)

	lis, err := net.Listen("tcp", *grpcAddr)
	if err != nil {
		fatal("listening on %s: %v", *grpcAddr, err)
	}

	go func() {
		<-ctx.Done()
		grpcServer.GracefulStop()
	}()

	// Signal readiness on stdout so the Rust harness can proceed deterministically.
	fmt.Printf("CONFORMANCE-SERVER-READY %s\n", lis.Addr().String())
	os.Stdout.Sync()

	if err := grpcServer.Serve(lis); err != nil {
		fatal("serving gRPC: %v", err)
	}
}

// waitAuthReady blocks until the k8s OIDC authenticator has fetched the JWKS
// from the discovery endpoint (it starts async and reports "authenticator not
// initialized" until then). A freshly signed token must authenticate ok=true.
func waitAuthReady(ctx context.Context, signer *oidc.Signer, authn authenticator.Token, timeout time.Duration) error {
	tok, err := signer.Token("conformance-readiness-probe")
	if err != nil {
		return fmt.Errorf("minting probe token: %w", err)
	}
	deadline := time.Now().Add(timeout)
	var lastErr error
	for time.Now().Before(deadline) {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		resp, ok, aerr := authn.AuthenticateToken(ctx, tok)
		if ok && resp != nil {
			return nil
		}
		lastErr = aerr
		time.Sleep(250 * time.Millisecond)
	}
	return fmt.Errorf("timed out after %s (last authenticate error: %v)", timeout, lastErr)
}
