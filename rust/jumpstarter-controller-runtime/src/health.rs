//! Health-probe HTTP server, mirroring controller-runtime's manager probe
//! endpoint (`HealthProbeBindAddress`, default `:8081` in
//! `controller/cmd/main.go`).
//!
//! The Go manager registers `healthz.Ping` for both the healthz and readyz
//! checks (`mgr.AddHealthzCheck("healthz", healthz.Ping)` /
//! `mgr.AddReadyzCheck("readyz", healthz.Ping)` in `controller/cmd/main.go`),
//! i.e. both probes are unconditionally OK once the HTTP listener is
//! serving. This port reproduces exactly that: `GET /healthz` and
//! `GET /readyz` return `200 "ok"` with the same headers controller-runtime
//! writes (`Content-Type: text/plain; charset=utf-8`,
//! `X-Content-Type-Options: nosniff`); no readiness semantics are invented.
//!
//! Address semantics ported from controller-runtime's
//! `defaultHealthProbeListener` (`pkg/manager/manager.go`): `""` and `"0"`
//! both disable the probe server entirely (`return nil, nil` — the manager
//! keeps running without it). Unlike the metrics server, an empty address
//! does *not* fall back to a default port; the `:8081` default comes from
//! the flag declaration in `controller/cmd/main.go`.

use std::future::Future;
use std::net::SocketAddr;

use axum::http::header;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::Router;
use thiserror::Error;
use tokio::net::TcpListener;

/// Errors from binding or running one of the auxiliary HTTP servers
/// (health probes, metrics).
#[derive(Debug, Error)]
pub enum HttpServeError {
    /// The bind address could not be parsed or bound.
    #[error("unable to listen on {addr:?}: {source}")]
    Bind {
        addr: String,
        #[source]
        source: std::io::Error,
    },
    /// The server failed while serving.
    #[error("http server error: {0}")]
    Serve(#[from] std::io::Error),
    /// The server task panicked or was aborted.
    #[error("http server task failed: {0}")]
    Join(#[source] tokio::task::JoinError),
}

/// Handle to a spawned auxiliary HTTP server.
#[derive(Debug)]
pub struct HttpServerHandle {
    local_addr: SocketAddr,
    task: tokio::task::JoinHandle<Result<(), std::io::Error>>,
}

impl HttpServerHandle {
    /// The actual bound address (useful when binding port 0 in tests).
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }

    /// Wait for the server to exit (it exits after the shutdown future
    /// passed to `serve` resolves, once in-flight requests complete).
    pub async fn join(self) -> Result<(), HttpServeError> {
        self.task.await.map_err(HttpServeError::Join)??;
        Ok(())
    }

    /// Abort the server task without graceful shutdown.
    pub fn abort(&self) {
        self.task.abort();
    }
}

/// A running (or deliberately disabled) health-probe server.
#[derive(Debug)]
pub enum HealthServer {
    /// The bind address was `""` or `"0"`: the probe server is disabled
    /// (controller-runtime `defaultHealthProbeListener` returns a nil
    /// listener in these cases and the manager runs without one).
    Disabled,
    /// The server is bound and serving `GET /healthz` and `GET /readyz`.
    Bound(HttpServerHandle),
}

impl HealthServer {
    /// The bound address, or `None` when disabled.
    pub fn local_addr(&self) -> Option<SocketAddr> {
        match self {
            Self::Disabled => None,
            Self::Bound(handle) => Some(handle.local_addr()),
        }
    }

    /// Wait for the server to exit; a no-op for a disabled server.
    pub async fn join(self) -> Result<(), HttpServeError> {
        match self {
            Self::Disabled => Ok(()),
            Self::Bound(handle) => handle.join().await,
        }
    }

    /// Abort without graceful shutdown; a no-op for a disabled server.
    pub fn abort(&self) {
        if let Self::Bound(handle) = self {
            handle.abort();
        }
    }
}

/// Bind a TCP listener for a Go-style listen address.
///
/// Go's `net.Listen("tcp", addr)` accepts a host-less `":8081"` form
/// meaning "all interfaces" (dual-stack). Rust's `SocketAddr` does not, so
/// the empty-host form is mapped to the IPv6 wildcard (dual-stack on
/// default Linux/macOS configs) with an IPv4-wildcard fallback. Everything
/// else (including `host:port` names) goes through standard resolution.
pub(crate) async fn bind_go_addr(addr: &str) -> Result<TcpListener, HttpServeError> {
    let bind = |target: String| async move { TcpListener::bind(target).await };
    let result = if let Some(port) = addr.strip_prefix(':') {
        match bind(format!("[::]:{port}")).await {
            Ok(listener) => Ok(listener),
            Err(_) => bind(format!("0.0.0.0:{port}")).await,
        }
    } else {
        bind(addr.to_string()).await
    };
    result.map_err(|source| HttpServeError::Bind {
        addr: addr.to_string(),
        source,
    })
}

/// Spawn `router` on `addr` with graceful shutdown driven by `shutdown`.
pub(crate) async fn spawn_http_server<F>(
    addr: &str,
    router: Router,
    shutdown: F,
) -> Result<HttpServerHandle, HttpServeError>
where
    F: Future<Output = ()> + Send + 'static,
{
    let listener = bind_go_addr(addr).await?;
    let local_addr = listener
        .local_addr()
        .map_err(|source| HttpServeError::Bind {
            addr: addr.to_string(),
            source,
        })?;
    let task = tokio::spawn(async move {
        axum::serve(listener, router)
            .with_graceful_shutdown(shutdown)
            .await
    });
    Ok(HttpServerHandle { local_addr, task })
}

/// The always-OK probe response, byte-identical to controller-runtime's
/// `healthz.CheckHandler` success path (status 200, body `ok`).
async fn ping() -> impl IntoResponse {
    (
        [
            (header::CONTENT_TYPE, "text/plain; charset=utf-8"),
            (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
        ],
        "ok",
    )
}

/// Router serving `/healthz` and `/readyz` (controller-runtime's
/// `defaultLivenessEndpoint` / `defaultReadinessEndpoint`).
fn health_router() -> Router {
    Router::new()
        .route("/healthz", get(ping))
        .route("/readyz", get(ping))
}

/// Serve the health probes on `addr` (the `health-probe-bind-address` flag
/// value, default `:8081`). Both endpoints return `200 "ok"` for as long
/// as the server is running.
///
/// `""` and `"0"` return [`HealthServer::Disabled`] without binding
/// anything (controller-runtime `defaultHealthProbeListener` semantics:
/// the manager runs without a probe server).
///
/// `shutdown` triggers graceful shutdown when it resolves (pass e.g.
/// `token.cancelled_owned()` from a `CancellationToken`, or a future
/// awaiting a `tokio::sync::watch` channel).
pub async fn serve<F>(addr: &str, shutdown: F) -> Result<HealthServer, HttpServeError>
where
    F: Future<Output = ()> + Send + 'static,
{
    if addr.is_empty() || addr == "0" {
        tracing::info!(
            addr,
            "health probe server is disabled (bind address empty or \"0\")"
        );
        return Ok(HealthServer::Disabled);
    }
    let handle = spawn_http_server(addr, health_router(), shutdown).await?;
    tracing::info!(addr = %handle.local_addr(), "health probe server listening");
    Ok(HealthServer::Bound(handle))
}

/// Test-only helper shared with the metrics module's tests.
#[cfg(test)]
pub(crate) mod test_util {
    use std::net::SocketAddr;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// Minimal HTTP/1.1 GET returning the raw response text.
    pub(crate) async fn http_get(addr: SocketAddr, path: &str) -> String {
        let mut stream = tokio::net::TcpStream::connect(addr).await.expect("connect");
        stream
            .write_all(
                format!("GET {path} HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\r\n")
                    .as_bytes(),
            )
            .await
            .expect("send request");
        let mut response = Vec::new();
        stream
            .read_to_end(&mut response)
            .await
            .expect("read response");
        String::from_utf8_lossy(&response).into_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::test_util::http_get;
    use super::*;

    #[tokio::test]
    async fn healthz_and_readyz_return_ok() {
        let (tx, rx) = tokio::sync::oneshot::channel::<()>();
        let server = serve("127.0.0.1:0", async move {
            rx.await.ok();
        })
        .await
        .expect("bind health server on port 0");
        let addr = server.local_addr().expect("bound server has an address");

        for path in ["/healthz", "/readyz"] {
            let response = http_get(addr, path).await;
            assert!(
                response.starts_with("HTTP/1.1 200 OK\r\n"),
                "{path}: {response}"
            );
            assert!(
                response.contains("content-type: text/plain; charset=utf-8"),
                "{path}: {response}"
            );
            assert!(
                response.contains("x-content-type-options: nosniff"),
                "{path}: {response}"
            );
            assert!(response.ends_with("\r\n\r\nok"), "{path}: {response}");
        }

        // Unknown paths are not health endpoints.
        let response = http_get(addr, "/other").await;
        assert!(response.starts_with("HTTP/1.1 404"), "{response}");

        // Graceful shutdown: the task must complete after the signal.
        tx.send(()).ok();
        server.join().await.expect("clean shutdown");
    }

    #[tokio::test]
    async fn go_style_hostless_address_binds() {
        // The operator/default form ":0" (host omitted, Go idiom).
        let (_tx, rx) = tokio::sync::oneshot::channel::<()>();
        let server = serve(":0", async move {
            rx.await.ok();
        })
        .await
        .expect("bind host-less address");
        let addr = server.local_addr().expect("bound server has an address");
        assert_ne!(addr.port(), 0);
        server.abort();
    }

    #[tokio::test]
    async fn empty_and_zero_addresses_disable_probes() {
        // controller-runtime's defaultHealthProbeListener returns a nil
        // listener (no error) for "" and "0": the manager runs probe-less.
        for addr in ["", "0"] {
            let server = serve(addr, std::future::pending())
                .await
                .expect("disabled server is not an error");
            assert!(matches!(server, HealthServer::Disabled), "addr {addr:?}");
            assert_eq!(server.local_addr(), None, "addr {addr:?}");
            server
                .join()
                .await
                .expect("joining a disabled server is a no-op");
        }
    }

    #[tokio::test]
    async fn bind_failure_is_reported() {
        let first = serve("127.0.0.1:0", std::future::pending())
            .await
            .expect("bind first server");
        let occupied = first.local_addr().expect("bound server has an address");
        let err = serve(&occupied.to_string(), std::future::pending())
            .await
            .expect_err("second bind on the same port must fail");
        assert!(matches!(err, HttpServeError::Bind { .. }), "{err}");
        first.abort();
    }
}
