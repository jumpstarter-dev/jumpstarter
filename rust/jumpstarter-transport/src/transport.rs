//! The `Transport` trait — the core abstraction of the native-gRPC architecture.
//!
//! gRPC is "tonic over a byte duplex," so we define **one trait that yields that duplex** and
//! implement it per topology. tonic + the stock stubs/servicers + the native demux run **identically
//! over every variant** — the gRPC stack never changes, only the IO does:
//!
//! - [`InProcessTransport`] — an in-memory `tokio::io::duplex` loopback (a test embeds the
//!   exporter/driver in-process): zero IPC, zero serialization-over-socket.
//! - [`ShmTransport`] — an SHM-ring duplex ([`jumpstarter_shm::duplex`]) between co-located
//!   processes (`j` client ↔ hub; hub ↔ per-driver host): full h2 multiplexing over shared memory,
//!   **no loopback socket** — eliminating the local client↔hub and hub↔host hops.
//! - `Network` — tonic over the router tunnel (remote); the existing path, added later.
//!
//! Both `connect()` (client end) and `incoming()` (server accept end) yield a tokio
//! `AsyncRead + AsyncWrite + Connected` IO; the [`connect_channel`] helper wraps it in `TokioIo` for
//! tonic's client connector, while a server passes `incoming()` straight to `serve_with_incoming`.

use std::io;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::Mutex;
use std::task::{Context, Poll};

use hyper_util::rt::TokioIo;
use jumpstarter_shm::duplex::ShmDuplex;
use jumpstarter_shm::Ring;
use tokio::io::{AsyncRead, AsyncWrite, DuplexStream, ReadBuf};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::Stream;
use tonic::transport::server::Connected;
use tonic::transport::{Channel, Endpoint};

/// Ring sizing for the SHM transport's connection duplexes.
const SHM_RING_CAP: usize = 1024 * 1024; // 1 MiB per direction
const SHM_READ_CHUNK: usize = 256 * 1024;
const SHM_CHANNEL_CAP: usize = 8;
/// Depth of the listener's accept queue (pending server-side connection halves).
const ACCEPT_BACKLOG: usize = 64;

/// The sender half of a transport's accept queue (server-side connection halves produced by
/// `connect()`).
type AcceptTx<Io> = mpsc::Sender<io::Result<Io>>;
/// The receiver half, taken once by `incoming()`.
type AcceptRx<Io> = std::sync::Arc<Mutex<Option<mpsc::Receiver<io::Result<Io>>>>>;
/// A boxed stream of accepted connections.
type IncomingStream<Io> = Pin<Box<dyn Stream<Item = io::Result<Io>> + Send>>;

/// A connection IO for a non-socket transport: any `AsyncRead + AsyncWrite` duplex, tagged
/// [`Connected`] so tonic's server and client accept it. `ConnectInfo` is `()` — these transports
/// have no peer address.
pub struct DuplexIo<S>(pub S);

impl<S: AsyncRead + Unpin> AsyncRead for DuplexIo<S> {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_read(cx, buf)
    }
}

impl<S: AsyncWrite + Unpin> AsyncWrite for DuplexIo<S> {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.0).poll_write(cx, buf)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.0).poll_shutdown(cx)
    }
}

impl<S: Send + 'static> Connected for DuplexIo<S> {
    type ConnectInfo = ();
    fn connect_info(&self) -> Self::ConnectInfo {}
}

/// A pluggable gRPC byte transport: `connect()` opens a client connection, `incoming()` is the
/// server's stream of accepted connections. tonic runs unchanged over whatever IO this yields.
pub trait Transport: Send + Sync + 'static {
    /// The connection IO (tokio `AsyncRead + AsyncWrite` + tonic `Connected`).
    type Io: AsyncRead + AsyncWrite + Connected + Unpin + Send + 'static;

    /// Client end: open a new connection (registers a server half into `incoming`).
    fn connect(&self) -> impl std::future::Future<Output = io::Result<Self::Io>> + Send;

    /// Server end: the stream of accepted connections, taken once.
    fn incoming(&self) -> IncomingStream<Self::Io>;
}

/// Build a tonic [`Channel`] that dials `transport` (wrapping each connection in `TokioIo` for
/// tonic's client connector). The URI is a placeholder — the connector supplies the IO.
pub async fn connect_channel<T: Transport + Clone>(
    transport: &T,
) -> Result<Channel, tonic::transport::Error> {
    let transport = transport.clone();
    let connector = tower::service_fn(move |_uri: http::Uri| {
        let transport = transport.clone();
        async move {
            let io = transport.connect().await?;
            Ok::<_, io::Error>(TokioIo::new(io))
        }
    });
    Endpoint::from_static("http://jumpstarter.invalid")
        .connect_with_connector(connector)
        .await
}

// ----------------------------------------------------------------------------------------------
// InProcess

/// An in-memory loopback transport: each `connect()` makes a `tokio::io::duplex` pair and hands the
/// server half to `incoming()`. Zero IPC — used when a test embeds the exporter in-process.
#[derive(Clone)]
pub struct InProcessTransport {
    tx: AcceptTx<DuplexIo<DuplexStream>>,
    rx: AcceptRx<DuplexIo<DuplexStream>>,
    buf: usize,
}

impl InProcessTransport {
    pub fn new() -> Self {
        let (tx, rx) = mpsc::channel(ACCEPT_BACKLOG);
        Self {
            tx,
            rx: std::sync::Arc::new(Mutex::new(Some(rx))),
            buf: 64 * 1024,
        }
    }
}

impl Default for InProcessTransport {
    fn default() -> Self {
        Self::new()
    }
}

impl Transport for InProcessTransport {
    type Io = DuplexIo<DuplexStream>;

    async fn connect(&self) -> io::Result<Self::Io> {
        let (client, server) = tokio::io::duplex(self.buf);
        self.tx
            .send(Ok(DuplexIo(server)))
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::ConnectionRefused, "listener gone"))?;
        Ok(DuplexIo(client))
    }

    fn incoming(&self) -> IncomingStream<Self::Io> {
        let rx = self
            .rx
            .lock()
            .unwrap()
            .take()
            .expect("InProcessTransport::incoming called more than once");
        Box::pin(ReceiverStream::new(rx))
    }
}

// ----------------------------------------------------------------------------------------------
// Shm

/// An SHM-ring duplex transport between co-located processes. Each `connect()` mints a fresh
/// ring-pair under `dir` and hands the server-side [`ShmDuplex`] to `incoming()`. (Cross-process
/// rendezvous — passing ring paths to a peer process — is layered on later; this in-test form
/// proves tonic runs over the ring.)
#[derive(Clone)]
pub struct ShmTransport {
    dir: std::sync::Arc<tempfile::TempDir>,
    counter: std::sync::Arc<std::sync::atomic::AtomicU64>,
    tx: AcceptTx<DuplexIo<ShmDuplex>>,
    rx: AcceptRx<DuplexIo<ShmDuplex>>,
}

impl ShmTransport {
    pub fn new() -> io::Result<Self> {
        let (tx, rx) = mpsc::channel(ACCEPT_BACKLOG);
        Ok(Self {
            dir: std::sync::Arc::new(tempfile::tempdir()?),
            counter: std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0)),
            tx,
            rx: std::sync::Arc::new(Mutex::new(Some(rx))),
        })
    }

    fn ring_path(&self, name: &str) -> PathBuf {
        let n = self
            .counter
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        self.dir.path().join(format!("conn-{n}-{name}"))
    }
}

impl Transport for ShmTransport {
    type Io = DuplexIo<ShmDuplex>;

    async fn connect(&self) -> io::Result<Self::Io> {
        // Two rings: c2s (client→server) and s2c (server→client). Each producer `create`s, each
        // consumer `open`s the same file.
        let c2s = self.ring_path("c2s");
        let s2c = self.ring_path("s2c");
        let c2s_prod = Ring::create(&c2s, SHM_RING_CAP)?;
        let c2s_cons = Ring::open(&c2s, SHM_RING_CAP)?;
        let s2c_prod = Ring::create(&s2c, SHM_RING_CAP)?;
        let s2c_cons = Ring::open(&s2c, SHM_RING_CAP)?;

        // Client writes c2s, reads s2c; server writes s2c, reads c2s.
        let client = ShmDuplex::new(s2c_cons, c2s_prod, SHM_READ_CHUNK, SHM_CHANNEL_CAP);
        let server = ShmDuplex::new(c2s_cons, s2c_prod, SHM_READ_CHUNK, SHM_CHANNEL_CAP);
        self.tx
            .send(Ok(DuplexIo(server)))
            .await
            .map_err(|_| io::Error::new(io::ErrorKind::ConnectionRefused, "listener gone"))?;
        Ok(DuplexIo(client))
    }

    fn incoming(&self) -> IncomingStream<Self::Io> {
        let rx = self
            .rx
            .lock()
            .unwrap()
            .take()
            .expect("ShmTransport::incoming called more than once");
        Box::pin(ReceiverStream::new(rx))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_protocol::v1::exporter_service_server::{
        ExporterService, ExporterServiceServer,
    };
    use jumpstarter_protocol::v1::{
        DriverCallRequest, DriverCallResponse, DriverInstanceReport, EndSessionRequest,
        EndSessionResponse, GetReportResponse, GetStatusRequest, GetStatusResponse,
        LogStreamResponse, ResetRequest, ResetResponse, StreamingDriverCallRequest,
        StreamingDriverCallResponse,
    };
    use tonic::transport::Server;
    use tonic::{Request, Response, Status};

    type RespStream<T> = Pin<Box<dyn Stream<Item = Result<T, Status>> + Send>>;

    /// A minimal `ExporterService` whose `get_report` returns one named driver — enough to prove a
    /// real unary gRPC call round-trips over a `Transport`.
    struct StubExporter;

    #[tonic::async_trait]
    impl ExporterService for StubExporter {
        async fn get_report(
            &self,
            _req: Request<()>,
        ) -> Result<Response<GetReportResponse>, Status> {
            Ok(Response::new(GetReportResponse {
                reports: vec![DriverInstanceReport {
                    uuid: "stub-uuid".into(),
                    ..Default::default()
                }],
                ..Default::default()
            }))
        }
        // The legacy DriverCall RPCs exist again for old-client backwards compat; this stub only
        // exercises GetReport over a transport, so they are unimplemented.
        async fn driver_call(
            &self,
            _req: Request<DriverCallRequest>,
        ) -> Result<Response<DriverCallResponse>, Status> {
            Err(Status::unimplemented("stub"))
        }
        type StreamingDriverCallStream = RespStream<StreamingDriverCallResponse>;
        async fn streaming_driver_call(
            &self,
            _req: Request<StreamingDriverCallRequest>,
        ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
            Err(Status::unimplemented("stub"))
        }
        type LogStreamStream = RespStream<LogStreamResponse>;
        async fn log_stream(
            &self,
            _req: Request<()>,
        ) -> Result<Response<Self::LogStreamStream>, Status> {
            Err(Status::unimplemented("stub"))
        }
        async fn reset(
            &self,
            _req: Request<ResetRequest>,
        ) -> Result<Response<ResetResponse>, Status> {
            Err(Status::unimplemented("stub"))
        }
        async fn get_status(
            &self,
            _req: Request<GetStatusRequest>,
        ) -> Result<Response<GetStatusResponse>, Status> {
            Err(Status::unimplemented("stub"))
        }
        async fn end_session(
            &self,
            _req: Request<EndSessionRequest>,
        ) -> Result<Response<EndSessionResponse>, Status> {
            Err(Status::unimplemented("stub"))
        }
    }

    async fn round_trip<T: Transport + Clone>(transport: T) {
        let incoming = transport.incoming();
        let server = tokio::spawn(async move {
            Server::builder()
                .add_service(ExporterServiceServer::new(StubExporter))
                .serve_with_incoming(incoming)
                .await
        });

        let channel = connect_channel(&transport).await.expect("connect");
        let mut client =
            jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient::new(channel);
        let report = client.get_report(()).await.expect("get_report").into_inner();
        assert_eq!(report.reports.len(), 1);
        assert_eq!(report.reports[0].uuid, "stub-uuid");

        server.abort();
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn tonic_rpc_over_in_process_transport() {
        round_trip(InProcessTransport::new()).await;
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn tonic_rpc_over_shm_transport() {
        round_trip(ShmTransport::new().unwrap()).await;
    }
}
