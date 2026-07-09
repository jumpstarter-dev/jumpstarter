//! Go-parity gRPC message compression for the router server.
//!
//! The Go router registers the gRPC gzip codec by blank import
//! (`controller/cmd/router/main.go:34`, spec 06 §3.1). With a codec
//! registered and **no** server-wide compressor option configured
//! (`router_service.go:154-159` sets only TLS + recovery interceptors +
//! keepalive), grpc-go's exact behavior is (grpc-go v1.80.0 — the version in
//! `controller/go.mod` — `server.go:1671-1706`):
//!
//! 1. **requests**: a `grpc-encoding: gzip` request is transparently
//!    decompressed; an *unregistered* encoding is rejected with
//!    `UNIMPLEMENTED`;
//! 2. **responses**: compression **mirrors the request** — responses are
//!    gzip-compressed if and only if the request came in gzip-compressed
//!    (`rc := stream.RecvCompress(); ... SetSendCompress(rc)`). grpc-go
//!    never compresses proactively for a peer that merely *advertises*
//!    `grpc-accept-encoding: gzip` (that header only gates the
//!    `SetSendCompressor` API, which the router never calls).
//!
//! tonic's knobs are close but not identical: `accept_compressed(Gzip)`
//! matches (1), but `send_compressed(Gzip)` compresses whenever the client's
//! `grpc-accept-encoding` includes gzip — and stock grpc-c/grpcio peers
//! advertise gzip on every call while sending identity, so tonic alone would
//! gzip-compress every frame on the forwarding hot path where Go sends
//! identity. [`MirrorGzipLayer`] restores the Go semantics: it strips the
//! request's `grpc-accept-encoding` header unless the request itself is
//! gzip-compressed, so tonic's send-side negotiation only ever fires as a
//! mirror. The Go registration is process-global, so the layer is applied
//! server-wide (reflection mirrors too, exactly like Go).
//!
//! Known residual divergences (both wire-tolerant, no real peer hits them):
//!
//! - an *unsupported* `grpc-encoding` (e.g. `deflate`) is `UNIMPLEMENTED` in
//!   both, but the message text differs (Go: `grpc: Decompressor is not
//!   installed for grpc-encoding "deflate"`; tonic: ``Content is compressed
//!   with `deflate` which isn't supported``);
//! - a peer that sends gzip *without* advertising `grpc-accept-encoding`
//!   gets gzip responses from Go (the mirror ignores accept-encoding) but
//!   identity from tonic; every real gRPC stack advertises what it sends.

use jumpstarter_protocol::v1::router_service_server::RouterServiceServer;
use tonic::codec::CompressionEncoding;

use crate::RouterService;

impl RouterService {
    /// Wrap the service for serving with the Go router's compression surface
    /// (the gzip codec registration of `cmd/router/main.go:34`): gzip
    /// requests are accepted and responses can be gzip-compressed. Serve it
    /// under [`MirrorGzipLayer`] to complete the Go parity (mirror-only
    /// response compression, see the module docs).
    pub fn into_server(self) -> RouterServiceServer<RouterService> {
        RouterServiceServer::new(self)
            .accept_compressed(CompressionEncoding::Gzip)
            .send_compressed(CompressionEncoding::Gzip)
    }
}

/// Tower layer reproducing grpc-go's mirror-only response compression: the
/// request's `grpc-accept-encoding` header is dropped unless the request is
/// itself gzip-compressed, so a `send_compressed(Gzip)` tonic service
/// compresses responses exactly when grpc-go would (see the module docs).
#[derive(Debug, Clone, Copy, Default)]
pub struct MirrorGzipLayer;

impl<S> tower_layer::Layer<S> for MirrorGzipLayer {
    type Service = MirrorGzip<S>;

    fn layer(&self, inner: S) -> Self::Service {
        MirrorGzip { inner }
    }
}

/// The service produced by [`MirrorGzipLayer`].
#[derive(Debug, Clone)]
pub struct MirrorGzip<S> {
    inner: S,
}

impl<S, B> tower_service::Service<http::Request<B>> for MirrorGzip<S>
where
    S: tower_service::Service<http::Request<B>>,
{
    type Response = S::Response;
    type Error = S::Error;
    type Future = S::Future;

    fn poll_ready(
        &mut self,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, mut request: http::Request<B>) -> Self::Future {
        // grpc-go mirrors `stream.RecvCompress()` (the `grpc-encoding`
        // request header); identity/absent means "respond uncompressed".
        let gzip_request = request
            .headers()
            .get("grpc-encoding")
            .is_some_and(|encoding| encoding.as_bytes() == b"gzip");
        if !gzip_request {
            request.headers_mut().remove("grpc-accept-encoding");
        }
        self.inner.call(request)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tower_layer::Layer;
    use tower_service::Service;

    /// Terminal service capturing the headers it was called with.
    #[derive(Default)]
    struct Capture {
        headers: std::rc::Rc<std::cell::RefCell<Option<http::HeaderMap>>>,
    }

    impl Service<http::Request<()>> for Capture {
        type Response = ();
        type Error = std::convert::Infallible;
        type Future = std::future::Ready<Result<(), Self::Error>>;

        fn poll_ready(
            &mut self,
            _cx: &mut std::task::Context<'_>,
        ) -> std::task::Poll<Result<(), Self::Error>> {
            std::task::Poll::Ready(Ok(()))
        }

        fn call(&mut self, request: http::Request<()>) -> Self::Future {
            *self.headers.borrow_mut() = Some(request.headers().clone());
            std::future::ready(Ok(()))
        }
    }

    fn seen_by_inner(request_headers: &[(&str, &str)]) -> http::HeaderMap {
        let capture = Capture::default();
        let seen = std::rc::Rc::clone(&capture.headers);
        let mut service = MirrorGzipLayer.layer(capture);
        let mut request = http::Request::new(());
        for (name, value) in request_headers {
            request.headers_mut().insert(
                http::header::HeaderName::from_bytes(name.as_bytes()).unwrap(),
                http::header::HeaderValue::from_str(value).unwrap(),
            );
        }
        // Capture's future is `Ready` and its side effect happens in `call`.
        service
            .call(request)
            .into_inner()
            .expect("infallible capture service");
        let headers = seen.borrow_mut().take().expect("inner service called");
        headers
    }

    /// gzip-compressed request: accept-encoding passes through, so tonic
    /// mirrors with gzip responses (grpc-go `SetSendCompress(rc)`).
    #[test]
    fn gzip_request_keeps_accept_encoding() {
        let headers = seen_by_inner(&[
            ("grpc-encoding", "gzip"),
            ("grpc-accept-encoding", "identity, deflate, gzip"),
        ]);
        assert_eq!(
            headers.get("grpc-accept-encoding").map(|v| v.as_bytes()),
            Some(b"identity, deflate, gzip".as_slice())
        );
    }

    /// Identity request from a peer that merely advertises gzip (every stock
    /// grpcio client): grpc-go responds identity, so the advertisement is
    /// stripped before tonic's send-side negotiation sees it.
    #[test]
    fn identity_request_drops_accept_encoding() {
        let headers = seen_by_inner(&[("grpc-accept-encoding", "identity, deflate, gzip")]);
        assert!(headers.get("grpc-accept-encoding").is_none());

        let headers = seen_by_inner(&[
            ("grpc-encoding", "identity"),
            ("grpc-accept-encoding", "gzip"),
        ]);
        assert!(headers.get("grpc-accept-encoding").is_none());
    }

    /// A non-gzip encoding is not a mirror trigger (tonic will reject it as
    /// UNIMPLEMENTED downstream, like unregistered codecs in grpc-go).
    #[test]
    fn unsupported_encoding_drops_accept_encoding() {
        let headers = seen_by_inner(&[
            ("grpc-encoding", "deflate"),
            ("grpc-accept-encoding", "deflate, gzip"),
        ]);
        assert!(headers.get("grpc-accept-encoding").is_none());
    }
}
