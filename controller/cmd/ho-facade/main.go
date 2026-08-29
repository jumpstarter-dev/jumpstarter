/*
Copyright 2026 The Jumpstarter Authors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

// ho-facade serves the upstream Android Cuttlefish Host Orchestrator HTTP
// wire API as a stateless view over Jumpstarter Lease CRs (JEP-0016
// prototype). POST /cvds creates an ordinary Lease for the authenticated
// caller against the fronted ExporterSet's selector; operations, listings and
// deletes are derived from Lease state per request.
//
// v0 runs via `go run ./cmd/ho-facade` or bin/ho-facade with a kubeconfig —
// operator Deployment/Role constructors, a Containerfile and docker-build-ci
// wiring are deliberate v0 omissions. An in-cluster deployment needs a
// namespaced Role with at least:
//
//	jumpstarter.dev            leases        get;list;watch;create;patch
//	jumpstarter.dev            clients       get;list;watch (+create when JIT provisioning is enabled)
//	virtualtarget.jumpstarter.dev exportersets get
//	""                         configmaps    get (the jumpstarter-controller ConfigMap)
//
// plus the CONTROLLER_KEY and NAMESPACE environment variables (see below).
//
// Manual acid test (test 34, not CI): against a kind cluster running the
// controller, start
//
//	NAMESPACE=jumpstarter go run ./cmd/ho-facade \
//	    --exporter-set <pool> --bind :2080 --insecure-dev-anonymous-client <client>
//
// then point jumpstarter_driver_cuttlefish.driver.Cuttlefish's base URL at
// it and verify status() -> create_cvd -> _wait_for_operation (including a
// 503 retry cycle) -> get_cvd_info -> delete_cvd -> reset_host with the
// UNCHANGED Python driver. The unchanged driver sends no Authorization
// header on any route, so the recipe maps anonymous requests to a dev
// Client CR; --insecure-dev-token remains for clients that do send bearer
// tokens (e.g. curl).
package main

import (
	"context"
	"encoding/pem"
	"flag"
	"fmt"
	"net"
	"os"
	"strings"
	"time"

	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	_ "k8s.io/client-go/plugin/pkg/client/auth"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authorization"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/auth"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/hofacade"
)

var (
	scheme   = runtime.NewScheme()
	setupLog = ctrl.Log.WithName("setup")

	// Version information - set via ldflags at build time
	version   = "dev"
	gitCommit = "unknown"
	buildDate = "unknown"
)

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(jumpstarterdevv1alpha1.AddToScheme(scheme))
	utilruntime.Must(virtualtargetv1alpha1.AddToScheme(scheme))
}

// devTokenFlags collects repeatable --insecure-dev-token token=clientName
// mappings.
type devTokenFlags map[string]string

func (f devTokenFlags) String() string {
	return fmt.Sprintf("%d dev token(s)", len(f))
}

func (f devTokenFlags) Set(value string) error {
	token, clientName, ok := strings.Cut(value, "=")
	if !ok || token == "" || clientName == "" {
		return fmt.Errorf("expected token=clientName, got %q", value)
	}
	f[token] = clientName
	return nil
}

func main() {
	var (
		bindAddr     string
		exporterSet  string
		metricsAddr  string
		probeAddr    string
		leaseDur     time.Duration
		waitDur      time.Duration
		pollInterval time.Duration
		devAnonymous string
	)
	devTokens := devTokenFlags{}

	flag.StringVar(&bindAddr, "bind", ":2080",
		"The address the Host Orchestrator facade HTTP endpoint binds to.")
	flag.StringVar(&exporterSet, "exporter-set", "",
		"Name of the ExporterSet whose selector fronts POST /cvds creates. Required.")
	flag.DurationVar(&leaseDur, "lease-duration", 30*time.Minute,
		"Duration stamped on created leases. A v0 stand-in for "+
			"ExporterAccessPolicy maximumDuration evaluation: an over-policy "+
			"duration surfaces asynchronously as an Unsatisfiable lease.")
	flag.DurationVar(&waitDur, "wait-operation-duration", 2*time.Minute,
		"Server-side long-poll bound for POST /operations/{name}/:wait "+
			"(upstream WaitOperationDuration parity).")
	flag.DurationVar(&pollInterval, "poll-interval", 500*time.Millisecond,
		"Lease re-check cadence inside :wait long-polls.")
	flag.StringVar(&metricsAddr, "metrics-bind-address", "0",
		"The address the metrics endpoint binds to. "+
			"Use :8443 for HTTPS or :8080 for HTTP, or 0 to disable.")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081",
		"The address the probe endpoint binds to.")
	flag.Var(devTokens, "insecure-dev-token",
		"INSECURE, repeatable: token=clientName mapping that BYPASSES token "+
			"authentication (local development only). When set, the OIDC/internal "+
			"authenticator is not wired at all.")
	flag.StringVar(&devAnonymous, "insecure-dev-anonymous-client", "",
		"INSECURE: Client CR name that requests WITHOUT an Authorization header "+
			"resolve to (local development only; the unchanged Python driver sends "+
			"no auth header). Like --insecure-dev-token, setting it skips the "+
			"OIDC/internal authenticator entirely.")

	opts := zap.Options{}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()
	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))

	setupLog.Info("Jumpstarter Host Orchestrator facade starting",
		"version", version,
		"gitCommit", gitCommit,
		"buildDate", buildDate,
		"exporterSet", exporterSet,
	)

	if exporterSet == "" {
		setupLog.Error(fmt.Errorf("missing required flag"), "--exporter-set flag is required")
		os.Exit(1)
	}

	// Refuse a non-positive lease duration up front: nothing downstream
	// validates it (the facade creates leases directly, bypassing
	// ReconcileLeaseTimeFields' "duration must be positive" check), and a
	// zero/negative duration yields leases that expire the instant they are
	// acquired — a confusing systemic failure instead of a startup refusal.
	if leaseDur <= 0 {
		setupLog.Error(fmt.Errorf("invalid flag value"),
			"--lease-duration must be positive", "leaseDuration", leaseDur)
		os.Exit(1)
	}

	// Like the exporter-set controller, this binary is only meant to hold a
	// namespaced Role, so the cache MUST be restricted to its own namespace
	// (see cmd/exporter-set-controller/main.go for the full rationale).
	namespace := os.Getenv("NAMESPACE")
	if namespace == "" {
		setupLog.Error(fmt.Errorf("missing required environment variable"), "NAMESPACE environment variable is required")
		os.Exit(1)
	}

	devMode := len(devTokens) > 0 || devAnonymous != ""
	if !devMode && os.Getenv("CONTROLLER_KEY") == "" {
		setupLog.Error(fmt.Errorf("missing required environment variable"),
			"CONTROLLER_KEY environment variable is required unless --insecure-dev-token or --insecure-dev-anonymous-client is used")
		os.Exit(1)
	}

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme: scheme,
		Cache: cache.Options{
			DefaultNamespaces: map[string]cache.Config{
				namespace: {},
			},
		},
		Metrics: metricsserver.Options{
			BindAddress: metricsAddr,
		},
		HealthProbeBindAddress: probeAddr,
	})
	if err != nil {
		setupLog.Error(err, "unable to start manager")
		os.Exit(1)
	}

	// Resolve the fronted pool's selector once at startup and refuse to run
	// with an empty one: creates would only yield Invalid/InvalidSelector
	// leases. GetAPIReader reads directly (no cache), so it works pre-Start.
	var pool virtualtargetv1alpha1.ExporterSet
	if err := mgr.GetAPIReader().Get(context.Background(), client.ObjectKey{
		Namespace: namespace,
		Name:      exporterSet,
	}, &pool); err != nil {
		setupLog.Error(err, "unable to fetch ExporterSet", "exporterSet", exporterSet)
		os.Exit(1)
	}
	if len(pool.Spec.Selector.MatchLabels) == 0 && len(pool.Spec.Selector.MatchExpressions) == 0 {
		setupLog.Error(fmt.Errorf("empty selector"),
			"ExporterSet has an empty spec.selector; refusing to create Invalid leases", "exporterSet", exporterSet)
		os.Exit(1)
	}

	var resolver hofacade.ClientResolver
	if devMode {
		// Dev mode: static tokens and/or an anonymous mapping, no OIDC block
		// at all (also avoids the loopback :8085 collision with a controller
		// on the same host).
		static := hofacade.NewStaticClientResolver(mgr.GetClient(), namespace, devTokens)
		static.AnonymousClient = devAnonymous
		resolver = static
	} else {
		// Mirror cmd/main.go:207-297: the internal OIDC signer is
		// deterministic from CONTROLLER_KEY, so this pod reconstructs it,
		// serves its own loopback JWKS endpoint on 127.0.0.1:8085, and hands
		// its self-signed CA to config.LoadConfiguration — the resulting
		// union authenticator validates internal client tokens AND external
		// OIDC tokens exactly as the controller does.
		oidcCert, err := service.NewSelfSignedCertificate("jumpstarter oidc", []string{"localhost"}, []net.IP{})
		if err != nil {
			setupLog.Error(err, "unable to generate certificate for internal oidc provider")
			os.Exit(1)
		}

		oidcSigner, err := oidc.NewSignerFromSeed(
			[]byte(os.Getenv("CONTROLLER_KEY")),
			"https://localhost:8085",
			"jumpstarter",
		)
		if err != nil {
			setupLog.Error(err, "unable to create internal oidc signer")
			os.Exit(1)
		}

		cfg, err := config.LoadConfiguration(
			context.Background(),
			mgr.GetAPIReader(),
			mgr.GetScheme(),
			client.ObjectKey{
				Namespace: namespace,
				Name:      "jumpstarter-controller",
			},
			oidcSigner,
			string(pem.EncodeToMemory(&pem.Block{
				Type:  "CERTIFICATE",
				Bytes: oidcCert.Certificate[0],
			})),
		)
		if err != nil {
			setupLog.Error(err, "unable to load configuration")
			os.Exit(1)
		}

		if err = (&service.OIDCService{
			Signer: oidcSigner,
			Cert:   oidcCert,
		}).SetupWithManager(mgr); err != nil {
			setupLog.Error(err, "unable to create service", "service", "OIDC")
			os.Exit(1)
		}

		resolver = &hofacade.AuthClientResolver{
			Auth: auth.NewAuth(
				mgr.GetClient(),
				authentication.NewBearerTokenAuthenticator(cfg.Authenticator),
				authorization.NewBasicAuthorizer(mgr.GetClient(), cfg.Prefix, cfg.Provisioning.Enabled),
				authorization.NewMetadataAttributesGetter(authorization.MetadataAttributesGetterConfig{
					NamespaceKey: "jumpstarter-namespace",
					ResourceKey:  "jumpstarter-kind",
					NameKey:      "jumpstarter-name",
				}),
			),
			Namespace: namespace,
		}
	}

	server := hofacade.NewServer(hofacade.Server{
		Client: mgr.GetClient(),
		// Uncached read-your-own-writes fallback: a :wait/GET arriving before
		// the informer cache sees a just-created Lease must not 404.
		APIReader:     mgr.GetAPIReader(),
		Resolver:      resolver,
		Namespace:     namespace,
		PoolSelector:  &pool.Spec.Selector,
		LeaseDuration: leaseDur,
		WaitDuration:  waitDur,
		PollInterval:  pollInterval,
	})
	if err := mgr.Add(server.Runnable(bindAddr)); err != nil {
		setupLog.Error(err, "unable to register ho-facade server")
		os.Exit(1)
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up ready check")
		os.Exit(1)
	}

	setupLog.Info("starting manager", "bind", bindAddr, "exporterSet", exporterSet)
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		setupLog.Error(err, "problem running manager")
		os.Exit(1)
	}
}
