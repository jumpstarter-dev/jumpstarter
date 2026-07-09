//! The opaque native-gRPC demux + the identity codec it rides on.
//!
//! The Rust core must proxy a **native per-driver gRPC call it has no proto knowledge of** —
//! `jumpstarter.driver.*.v1.*` — to the host that owns the driver, keyed by the
//! `x-jumpstarter-driver-uuid` invocation header, **without ever deserializing the per-driver
//! protobuf**. This generalizes the [`tunnel`](../../jumpstarter_exporter/tunnel) `RouterService`
//! proxy from the single fixed `Stream` method to arbitrary driver methods of **any** gRPC call
//! shape (unary, server-/client-/bidi-streaming).
//!
//! Two pieces live here:
//!
//! - [`BytesCodec`] — an identity `tonic::codec::Codec` whose encoder/decoder pass the raw gRPC
//!   message buffer through unchanged. It lets a tonic client/server carry opaque proto messages
//!   with no per-method Rust types, so the core is a pure byte forwarder.
//! - [`Demux`] — a catch-all gRPC server (mounted as an `axum::Router` fallback so it accepts ANY
//!   method path) that reads the driver-uuid header, looks up the backend, and relays the call via
//!   [`DriverBackend::forward_bidi`](crate::DriverBackend::forward_bidi). It frames every call as
//!   bidi-streaming — the most general HTTP/2 shape, which a unary/server-/client-streaming client
//!   reads identically — so a client uplink is never truncated to its first frame.

use std::convert::Infallible;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use bytes::{Buf, BufMut, Bytes};
use tonic::codec::{Codec, DecodeBuf, Decoder, EncodeBuf, Encoder};
use tonic::{Request, Response, Status, Streaming};

use crate::{DriverBackend, ResponseStream};

/// The invocation metadata key that selects which driver a native call targets.
pub const DRIVER_UUID_KEY: &str = "x-jumpstarter-driver-uuid";

/// Marks a native bidi call as a **byte channel** (`@exportstream` / resource `StreamData`) rather
/// than a typed method. The client sets it on byte-stream opens; the hub's `ShmChannelBackend` keys
/// on it to SHM-accelerate the bulk byte plane (vs. a typed call, which has no bulk payload). The
/// demux itself ignores it — it routes every native call opaquely regardless.
pub const BYTE_STREAM_KEY: &str = "x-jmp-byte-stream";

// --------------------------------------------------------------------------------------------
// Identity codec

/// An **identity** gRPC codec: encode/decode are byte-for-byte passthrough.
///
/// gRPC framing (the 5-byte length-prefix) is handled by tonic; this codec only moves the message
/// payload, so an opaque per-driver protobuf rides through the core untouched. `Encode`/`Decode`
/// are both [`Bytes`], so the same codec serves both the client (`forward_unary`) and the server
/// (the [`Demux`]) side of the proxy.
#[derive(Debug, Clone, Default)]
pub struct BytesCodec;

impl Codec for BytesCodec {
    type Encode = Bytes;
    type Decode = Bytes;
    type Encoder = BytesCodec;
    type Decoder = BytesCodec;

    fn encoder(&mut self) -> Self::Encoder {
        BytesCodec
    }
    fn decoder(&mut self) -> Self::Decoder {
        BytesCodec
    }
}

impl Encoder for BytesCodec {
    type Item = Bytes;
    type Error = Status;

    /// Write the raw message bytes into the gRPC encode buffer verbatim.
    fn encode(&mut self, item: Self::Item, dst: &mut EncodeBuf<'_>) -> Result<(), Self::Error> {
        dst.put_slice(&item);
        Ok(())
    }
}

impl Decoder for BytesCodec {
    type Item = Bytes;
    type Error = Status;

    /// Take the whole framed message buffer as opaque bytes (no proto parse).
    fn decode(&mut self, src: &mut DecodeBuf<'_>) -> Result<Option<Self::Item>, Self::Error> {
        // `src` holds exactly one full message; copy it out as an owned `Bytes`.
        let len = src.remaining();
        Ok(Some(src.copy_to_bytes(len)))
    }
}

// --------------------------------------------------------------------------------------------
// Demux

/// Resolves the [`DriverBackend`] a native call should be forwarded to, by driver uuid.
///
/// The test wiring uses a single fixed backend; a real exporter swaps in a routing table here
/// (uuid → per-host backend) without touching the demux itself.
pub trait Router: Send + Sync + 'static {
    /// Look up the backend for `uuid`, or `None` if no such driver is leased here.
    fn backend(&self, uuid: &str) -> Option<Arc<dyn DriverBackend>>;
}

/// A [`Router`] that forwards every call to one fixed backend, regardless of uuid — the minimal
/// wiring used to prove opaque forwarding end to end.
pub struct SingleBackend(pub Arc<dyn DriverBackend>);

impl Router for SingleBackend {
    fn backend(&self, _uuid: &str) -> Option<Arc<dyn DriverBackend>> {
        Some(self.0.clone())
    }
}

/// The native demux server: a catch-all gRPC handler that forwards any `jumpstarter.driver.*.v1.*`
/// call (of any call shape) to the backend named by the `x-jumpstarter-driver-uuid` header —
/// opaquely, with zero per-driver proto knowledge.
pub struct Demux<R: Router> {
    router: Arc<R>,
}

// A manual `Clone` (the `#[derive]` would wrongly demand `R: Clone`); the demux is just an `Arc`,
// and axum's `fallback_service` requires its service to be `Clone`.
impl<R: Router> Clone for Demux<R> {
    fn clone(&self) -> Self {
        Self {
            router: self.router.clone(),
        }
    }
}

impl<R: Router> Demux<R> {
    pub fn new(router: R) -> Self {
        Self {
            router: Arc::new(router),
        }
    }

    /// Build an [`axum::Router`] whose **fallback** is this demux, so it accepts ANY method path
    /// (`add_routes` / `serve_with_incoming` then run tonic over it). The fallback is the catch-all
    /// — there are no statically-known per-driver routes, by design.
    pub fn into_axum_router(self) -> axum::Router {
        axum::Router::new().fallback_service(self)
    }

    /// The forwarding body: read the target uuid, look up the backend, and relay the call opaquely
    /// via [`DriverBackend::forward_bidi`] — the fully general primitive. Every native call is framed
    /// as **bidi-streaming** (the most general HTTP/2 shape); the four gRPC call kinds are then just
    /// special cases a unary/server-/client-streaming client reads identically, so the opaque demux
    /// never needs to know a method's call shape and never truncates a client uplink to its first
    /// frame.
    async fn forward(
        router: Arc<R>,
        path: String,
        request: Request<Streaming<Bytes>>,
    ) -> Result<Response<ResponseStream<Bytes>>, Status> {
        let (metadata, _ext, body) = request.into_parts();
        let uuid = metadata
            .get(DRIVER_UUID_KEY)
            .and_then(|v| v.to_str().ok())
            .ok_or_else(|| Status::invalid_argument("missing x-jumpstarter-driver-uuid header"))?
            .to_owned();
        let backend = router
            .backend(&uuid)
            .ok_or_else(|| Status::not_found(format!("unknown driver uuid: {uuid}")))?;
        // Forward the opaque inbound request-frame stream + metadata to the same method path on the
        // backend, relaying its response-frame stream verbatim.
        let uplink: ResponseStream<Bytes> = Box::pin(body);
        let (initial, stream) = backend.forward_bidi(&path, metadata, uplink).await?;
        let mut response = Response::new(stream);
        *response.metadata_mut() = initial;
        Ok(response)
    }
}

/// `tower::Service` over raw HTTP so the demux can be mounted as an axum fallback. It drives a
/// per-request [`tonic::server::Grpc`] with [`BytesCodec`] (so tonic does the gRPC framing) and a
/// one-shot [`tonic::server::UnaryService`] that calls [`Demux::forward`].
impl<R: Router> tower::Service<http::Request<axum::body::Body>> for Demux<R> {
    type Response = http::Response<tonic::body::BoxBody>;
    type Error = Infallible;
    type Future = std::pin::Pin<
        Box<dyn std::future::Future<Output = Result<Self::Response, Infallible>> + Send>,
    >;

    fn poll_ready(&mut self, _cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        Poll::Ready(Ok(()))
    }

    fn call(&mut self, req: http::Request<axum::body::Body>) -> Self::Future {
        let router = self.router.clone();
        // The full method path (`/jumpstarter.driver.power.v1.PowerInterface/Echo`) — forwarded
        // unchanged to the backend so it hits the identical method.
        let path = req.uri().path().to_owned();
        Box::pin(async move {
            let mut grpc = tonic::server::Grpc::new(BytesCodec)
                .max_decoding_message_size(64 * 1024 * 1024)
                .max_encoding_message_size(64 * 1024 * 1024);
            let svc = ForwardService { router, path };
            // Bidi-streaming for every call: it subsumes all four gRPC call kinds (a unary method's
            // one-frame request/response is read identically by a unary client), so the opaque demux
            // never decides a method's call shape and never drops a client uplink's later frames.
            Ok(grpc.streaming(svc, req).await)
        })
    }
}

/// One-shot [`tonic::server::StreamingService`] adapter binding a single inbound (bidi-framed) call
/// to [`Demux::forward`].
struct ForwardService<R: Router> {
    router: Arc<R>,
    path: String,
}

impl<R: Router> tonic::server::StreamingService<Bytes> for ForwardService<R> {
    type Response = Bytes;
    type ResponseStream = ResponseStream<Bytes>;
    type Future = Pin<
        Box<
            dyn std::future::Future<Output = Result<Response<Self::ResponseStream>, Status>> + Send,
        >,
    >;

    fn call(&mut self, request: Request<Streaming<Bytes>>) -> Self::Future {
        let router = self.router.clone();
        let path = self.path.clone();
        Box::pin(Demux::forward(router, path, request))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transport::{connect_channel, InProcessTransport, ShmTransport, Transport};
    use crate::{ChannelBackend, DriverBackend};
    use std::str::FromStr;
    use tonic::transport::Server;

    /// A tonic "driver host" server that serves a single **echo** method at an arbitrary native
    /// path — it returns the request bytes verbatim. Built with [`BytesCodec`] so it, too, has no
    /// per-driver proto types; it stands in for a real per-driver host.
    #[derive(Clone)]
    struct EchoHost;

    impl tower::Service<http::Request<axum::body::Body>> for EchoHost {
        type Response = http::Response<tonic::body::BoxBody>;
        type Error = Infallible;
        type Future = std::pin::Pin<
            Box<dyn std::future::Future<Output = Result<Self::Response, Infallible>> + Send>,
        >;

        fn poll_ready(&mut self, _cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
            Poll::Ready(Ok(()))
        }

        fn call(&mut self, req: http::Request<axum::body::Body>) -> Self::Future {
            Box::pin(async move {
                let mut grpc = tonic::server::Grpc::new(BytesCodec);
                Ok(grpc.unary(EchoSvc, req).await)
            })
        }
    }

    struct EchoSvc;
    impl tonic::server::UnaryService<Bytes> for EchoSvc {
        type Response = Bytes;
        type Future = std::pin::Pin<
            Box<dyn std::future::Future<Output = Result<Response<Bytes>, Status>> + Send>,
        >;
        fn call(&mut self, request: Request<Bytes>) -> Self::Future {
            // Echo the opaque body straight back.
            Box::pin(async move { Ok(Response::new(request.into_inner())) })
        }
    }

    /// A **bidi-streaming** "driver host" that echoes EACH request frame back as a response frame —
    /// the client-/bidi-streaming analogue of [`EchoHost`]. It stands in for a native client-/bidi-
    /// streaming driver, and proves the demux forwards an entire client uplink (every frame, in
    /// order), not just its first.
    #[derive(Clone)]
    struct BidiEchoHost;

    impl tower::Service<http::Request<axum::body::Body>> for BidiEchoHost {
        type Response = http::Response<tonic::body::BoxBody>;
        type Error = Infallible;
        type Future = std::pin::Pin<
            Box<dyn std::future::Future<Output = Result<Self::Response, Infallible>> + Send>,
        >;

        fn poll_ready(&mut self, _cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
            Poll::Ready(Ok(()))
        }

        fn call(&mut self, req: http::Request<axum::body::Body>) -> Self::Future {
            Box::pin(async move {
                let mut grpc = tonic::server::Grpc::new(BytesCodec);
                Ok(grpc.streaming(BidiEchoSvc, req).await)
            })
        }
    }

    struct BidiEchoSvc;
    impl tonic::server::StreamingService<Bytes> for BidiEchoSvc {
        type Response = Bytes;
        type ResponseStream = ResponseStream<Bytes>;
        type Future = std::pin::Pin<
            Box<
                dyn std::future::Future<Output = Result<Response<Self::ResponseStream>, Status>>
                    + Send,
            >,
        >;
        fn call(&mut self, request: Request<Streaming<Bytes>>) -> Self::Future {
            // Echo each inbound request frame straight back, in order.
            Box::pin(async move {
                let stream: ResponseStream<Bytes> = Box::pin(request.into_inner());
                Ok(Response::new(stream))
            })
        }
    }

    const ECHO_PATH: &str = "/jumpstarter.driver.power.v1.PowerInterface/Echo";

    /// End-to-end opaque native forward over `transport`:
    ///
    /// driver-host (echo) ── ChannelBackend ──> Demux (catch-all) <── raw client (BytesCodec)
    ///
    /// The raw client calls `PowerInterface/Echo` through the demux with the uuid header and asserts
    /// the echoed bytes come back — proving header-keyed opaque forwarding with no per-driver proto
    /// knowledge anywhere in the core.
    async fn round_trip<Th, Td>(host_transport: Th, demux_transport: Td)
    where
        Th: Transport + Clone,
        Td: Transport + Clone,
    {
        // 1. The "driver host": an echo server reachable over `host_transport`. Like the demux it
        //    serves an arbitrary path, so it is mounted as an axum fallback (no NamedService route).
        let host_incoming = host_transport.incoming();
        let host = tokio::spawn(async move {
            let routes = axum::Router::new().fallback_service(EchoHost);
            Server::builder()
                .add_routes(routes.into())
                .serve_with_incoming(host_incoming)
                .await
        });
        // A ChannelBackend dialed at the host — the backend the demux forwards to.
        let host_channel = connect_channel(&host_transport).await.expect("dial host");
        let backend: Arc<dyn DriverBackend> = Arc::new(ChannelBackend::new(host_channel));

        // 2. The demux server in front, reachable over `demux_transport`, forwarding to `backend`.
        let demux = Demux::new(SingleBackend(backend));
        let demux_incoming = demux_transport.incoming();
        let demux_server = tokio::spawn(async move {
            Server::builder()
                .add_routes(demux.into_axum_router().into())
                .serve_with_incoming(demux_incoming)
                .await
        });

        // 3. A raw client (BytesCodec, no per-driver types) calls Echo through the demux.
        let client_channel = connect_channel(&demux_transport).await.expect("dial demux");
        let mut client = tonic::client::Grpc::new(client_channel);
        client.ready().await.expect("client ready");

        let payload = Bytes::from_static(b"\x08\x01opaque-power-proto-bytes");
        let mut request = Request::new(payload.clone());
        request
            .metadata_mut()
            .insert(DRIVER_UUID_KEY, "some-uuid".parse().unwrap());
        let path = http::uri::PathAndQuery::from_str(ECHO_PATH).unwrap();
        let response = client
            .unary(request, path, BytesCodec)
            .await
            .expect("forwarded echo call");
        assert_eq!(response.into_inner(), payload);

        demux_server.abort();
        host.abort();
    }

    /// End-to-end opaque **bidi** forward over `transport`: a client-streaming client sends N request
    /// frames through the demux to a bidi-echo host and asserts all N come back, in order — proving
    /// the demux relays an entire client uplink without truncating it to the first frame.
    async fn bidi_round_trip<Th, Td>(host_transport: Th, demux_transport: Td)
    where
        Th: Transport + Clone,
        Td: Transport + Clone,
    {
        use tokio_stream::StreamExt as _;

        // 1. The bidi-echo "driver host", reachable over `host_transport`.
        let host_incoming = host_transport.incoming();
        let host = tokio::spawn(async move {
            let routes = axum::Router::new().fallback_service(BidiEchoHost);
            Server::builder()
                .add_routes(routes.into())
                .serve_with_incoming(host_incoming)
                .await
        });
        let host_channel = connect_channel(&host_transport).await.expect("dial host");
        let backend: Arc<dyn DriverBackend> = Arc::new(ChannelBackend::new(host_channel));

        // 2. The demux in front, forwarding to `backend` via `forward_bidi`.
        let demux = Demux::new(SingleBackend(backend));
        let demux_incoming = demux_transport.incoming();
        let demux_server = tokio::spawn(async move {
            Server::builder()
                .add_routes(demux.into_axum_router().into())
                .serve_with_incoming(demux_incoming)
                .await
        });

        // 3. A raw client-streaming client (BytesCodec) sends 3 frames through the demux.
        let client_channel = connect_channel(&demux_transport).await.expect("dial demux");
        let mut client = tonic::client::Grpc::new(client_channel);
        client.ready().await.expect("client ready");

        let messages = vec![
            Bytes::from_static(b"frame-one"),
            Bytes::from_static(b"frame-two"),
            Bytes::from_static(b"frame-three"),
        ];
        let req_stream = tokio_stream::iter(messages.clone());
        let mut request = Request::new(req_stream);
        request
            .metadata_mut()
            .insert(DRIVER_UUID_KEY, "some-uuid".parse().unwrap());
        let path = http::uri::PathAndQuery::from_str(ECHO_PATH).unwrap();
        let response = client
            .streaming(request, path, BytesCodec)
            .await
            .expect("forwarded bidi call");
        let mut stream = response.into_inner();
        let mut got = Vec::new();
        while let Some(item) = stream.next().await {
            got.push(item.expect("response frame"));
        }
        assert_eq!(
            got, messages,
            "every client uplink frame must be echoed back through the demux"
        );

        demux_server.abort();
        host.abort();
    }

    /// A missing uuid header is rejected at the demux boundary (no backend lookup attempted).
    async fn missing_uuid_rejected<Td>(demux_transport: Td)
    where
        Td: Transport + Clone,
    {
        // A backend that would panic if ever called — it must not be reached.
        struct Unreachable;
        #[tonic::async_trait]
        impl DriverBackend for Unreachable {
            async fn get_report(
                &self,
            ) -> Result<jumpstarter_protocol::v1::GetReportResponse, Status> {
                unreachable!()
            }
            async fn open_router_stream(
                &self,
                _request_meta: tonic::metadata::AsciiMetadataValue,
                _uplink: crate::FrameUplink,
            ) -> Result<crate::RouterStreamOpen, Status> {
                unreachable!()
            }
            async fn log_stream(
                &self,
            ) -> Result<crate::ResponseStream<jumpstarter_protocol::v1::LogStreamResponse>, Status>
            {
                unreachable!()
            }
        }

        let backend: Arc<dyn DriverBackend> = Arc::new(Unreachable);
        let demux = Demux::new(SingleBackend(backend));
        let demux_incoming = demux_transport.incoming();
        let demux_server = tokio::spawn(async move {
            Server::builder()
                .add_routes(demux.into_axum_router().into())
                .serve_with_incoming(demux_incoming)
                .await
        });

        let client_channel = connect_channel(&demux_transport).await.expect("dial demux");
        let mut client = tonic::client::Grpc::new(client_channel);
        client.ready().await.expect("client ready");
        let path = http::uri::PathAndQuery::from_str(ECHO_PATH).unwrap();
        let err = client
            .unary(Request::new(Bytes::new()), path, BytesCodec)
            .await
            .expect_err("call without uuid header must fail");
        assert_eq!(err.code(), tonic::Code::InvalidArgument);

        demux_server.abort();
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn opaque_forward_over_in_process_transport() {
        round_trip(InProcessTransport::new(), InProcessTransport::new()).await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn opaque_forward_over_shm_transport() {
        round_trip(ShmTransport::new().unwrap(), ShmTransport::new().unwrap()).await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn opaque_bidi_forward_over_in_process_transport() {
        bidi_round_trip(InProcessTransport::new(), InProcessTransport::new()).await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn opaque_bidi_forward_over_shm_transport() {
        bidi_round_trip(ShmTransport::new().unwrap(), ShmTransport::new().unwrap()).await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn demux_rejects_missing_uuid_header() {
        missing_uuid_rejected(InProcessTransport::new()).await;
    }
}
