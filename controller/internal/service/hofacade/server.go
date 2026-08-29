// Package hofacade implements JEP-0016's pool-as-Host-Orchestrator facade:
// an HTTP service speaking the upstream Android Cuttlefish Host Orchestrator
// wire API, backed entirely by Jumpstarter Lease CRs. POST /cvds creates an
// ordinary Lease for the authenticated caller, the long-running operation is
// lease acquisition (+ prewarm boot), GET /cvds is strictly caller-scoped,
// and DELETE releases the lease. See mapping.go for the stateless
// Lease -> wire view.
package hofacade

import (
	"context"
	"net"
	"net/http"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	ctrl "sigs.k8s.io/controller-runtime"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/manager"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/controller"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/hofacade/howire"
)

// msgCVDNotFound is the 404 body for /cvds/{group} routes. Facade-local
// wording: the upstream unknown-group behavior is unpinned by the wire map
// (empty list vs 404); the facade answers 404 because it makes wrong-owner
// and nonexistent byte-identical (tenancy outranks unpinned fidelity).
// TODO(JEP-0016): verify against upstream listCVDsHandler before the CO
// instances.Manager backend ships.
const msgCVDNotFound = "CVD not found"

// Server is the HO facade. It is a pure view over Lease CRs: the only writes
// it ever performs are Lease Create (POST /cvds) and spec.release MergeFrom
// patches (DELETE, /reset). It never deletes a Lease, never writes status,
// and keeps no state of its own, so every replica can serve (no leader
// election) and restarts need no recovery.
type Server struct {
	Client   kclient.Client
	Resolver ClientResolver
	// APIReader, when set, is an UNCACHED reader (mgr.GetAPIReader()) used as
	// a read-your-own-writes fallback: POST /cvds writes the Lease through to
	// the API server, but Client reads go through the informer cache, which
	// lags the write by the watch-event round trip. A driver's :wait or GET
	// issued immediately after create would otherwise transiently 404 on a
	// name the facade itself just returned — a terminal error for the Python
	// driver, which only retries 503/504. Nil (e.g. in tests over a fake
	// client, where reads are always consistent) disables the fallback.
	APIReader kclient.Reader
	// Namespace scopes every read and write; it must match the caller's
	// Client CR namespace (enforced by the resolver).
	Namespace string
	// PoolSelector is the fronted ExporterSet's spec.selector, stamped on
	// every created Lease. Resolved once at startup by cmd/ho-facade.
	PoolSelector *metav1.LabelSelector
	// LeaseDuration is stamped on created leases (--lease-duration; a v0
	// stand-in for ExporterAccessPolicy maximumDuration evaluation).
	LeaseDuration time.Duration
	// WaitDuration bounds POST /operations/{name}/:wait long-polls (upstream
	// WaitOperationDuration, main.go: 2 minutes).
	WaitDuration time.Duration
	// PollInterval is the :wait re-check cadence.
	PollInterval time.Duration
}

// NewServer applies upstream-parity defaults to unset fields.
func NewServer(s Server) *Server {
	if s.WaitDuration == 0 {
		s.WaitDuration = 2 * time.Minute
	}
	if s.PollInterval == 0 {
		s.PollInterval = 500 * time.Millisecond
	}
	return &s
}

// getLease reads a Lease by name through the (cached) client, falling back
// to one uncached APIReader read on NotFound (see the APIReader field doc).
// Tenancy is unaffected: every caller still passes the clientRef check.
func (s *Server) getLease(ctx context.Context, name string, lease *jumpstarterdevv1alpha1.Lease) error {
	key := kclient.ObjectKey{Namespace: s.Namespace, Name: name}
	err := s.Client.Get(ctx, key, lease)
	if err != nil && kclient.IgnoreNotFound(err) == nil && s.APIReader != nil {
		return s.APIReader.Get(ctx, key, lease)
	}
	return err
}

// ResolveCallerLease is THE tenancy chokepoint: every group/operation route
// resolves lease names through it. A lease that does not exist and a lease
// owned by another client produce the identical 404 — wrong-owner is
// indistinguishable from nonexistent (F10).
func (s *Server) ResolveCallerLease(ctx context.Context, caller *jumpstarterdevv1alpha1.Client, name, notFoundMsg string) (*jumpstarterdevv1alpha1.Lease, *AppError) {
	var lease jumpstarterdevv1alpha1.Lease
	if err := s.getLease(ctx, name, &lease); err != nil {
		if kclient.IgnoreNotFound(err) == nil {
			return nil, NewNotFoundError(notFoundMsg, nil)
		}
		return nil, NewInternalError(howire.MsgInternal, err)
	}
	if lease.Spec.ClientRef.Name != caller.Name {
		return nil, NewNotFoundError(notFoundMsg, nil)
	}
	return &lease, nil
}

// CallerActiveLeases lists the caller's active (not-ended) leases: List with
// the active-leases label selector then an in-memory clientRef filter — the
// controller_service.go:1186 idiom; no field index on spec.clientRef exists
// (KEP-4358).
func (s *Server) CallerActiveLeases(ctx context.Context, caller *jumpstarterdevv1alpha1.Client) ([]jumpstarterdevv1alpha1.Lease, error) {
	return s.callerLeases(ctx, caller, true)
}

func (s *Server) callerLeases(ctx context.Context, caller *jumpstarterdevv1alpha1.Client, activeOnly bool) ([]jumpstarterdevv1alpha1.Lease, error) {
	opts := []kclient.ListOption{kclient.InNamespace(s.Namespace)}
	if activeOnly {
		opts = append(opts, controller.MatchingActiveLeases())
	}
	var list jumpstarterdevv1alpha1.LeaseList
	if err := s.Client.List(ctx, &list, opts...); err != nil {
		return nil, err
	}
	leases := make([]jumpstarterdevv1alpha1.Lease, 0, len(list.Items))
	for _, lease := range list.Items {
		if lease.Spec.ClientRef.Name == caller.Name {
			leases = append(leases, lease)
		}
	}
	return leases, nil
}

// Routes returns the facade's ServeMux. Route inventory mirrors upstream
// AddRoutes (android-cuttlefish orchestrator/controller.go:64-139); the
// ':verb' segments are literal path components. Returned as the raw mux so a
// future caller-scoped UI tier can wrap it: /devices, /devices/{id}/connect,
// /polled_connections, /infra_config and the webui asset paths are
// deliberately UNREGISTERED (JSON-404 fallback) and reserved for that tier —
// they are operator-webui paths, not HO endpoints, so the 501 contract does
// not apply to them.
func (s *Server) Routes() *http.ServeMux {
	mux := http.NewServeMux()

	// Lifecycle surface (all authenticated, all caller-scoped).
	mux.Handle("POST /cvds", s.authed(s.createCVD))
	mux.Handle("GET /cvds", s.authed(s.listCVDs))
	mux.Handle("GET /cvds/{group}", s.authed(s.getCVDGroup))
	mux.Handle("GET /cvds/{group}/{name}", s.authed(s.getCVD))
	mux.Handle("DELETE /cvds/{group}", s.authed(s.deleteCVDGroup))
	mux.Handle("DELETE /cvds/{group}/{name}", s.authed(s.deleteCVD))
	mux.Handle("GET /operations", s.authed(s.listOperations))
	mux.Handle("GET /operations/{name}", s.authed(s.getOperation))
	mux.Handle("GET /operations/{name}/result", s.authed(s.getOperationResult))
	mux.Handle("POST /operations/{name}/:wait", s.authed(s.waitOperation))
	mux.Handle("POST /reset", s.authed(s.reset))

	// Driver health probe: bare unauthenticated 200 (upstream okHandler,
	// controller.go:138).
	mux.Handle("GET /_debug/statusz", unauthenticated(func(r *http.Request) (any, error) {
		return nil, nil
	}))

	// Out-of-scope HO surface: 501 + ErrorMsg so real HO clients fail
	// cleanly. Unauthenticated — no data is served.
	for _, pattern := range []string{
		"POST /cvds/{group}/:start",
		"POST /cvds/{group}/:stop",
		"POST /cvds/{group}/:bugreport",
		"POST /cvds/{group}/{name}/:start",
		"POST /cvds/{group}/{name}/:stop",
		"POST /cvds/{group}/{name}/:powerwash",
		"POST /cvds/{group}/{name}/:powerbtn",
		"POST /cvds/{group}/{name}/:restart",
		"POST /cvds/{group}/{name}/:start_screen_recording",
		"POST /cvds/{group}/{name}/:stop_screen_recording",
		"POST /cvds/{group}/{name}/displays",
		"GET /cvds/{group}/{name}/displays",
		"DELETE /cvds/{group}/{name}/displays/{displayNumber}",
		"GET /cvds/{group}/{name}/displays/{displayNumber}/:screenshot",
		"GET /cvds/{group}/{name}/screen_recordings",
		"GET /cvds/{group}/{name}/screen_recordings/{recording_name}",
		"POST /cvds/{group}/{name}/snapshots",
		"GET /cvds/{group}/{name}/logs/",
		"GET /cvdbugreports/{uuid}",
		"DELETE /cvdbugreports/{uuid}",
		"DELETE /snapshots/{id}",
		"PUT /v1/userartifacts/{checksum}",
		"GET /v1/userartifacts/{checksum}",
		"POST /v1/userartifacts/{checksum}/:extract",
		"POST /cvd_imgs_dirs",
		"GET /cvd_imgs_dirs",
		"PUT /cvd_imgs_dirs/{id}",
		"DELETE /cvd_imgs_dirs/{id}",
		"GET /_debug/varz",
	} {
		mux.Handle(pattern, notImplemented())
	}

	// Fallback: JSON 404 for unknown paths (including the reserved UI-tier
	// paths above). A method-only mismatch on a known path still answers 405
	// like upstream's router: probe the mux with the other methods to tell
	// the two apart, since registering "/" suppresses ServeMux's built-in
	// 405 handling.
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		for _, m := range []string{
			http.MethodGet, http.MethodPost, http.MethodPut,
			http.MethodDelete, http.MethodPatch,
		} {
			if m == r.Method {
				continue
			}
			probe := r.Clone(r.Context())
			probe.Method = m
			if _, pattern := mux.Handler(probe); pattern != "" && pattern != "/" {
				w.WriteHeader(http.StatusMethodNotAllowed)
				return
			}
		}
		writeJSON(w, howire.ErrorMsg{Error: "Not found"}, http.StatusNotFound)
	})

	return mux
}

// Start serves Routes() on addr and returns the bound listen address plus a
// graceful-shutdown func (the cmd/router metrics.go idiom). addr ending in
// ":0" binds an ephemeral port.
func (s *Server) Start(addr string) (string, func(context.Context) error, error) {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return "", nil, err
	}
	srv := &http.Server{
		Handler:           s.Routes(),
		ReadHeaderTimeout: 10 * time.Second,
		// No WriteTimeout: :wait long-polls legitimately hold responses
		// open for up to WaitDuration.
	}
	go func() {
		if err := srv.Serve(ln); err != nil && err != http.ErrServerClosed {
			ctrl.Log.WithName("hofacade").Error(err, "ho-facade server stopped unexpectedly")
		}
	}()
	return ln.Addr().String(), srv.Shutdown, nil
}

// Runnable wraps the server for manager registration. NeedLeaderElection is
// false: the facade is a stateless view, every replica serves.
func (s *Server) Runnable(addr string) manager.Runnable {
	return &serverRunnable{server: s, addr: addr}
}

type serverRunnable struct {
	server *Server
	addr   string
}

func (r *serverRunnable) Start(ctx context.Context) error {
	listenAddr, shutdown, err := r.server.Start(r.addr)
	if err != nil {
		return err
	}
	ctrl.Log.WithName("hofacade").Info("ho-facade listening", "addr", listenAddr)
	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return shutdown(shutdownCtx)
}

func (r *serverRunnable) NeedLeaderElection() bool {
	return false
}
