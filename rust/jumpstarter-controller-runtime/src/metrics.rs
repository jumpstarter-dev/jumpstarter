//! Minimal metrics endpoint, mirroring controller-runtime's metrics server
//! surface (`metricsserver.Options.BindAddress` in `controller/cmd/main.go`).
//!
//! Address semantics ported from
//! `sigs.k8s.io/controller-runtime/pkg/metrics/server/server.go`:
//! `"0"` disables the server entirely (`NewServer` returns nil; the Go flag
//! default), and an empty address defaults to `":8080"`
//! (`DefaultBindAddress`). The operator passes `-metrics-bind-address=:8080`.
//!
//! `GET /metrics` (controller-runtime's `defaultMetricsEndpoint`) serves a
//! valid — currently empty — Prometheus text exposition. Divergence from
//! Go (documented, pending maintainer decision — plan risk #6): no
//! `controller_runtime_*` metric families are emitted yet, and
//! `metrics-secure` TLS serving is not implemented.

use std::future::Future;
use std::net::SocketAddr;

use axum::http::header;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::Router;

use crate::health::{spawn_http_server, HttpServeError, HttpServerHandle};

/// The default bind address used when the flag value is empty
/// (controller-runtime `DefaultBindAddress`).
pub const DEFAULT_BIND_ADDRESS: &str = ":8080";

/// A running (or deliberately disabled) metrics server.
#[derive(Debug)]
pub enum MetricsServer {
    /// The bind address was `"0"`: metrics serving is disabled
    /// (controller-runtime `NewServer` returns nil in this case).
    Disabled,
    /// The server is bound and serving `GET /metrics`.
    Bound(HttpServerHandle),
}

impl MetricsServer {
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

/// Prometheus text exposition format 0.0.4; an empty body is a valid
/// (empty) exposition.
async fn metrics() -> impl IntoResponse {
    (
        [(
            header::CONTENT_TYPE,
            "text/plain; version=0.0.4; charset=utf-8",
        )],
        "",
    )
}

/// Serve `GET /metrics` on `addr` (the `metrics-bind-address` flag value).
///
/// `"0"` returns [`MetricsServer::Disabled`] without binding anything; an
/// empty address falls back to [`DEFAULT_BIND_ADDRESS`]. `shutdown`
/// triggers graceful shutdown when it resolves.
pub async fn serve<F>(addr: &str, shutdown: F) -> Result<MetricsServer, HttpServeError>
where
    F: Future<Output = ()> + Send + 'static,
{
    if addr == "0" {
        tracing::info!("metrics server is disabled (bind address \"0\")");
        return Ok(MetricsServer::Disabled);
    }
    let addr = if addr.is_empty() {
        DEFAULT_BIND_ADDRESS
    } else {
        addr
    };
    let router = Router::new().route("/metrics", get(metrics));
    let handle = spawn_http_server(addr, router, shutdown).await?;
    tracing::info!(addr = %handle.local_addr(), "metrics server listening");
    Ok(MetricsServer::Bound(handle))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn address_zero_disables_metrics() {
        let server = serve("0", std::future::pending())
            .await
            .expect("disabled server is not an error");
        assert!(matches!(server, MetricsServer::Disabled));
        assert_eq!(server.local_addr(), None);
        server
            .join()
            .await
            .expect("joining a disabled server is a no-op");
    }

    #[tokio::test]
    async fn metrics_endpoint_serves_empty_exposition() {
        let (tx, rx) = tokio::sync::oneshot::channel::<()>();
        let server = serve("127.0.0.1:0", async move {
            rx.await.ok();
        })
        .await
        .expect("bind metrics server on port 0");
        let addr = server.local_addr().expect("bound server has an address");

        let response = crate::health::test_util::http_get(addr, "/metrics").await;
        assert!(response.starts_with("HTTP/1.1 200 OK\r\n"), "{response}");
        assert!(
            response.contains("content-type: text/plain; version=0.0.4; charset=utf-8"),
            "{response}"
        );
        // Empty exposition: nothing after the header block.
        assert!(response.ends_with("\r\n\r\n"), "{response}");

        let response = crate::health::test_util::http_get(addr, "/other").await;
        assert!(response.starts_with("HTTP/1.1 404"), "{response}");

        tx.send(()).ok();
        server.join().await.expect("clean shutdown");
    }
}
