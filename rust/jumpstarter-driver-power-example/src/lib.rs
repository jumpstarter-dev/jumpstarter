//! Proto-first example power driver — the W1 vertical headline.
//!
//! The author writes **only** a native `tonic` service impl (`impl PowerInterface for MockPower`)
//! plus the [`MockPower`] state. Everything else is generated or stock:
//!
//! - the `PowerInterface` service trait, the `PowerReading` message, and the `FILE_DESCRIPTOR_SET`
//!   are stock `tonic-build` output ([`proto`], compiled by `build.rs` from the hand-authored
//!   `interfaces/.../power.proto`);
//! - the driver **host** is the stock `PowerInterfaceServer` served by the generic
//!   [`jumpstarter_driver_runtime::serve_driver`] over the **SHM transport** — there is NO
//!   generated, per-interface adapter;
//! - the typed [`PowerClient`] is generated **at build time** by `build.rs` (`jumpstarter-codegen`'s
//!   `RustGenerator` over the `FILE_DESCRIPTOR_SET`) into `OUT_DIR` — NOT committed. The only
//!   committed code in this crate is the author's [`MockPower`] driver implementation.
//!
//! In the round-trip test below, [`MockPower`] is served by `serve_driver` over SHM and driven by
//! the generated [`PowerClient`] through the real `client → exporter/demux → SHM → tonic service`
//! loop — not a direct method call.

pub mod proto;

pub mod generated {
    //! Codegen output produced at **build time** by `build.rs` (NOT committed): the typed client.
    //!
    //! The interface stubs (the `tonic` service trait + prost messages + `FILE_DESCRIPTOR_SET`) are
    //! `tonic-build` output, also in `OUT_DIR`. The driver host is the stock `tonic` service served
    //! by the generic [`jumpstarter_driver_runtime::serve_driver`] — no per-interface adapter is
    //! generated. The only committed code in this crate is the author's driver impl ([`MockPower`]).

    pub mod power_client {
        include!(concat!(env!("OUT_DIR"), "/power_client.rs"));
    }
}

pub use generated::power_client::PowerClient;

use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};

use proto::power_interface_server::PowerInterface;
use proto::PowerReading;
use tonic::{Request, Response, Status};

/// The `jumpstarter.dev/client` class advertised for the mock power driver (the existing Python
/// power client; the native `PowerClient` drives it identically over the descriptor).
pub const POWER_CLIENT_CLASS: &str = "jumpstarter_driver_power.client.PowerClient";

/// A mock power driver authored as a native `tonic` service: `on`/`off` flip a powered flag, and
/// `read` streams a few [`PowerReading`]s reflecting the current state (powered-on -> `voltage > 0`,
/// off -> `0.0`). The author implements only the generated `PowerInterface` trait — no descriptor
/// building, no `DriverBackend` boilerplate.
#[derive(Default)]
pub struct MockPower {
    /// `1` while powered on, `0` while off. An atomic so the `&self` trait methods can mutate it.
    powered: AtomicU64,
}

impl MockPower {
    /// The nominal on-voltage (volts) reported while powered.
    const ON_VOLTAGE: f64 = 5.0;
    /// The nominal on-current (amps) reported while powered.
    const ON_CURRENT: f64 = 2.0;
    /// How many readings `read` streams.
    const READINGS: usize = 3;

    /// Whether the mock is currently powered on (for assertions in tests).
    pub fn is_on(&self) -> bool {
        self.powered.load(Ordering::SeqCst) != 0
    }
}

#[tonic::async_trait]
impl PowerInterface for MockPower {
    async fn on(&self, _request: Request<()>) -> Result<Response<()>, Status> {
        self.powered.store(1, Ordering::SeqCst);
        Ok(Response::new(()))
    }

    async fn off(&self, _request: Request<()>) -> Result<Response<()>, Status> {
        self.powered.store(0, Ordering::SeqCst);
        Ok(Response::new(()))
    }

    type ReadStream = Pin<Box<dyn tokio_stream::Stream<Item = Result<PowerReading, Status>> + Send>>;

    async fn read(&self, _request: Request<()>) -> Result<Response<Self::ReadStream>, Status> {
        let (voltage, current) = if self.is_on() {
            (Self::ON_VOLTAGE, Self::ON_CURRENT)
        } else {
            (0.0, 0.0)
        };
        let readings: Vec<Result<PowerReading, Status>> = (0..Self::READINGS)
            .map(|_| Ok(PowerReading { voltage, current }))
            .collect();
        let stream: Self::ReadStream = Box::pin(tokio_stream::iter(readings));
        Ok(Response::new(stream))
    }
}

#[cfg(test)]
mod round_trip_tests {
    //! The W1 exit criterion: `MockPower` (authored as `impl PowerInterface`) is served through the
    //! **generated** `PowerBackend` over the real demux + `ClientSession` native transport, and
    //! driven by the **generated** `PowerClient`. `on`/`off` (unary) and `read` (server-streaming)
    //! reach the driver through `forward_unary`/`forward_stream` — a true round-trip, mirroring
    //! `jumpstarter-core`'s `client.rs` native demux tests + `driver.rs`'s
    //! `native_forward_unary_dispatches_to_the_driver`.

    use std::sync::Arc;

    use jumpstarter_core::ClientSession;
    use jumpstarter_protocol::v1::exporter_service_server::{
        ExporterService, ExporterServiceServer,
    };
    use jumpstarter_protocol::v1::{
        DriverCallRequest, DriverCallResponse, EndSessionRequest, EndSessionResponse,
        GetReportResponse, GetStatusRequest, GetStatusResponse, LogStreamResponse, ResetRequest,
        ResetResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
    };
    use jumpstarter_transport::demux::{Demux, SingleBackend};
    use jumpstarter_transport::DriverBackend;
    use tokio::net::UnixListener;
    use tokio_stream::wrappers::UnixListenerStream;
    use tokio_stream::StreamExt as _;
    use tonic::service::Routes;
    use tonic::{Request, Response, Status};

    use super::*;

    /// A minimal `ExporterService` whose `GetReport` proxies the per-driver `backend.get_report()`
    /// (so the client's `PowerClient::new` name-lookup resolves the driver uuid); every other RPC is
    /// unimplemented. The native per-driver calls go to the demux fallback, not here.
    struct ReportExporter {
        backend: Arc<dyn DriverBackend>,
    }

    type RespStream<T> =
        std::pin::Pin<Box<dyn tokio_stream::Stream<Item = Result<T, Status>> + Send>>;

    #[tonic::async_trait]
    impl ExporterService for ReportExporter {
        async fn get_report(
            &self,
            _req: Request<()>,
        ) -> Result<Response<GetReportResponse>, Status> {
            Ok(Response::new(self.backend.get_report().await?))
        }
        async fn driver_call(
            &self,
            _req: Request<DriverCallRequest>,
        ) -> Result<Response<DriverCallResponse>, Status> {
            Err(Status::unimplemented("native-only fixture"))
        }
        type StreamingDriverCallStream = RespStream<StreamingDriverCallResponse>;
        async fn streaming_driver_call(
            &self,
            _req: Request<StreamingDriverCallRequest>,
        ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
            Err(Status::unimplemented("native-only fixture"))
        }
        type LogStreamStream = RespStream<LogStreamResponse>;
        async fn log_stream(
            &self,
            _req: Request<()>,
        ) -> Result<Response<Self::LogStreamStream>, Status> {
            Err(Status::unimplemented("native-only fixture"))
        }
        async fn reset(
            &self,
            _req: Request<ResetRequest>,
        ) -> Result<Response<ResetResponse>, Status> {
            Err(Status::unimplemented("native-only fixture"))
        }
        async fn get_status(
            &self,
            _req: Request<GetStatusRequest>,
        ) -> Result<Response<GetStatusResponse>, Status> {
            Err(Status::unimplemented("native-only fixture"))
        }
        async fn end_session(
            &self,
            _req: Request<EndSessionRequest>,
        ) -> Result<Response<EndSessionResponse>, Status> {
            Err(Status::unimplemented("native-only fixture"))
        }
    }

    /// Stand up an exporter serving `backend` over a private UDS socket — the typed
    /// `ExporterService` (for `GetReport`) plus the native [`Demux`] as the catch-all fallback (for
    /// the per-driver `On`/`Off`/`Read` calls), exactly as `jumpstarter-exporter`'s `session_routes`
    /// wires them. Then connect a `ClientSession` to it via the **public** `ClientSession::connect`
    /// (a UDS path) — the real transport-socket path a leased client uses. Returns the session, the
    /// spawned server handle, and the tempdir whose lifetime keeps the socket alive.
    async fn serve(
        backend: Arc<dyn DriverBackend>,
    ) -> (
        ClientSession,
        tokio::task::JoinHandle<Result<(), tonic::transport::Error>>,
        tempfile::TempDir,
    ) {
        let dir = tempfile::tempdir().expect("tempdir");
        let socket = dir.path().join("exporter.sock");
        let listener = UnixListener::bind(&socket).expect("bind UDS");
        let incoming = UnixListenerStream::new(listener);

        // Typed ExporterService (GetReport) + the native per-driver demux as the catch-all fallback,
        // mirroring `jumpstarter-exporter::session::session_routes`.
        let exporter = ExporterServiceServer::new(ReportExporter {
            backend: backend.clone(),
        });
        let mut builder = Routes::builder();
        builder.add_service(exporter);
        let axum_router = builder
            .routes()
            .into_axum_router()
            .fallback_service(Demux::new(SingleBackend(backend)));
        let routes = Routes::from(axum_router);

        let server = tokio::spawn(async move {
            tonic::transport::Server::builder()
                .add_routes(routes)
                .serve_with_incoming(incoming)
                .await
        });
        let session = ClientSession::connect(socket.to_string_lossy().into_owned())
            .await
            .expect("connect ClientSession over UDS");
        (session, server, dir)
    }

    /// Stream a `read()` call to completion, collecting the decoded readings.
    async fn collect_readings(
        stream: impl tokio_stream::Stream<Item = Result<proto::PowerReading, jumpstarter_core::error::DriverCallError>>,
    ) -> Vec<proto::PowerReading> {
        let mut stream = Box::pin(stream);
        let mut readings = Vec::new();
        while let Some(item) = stream.next().await {
            readings.push(item.expect("decoded reading"));
        }
        readings
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn on_off_read_round_trip_through_serve_driver_over_shm() {
        // The ENTIRE driver host: the author's stock `tonic` service, served over the SHM transport
        // by the generic runtime. No generated adapter — `serve_driver` returns the hub-side
        // `ChannelBackend` over the SHM hop.
        let backend = jumpstarter_driver_runtime::serve_driver(
            "power",
            POWER_CLIENT_CLASS,
            proto::FILE_DESCRIPTOR_SET.to_vec(),
            proto::power_interface_server::PowerInterfaceServer::new(MockPower::default()),
        )
        .await
        .expect("serve power driver over SHM");

        let (session, server, _dir) = serve(backend).await;

        // The GENERATED client, resolving the driver uuid from GetReport by the name label.
        let client = PowerClient::new(&session, "power")
            .await
            .expect("resolve power client from report");

        // Initially off: a Read streams zero-voltage readings (proving the call reached the driver
        // through client → exporter/demux → SHM → tonic service, all the way back).
        let readings = collect_readings(client.read().await.expect("read stream (off)")).await;
        assert_eq!(readings.len(), MockPower::READINGS, "Read yields N readings");
        for r in &readings {
            assert_eq!(r.voltage, 0.0, "off -> 0 V");
            assert_eq!(r.current, 0.0, "off -> 0 A");
        }

        // on() (unary) flips the driver; Read now streams powered readings.
        client.on().await.expect("on() unary");
        let readings = collect_readings(client.read().await.expect("read stream (on)")).await;
        assert_eq!(readings.len(), MockPower::READINGS);
        for r in &readings {
            assert!(r.voltage > 0.0, "powered-on voltage must be > 0, got {}", r.voltage);
            assert_eq!(r.voltage, MockPower::ON_VOLTAGE);
            assert_eq!(r.current, MockPower::ON_CURRENT);
        }

        // off() (unary) flips it back.
        client.off().await.expect("off() unary");
        let readings = collect_readings(client.read().await.expect("read stream (off2)")).await;
        for r in &readings {
            assert_eq!(r.voltage, 0.0, "off -> 0 V");
        }

        server.abort();
    }
}
