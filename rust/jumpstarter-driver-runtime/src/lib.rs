//! The generic, codegen-free driver host.
//!
//! A proto-first Jumpstarter driver is authored as a **stock `tonic` service** — the author
//! implements the `tonic-build`-generated service trait (`impl PowerInterface for MockPower`) and
//! nothing else. [`serve_driver`] takes that service (its `*Server<T>` form) and serves it over the
//! Jumpstarter **SHM transport** ([`ShmTransport`] — the hub↔driver-host hop, the proper
//! high-performance channel), alongside a minimal `ExporterService` that advertises the interface
//! descriptor over `GetReport`. It returns the hub-side view of that SHM hop as the **existing
//! generic [`ChannelBackend`]**, so:
//!
//! ```text
//!   client ──plain gRPC──▶ exporter{GetReport + Demux} ──ChannelBackend over SHM──▶ serve_driver host
//!                                                                                   { ExporterService + tonic service }
//! ```
//!
//! There is **no per-interface generated adapter**: `tonic` itself decodes/dispatches the typed
//! request on the host, the SHM transport + `ChannelBackend` forward opaque frames, and this one
//! generic runtime serves any interface. The only generated code in the whole system is the typed
//! *client* (which the consumer uses); the driver side is stock `tonic` + this runtime.

use std::collections::HashMap;
use std::convert::Infallible;
use std::pin::Pin;
use std::sync::Arc;

use jumpstarter_protocol::v1::exporter_service_server::{ExporterService, ExporterServiceServer};
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, DriverInstanceReport, EndSessionRequest,
    EndSessionResponse, GetReportResponse, GetStatusRequest, GetStatusResponse, LogStreamResponse,
    ResetRequest, ResetResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use jumpstarter_transport::transport::{connect_channel, ShmTransport, Transport};
use jumpstarter_transport::{ChannelBackend, DriverBackend};
use tonic::transport::Server;
use tonic::{Request, Response, Status};

/// `interface!()` — the one-line `src/lib.rs` of a driver crate: includes the build-time-generated
/// `proto` module + typed client (the aggregator written by `jumpstarter_codegen::build`).
pub use jumpstarter_driver_macros::interface;

/// `#[driver(client = "…")]` — on `impl <Interface> for <Driver>`: auto-registers the driver (sets its
/// default client), so the host binary's `main` is just [`host_main!`]. The Rust analog of the JVM
/// `@JumpstarterDriver` annotation.
pub use jumpstarter_driver_macros::driver;

/// `#[client_cli]` — on a typed CLI: auto-registers it, so the client binary's `main` is just
/// [`client_main!`]. The client-side mirror of [`driver`] (and the JVM `@JumpstarterClientCli`).
pub use jumpstarter_driver_macros::client_cli;

/// The client PRIMITIVES the entrypoint builds on, re-exported so a driver crate reaches them (and the
/// `#[client_cli]`-generated code) through this one runtime crate.
pub use jumpstarter_core::{resolve_driver_uuid, ClientSession};

/// Re-exported so the `#[driver]`/`#[client_cli]`-generated registrations can reach `inventory::submit!`.
#[doc(hidden)]
pub use inventory;

const CLIENT_LABEL: &str = "jumpstarter.dev/client";
const NAME_LABEL: &str = "jumpstarter.dev/name";

type RespStream<T> = Pin<Box<dyn tokio_stream::Stream<Item = Result<T, Status>> + Send>>;

/// Serve `service` (a stock `tonic` `*Server<T>`) as a Jumpstarter driver host over the SHM
/// transport, returning the hub-side [`DriverBackend`] (a [`ChannelBackend`] over the SHM hop).
///
/// - `name` is the driver instance's `jumpstarter.dev/name` label (the accessor a client resolves).
/// - `client_class` is the `jumpstarter.dev/client` label (the client class that drives it).
/// - `descriptor_set` is the interface's serialized `FileDescriptorSet` — `tonic-build`'s
///   `FILE_DESCRIPTOR_SET`, the single descriptor source of truth — advertised over `GetReport`.
/// - `service` is the author's typed service wrapped in its generated server, e.g.
///   `PowerInterfaceServer::new(MockPower::default())`.
///
/// The host runs detached on the current tokio runtime; the returned backend owns the SHM channel
/// that keeps it alive.
pub async fn serve_driver<S>(
    name: &str,
    client_class: &str,
    descriptor_set: Vec<u8>,
    service: S,
) -> std::io::Result<Arc<dyn DriverBackend>>
where
    S: tower::Service<
            http::Request<tonic::body::BoxBody>,
            Response = http::Response<tonic::body::BoxBody>,
            Error = Infallible,
        > + tonic::server::NamedService
        + Clone
        + Send
        + Sync
        + 'static,
    S::Future: Send + 'static,
{
    let uuid = uuid::Uuid::new_v4().to_string();
    let report = GetReportResponse {
        reports: vec![DriverInstanceReport {
            uuid,
            parent_uuid: None,
            labels: HashMap::from([
                (CLIENT_LABEL.to_string(), client_class.to_string()),
                (NAME_LABEL.to_string(), name.to_string()),
            ]),
            description: None,
            methods_description: HashMap::new(),
            // The single descriptor source of truth — the same FileDescriptorSet tonic-build emits.
            descriptor_set: Some(descriptor_set),
        }],
        ..Default::default()
    };

    // The hub↔driver-host hop: a fresh SHM ring duplex. tonic runs over it exactly as over a socket.
    let shm = ShmTransport::new()?;
    let incoming = shm.incoming();
    let exporter = ExporterServiceServer::new(ReportOnlyExporter { report });

    tokio::spawn(async move {
        if let Err(e) = Server::builder()
            .add_service(exporter) // GetReport: advertises the descriptor.
            .add_service(service) // the author's typed interface: tonic decodes/dispatches.
            .serve_with_incoming(incoming)
            .await
        {
            tracing::warn!(error = %e, "driver host server exited");
        }
    });

    // Dial the host over the same SHM transport; the resulting tonic Channel keeps a clone of the
    // transport (and thus the rings) alive, so the local `shm` may drop. The ChannelBackend is the
    // generic, interface-agnostic DriverBackend the hub/exporter forwards through.
    let channel = connect_channel(&shm).await.map_err(std::io::Error::other)?;
    Ok(Arc::new(ChannelBackend::new(channel)))
}

/// A minimal `ExporterService` for a single driver host: it answers `GetReport` with the host's one
/// driver-instance report (carrying the interface descriptor) and declines everything else — a
/// proto-first host serves its interface as native gRPC, not through the legacy `DriverCall` codec.
struct ReportOnlyExporter {
    report: GetReportResponse,
}

#[tonic::async_trait]
impl ExporterService for ReportOnlyExporter {
    async fn get_report(&self, _req: Request<()>) -> Result<Response<GetReportResponse>, Status> {
        Ok(Response::new(self.report.clone()))
    }

    async fn driver_call(
        &self,
        _req: Request<DriverCallRequest>,
    ) -> Result<Response<DriverCallResponse>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    type StreamingDriverCallStream = RespStream<StreamingDriverCallResponse>;
    async fn streaming_driver_call(
        &self,
        _req: Request<StreamingDriverCallRequest>,
    ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    type LogStreamStream = RespStream<LogStreamResponse>;
    async fn log_stream(
        &self,
        _req: Request<()>,
    ) -> Result<Response<Self::LogStreamStream>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    async fn reset(
        &self,
        _req: Request<ResetRequest>,
    ) -> Result<Response<ResetResponse>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    async fn get_status(
        &self,
        _req: Request<GetStatusRequest>,
    ) -> Result<Response<GetStatusResponse>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }

    async fn end_session(
        &self,
        _req: Request<EndSessionRequest>,
    ) -> Result<Response<EndSessionResponse>, Status> {
        Err(Status::unimplemented("proto-first host serves native gRPC only"))
    }
}

/// The complete entrypoint for a STANDALONE native driver host — call this from a driver crate's own
/// `fn main` and the crate *is* its own host, no per-crate boilerplate:
///
/// ```ignore
/// fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
///     jumpstarter_driver_runtime::run_host(
///         POWER_CLIENT_CLASS,
///         proto::FILE_DESCRIPTOR_SET,
///         proto::power_interface_server::PowerInterfaceServer::new(MockPower::default()),
///     )
/// }
/// ```
///
/// It parses `--serve <uds>` from argv, installs the `JMP_HUB_PID` parent-death watchdog, reads the
/// hub's single-entry config on stdin (serving the driver under that `export:` entry name), serves
/// `service` via [`serve_driver`], and serves the driver-host seam until the hub kills the process.
/// Builds its own Tokio runtime, so the crate's `main` stays a plain sync fn. The polyglot hub spawns
/// the resulting binary for a `type: rust:<crate>` entry (resolved to the crate's own bin).
pub fn run_host<S>(
    client_class: &str,
    descriptor_set: &[u8],
    service: S,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>>
where
    S: tower::Service<
            http::Request<tonic::body::BoxBody>,
            Response = http::Response<tonic::body::BoxBody>,
            Error = Infallible,
        > + tonic::server::NamedService
        + Clone
        + Send
        + Sync
        + 'static,
    S::Future: Send + 'static,
{
    use jumpstarter_config::YamlConfig as _;
    use std::io::Read as _;

    let uds = parse_serve_arg()
        .ok_or("usage: <driver-host> --serve <uds>  (single-entry config on stdin)")?;

    // Exit if the hub dies before it can SIGKILL us (POSIX parent-death watchdog via JMP_HUB_PID).
    jumpstarter_exporter::exit_when_orphaned();

    // The hub streams the single-entry config on stdin (EOF-terminated); we advertise the driver
    // under its `export:` entry name (the accessor the client and the hub's federation route by).
    let mut config_yaml = String::new();
    std::io::stdin().read_to_string(&mut config_yaml)?;
    let config = jumpstarter_config::ExporterConfig::from_yaml(&config_yaml)?;
    let name = config
        .export
        .keys()
        .next()
        .ok_or("config has no export entry")?
        .clone();

    let descriptor_set = descriptor_set.to_vec();
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async move {
        let backend = serve_driver(&name, client_class, descriptor_set, service).await?;
        jumpstarter_exporter::session::serve_native_host(std::path::Path::new(&uds), backend).await?;
        Ok(())
    })
}

/// Extract the `--serve <uds>` value from this process's argv.
fn parse_serve_arg() -> Option<String> {
    parse_value_arg("--serve")
}

/// The value following `flag` in this process's argv, if present.
fn parse_value_arg(flag: &str) -> Option<String> {
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == flag {
            return args.next();
        }
    }
    None
}

/// The full name (`<package>.<Service>`) of the first service in a serialized `FileDescriptorSet` —
/// the interface a registered driver implements. Used to select among a crate's drivers at runtime.
fn descriptor_interface(descriptor: &[u8]) -> Option<String> {
    use prost::Message as _;
    let set = prost_types::FileDescriptorSet::decode(descriptor).ok()?;
    set.file.iter().find_map(|f| {
        let pkg = f.package();
        f.service.first().map(|s| {
            if pkg.is_empty() {
                s.name().to_string()
            } else {
                format!("{pkg}.{}", s.name())
            }
        })
    })
}

type HostServe = Box<
    dyn Fn(
            String,
        )
            -> Pin<Box<dyn std::future::Future<Output = std::io::Result<Arc<dyn DriverBackend>>> + Send>>
        + Send
        + Sync,
>;

struct HostDriver {
    descriptor: &'static [u8],
    serve: HostServe,
}

/// A driver-host registry — the entrypoint for a crate that implements one OR MORE interfaces. Each
/// interface's driver is registered with [`Host::driver`]; [`Host::run`] serves the one the hub
/// selected (the single registered driver, or the `--interface <fqn>` match when several are
/// registered — exactly one runs per process). Replaces the per-interface `<short>_host!` macro:
///
/// ```ignore
/// fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
///     jumpstarter_driver_runtime::Host::new()
///         .driver(POWER_CLIENT_CLASS, proto::FILE_DESCRIPTOR_SET,
///                 || proto::power_interface_server::PowerInterfaceServer::new(MockPower::default()))
///         // .driver(..) for each additional interface this crate implements
///         .run()
/// }
/// ```
#[derive(Default)]
pub struct Host {
    drivers: Vec<HostDriver>,
}

impl Host {
    pub fn new() -> Self {
        Self::default()
    }

    /// Build a host from every `#[driver]`-annotated driver in the crate (collected at link time).
    /// The entrypoint behind [`host_main!`] — the author never lists drivers explicitly.
    pub fn from_inventory() -> Self {
        let mut host = Self::new();
        for reg in inventory::iter::<DriverRegistration> {
            host.drivers.push(HostDriver {
                descriptor: reg.descriptor,
                serve: Box::new(reg.serve),
            });
        }
        host
    }

    /// Register a driver for one interface: its stock `tonic` server (built fresh per lease by
    /// `make`), the `jumpstarter.dev/client` label, and the interface `FILE_DESCRIPTOR_SET`.
    pub fn driver<S, F>(
        mut self,
        client_class: &'static str,
        descriptor: &'static [u8],
        make: F,
    ) -> Self
    where
        S: tower::Service<
                http::Request<tonic::body::BoxBody>,
                Response = http::Response<tonic::body::BoxBody>,
                Error = Infallible,
            > + tonic::server::NamedService
            + Clone
            + Send
            + Sync
            + 'static,
        S::Future: Send + 'static,
        F: Fn() -> S + Send + Sync + 'static,
    {
        let serve: HostServe = Box::new(move |name| {
            let service = make();
            Box::pin(async move {
                serve_driver(&name, client_class, descriptor.to_vec(), service).await
            })
        });
        self.drivers.push(HostDriver { descriptor, serve });
        self
    }

    /// Parse `--serve <uds>` + the optional `--interface <fqn>`, read the hub's single-entry config on
    /// stdin, select the registered driver, install the parent-death watchdog, and serve the
    /// driver-host seam until the hub kills the process. Builds its own Tokio runtime (sync `main`).
    pub fn run(self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        use jumpstarter_config::YamlConfig as _;
        use std::io::Read as _;

        let uds = parse_serve_arg()
            .ok_or("usage: <driver-host> --serve <uds> [--interface <fqn>]  (config on stdin)")?;
        jumpstarter_exporter::exit_when_orphaned();

        let mut config_yaml = String::new();
        std::io::stdin().read_to_string(&mut config_yaml)?;
        let config = jumpstarter_config::ExporterConfig::from_yaml(&config_yaml)?;
        let name = config
            .export
            .keys()
            .next()
            .ok_or("config has no export entry")?
            .clone();

        let driver = self.select(parse_value_arg("--interface").as_deref())?;

        let runtime = tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()?;
        runtime.block_on(async move {
            let backend = (driver.serve)(name).await?;
            jumpstarter_exporter::session::serve_native_host(std::path::Path::new(&uds), backend)
                .await?;
            Ok(())
        })
    }

    /// Pick the driver to run: the only one registered, else the one whose interface matches
    /// `--interface`, else an error (ambiguous).
    fn select(&self, interface: Option<&str>) -> Result<&HostDriver, String> {
        match (self.drivers.as_slice(), interface) {
            ([], _) => Err("no drivers registered in this host".into()),
            ([only], _) => Ok(only),
            (many, Some(iface)) => many
                .iter()
                .find(|d| descriptor_interface(d.descriptor).as_deref() == Some(iface))
                .ok_or_else(|| format!("no registered driver implements interface `{iface}`")),
            (_, None) => {
                Err("this host registers multiple drivers; pass `--interface <fqn>`".into())
            }
        }
    }
}

/// What a `#[driver]` registration's `serve` builds: the driver's [`DriverBackend`] for an instance
/// name (it calls [`serve_driver`] with the baked-in server type + descriptor + client class).
pub type HostServeFn =
    fn(String) -> Pin<Box<dyn std::future::Future<Output = std::io::Result<Arc<dyn DriverBackend>>> + Send>>;

/// One driver registered by `#[driver]`, collected at link time by [`Host::from_inventory`].
pub struct DriverRegistration {
    pub client_class: &'static str,
    pub descriptor: &'static [u8],
    pub serve: HostServeFn,
}

inventory::collect!(DriverRegistration);

/// Generate the host binary's whole `fn main` from the crate's `#[driver]` registrations:
/// `jumpstarter_driver_runtime::host_main!();` is the entire `src/main.rs`. (The crate's lib must be
/// linked into the bin — `use <crate> as _;` next to this when the bin references nothing else.)
#[macro_export]
macro_rules! host_main {
    () => {
        fn main() -> ::std::result::Result<
            (),
            ::std::boxed::Box<dyn ::std::error::Error + ::std::marker::Send + ::std::marker::Sync>,
        > {
            $crate::Host::from_inventory().run()
        }
    };
}

// ── Client-CLI entrypoint ──────────────────────────────────────────────────────────────────────
// The client-side mirror of `Host`/`#[driver]`/`host_main!`, living in this same runtime crate so a
// driver crate imports ALL its entrypoint glue from `jumpstarter_driver_runtime`. Built over core's
// client primitives (`ClientSession`, `resolve_driver_uuid`); reuses `descriptor_interface` to match.

type ClientRun = Box<
    dyn for<'a> Fn(
        &'a [String],
        &'a ClientSession,
        &'a str,
    ) -> Pin<Box<dyn std::future::Future<Output = i32> + 'a>>,
>;

struct ClientEntry {
    descriptor: &'static [u8],
    run: ClientRun,
}

/// A driver-client registry — the entrypoint for a crate whose clients drive one OR MORE interfaces.
/// Each interface's CLI is registered with [`Client::cli`] (or auto-registered by `#[client_cli]`);
/// [`Client::run`] connects, resolves the driver, and dispatches to the selected CLI (the only one, or
/// the `--interface <fqn>` match). One client runs per process. The mirror of [`Host`].
#[derive(Default)]
pub struct Client {
    clis: Vec<ClientEntry>,
}

impl Client {
    pub fn new() -> Self {
        Self::default()
    }

    /// Build a client from every `#[client_cli]`-annotated CLI in the crate (collected at link time).
    /// The entrypoint behind [`client_main!`].
    pub fn from_inventory() -> Self {
        let mut client = Self::new();
        for reg in inventory::iter::<ClientRegistration> {
            client.clis.push(ClientEntry {
                descriptor: reg.descriptor,
                run: Box::new(reg.run),
            });
        }
        client
    }

    /// Register the CLI for one interface: its `FILE_DESCRIPTOR_SET` (used to match the driver's
    /// interface) and a `run(args, session, uuid)` dispatcher (the typed CLI's, boxed).
    pub fn cli<F>(mut self, descriptor: &'static [u8], run: F) -> Self
    where
        F: for<'a> Fn(
                &'a [String],
                &'a ClientSession,
                &'a str,
            ) -> Pin<Box<dyn std::future::Future<Output = i32> + 'a>>
            + 'static,
    {
        self.clis.push(ClientEntry {
            descriptor,
            run: Box::new(run),
        });
        self
    }

    /// Parse `<driver> <subcommand…>` (+ an optional `--interface <fqn>`), connect JUMPSTARTER_HOST,
    /// resolve the driver uuid, select the registered CLI, and dispatch. Builds its own runtime.
    pub fn run(self) -> std::process::ExitCode {
        use std::process::ExitCode;
        let runtime = match tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
        {
            Ok(rt) => rt,
            Err(e) => {
                eprintln!("jumpstarter: building the client runtime: {e}");
                return ExitCode::from(1);
            }
        };
        runtime.block_on(async move {
            // Strip `--interface <fqn>` from argv; the rest is `<driver> <subcommand…>`.
            let mut interface = None;
            let mut rest: Vec<String> = Vec::new();
            let mut argv = std::env::args().skip(1);
            while let Some(a) = argv.next() {
                if a == "--interface" {
                    interface = argv.next();
                } else {
                    rest.push(a);
                }
            }
            let Some(driver) = rest.first().cloned() else {
                eprintln!("usage: <driver> <subcommand> [args]");
                return ExitCode::from(2);
            };
            // Select the registered CLI BEFORE connecting, so an empty/ambiguous registry fails fast.
            let entry = match self.select(interface.as_deref()) {
                Ok(e) => e,
                Err(e) => {
                    eprintln!("{e}");
                    return ExitCode::from(1);
                }
            };
            let host = match std::env::var("JUMPSTARTER_HOST") {
                Ok(h) => h,
                Err(_) => {
                    eprintln!("JUMPSTARTER_HOST is not set (run inside a `jmp shell`)");
                    return ExitCode::from(1);
                }
            };
            let session = match ClientSession::connect(host).await {
                Ok(s) => s,
                Err(e) => {
                    eprintln!("connecting to the exporter: {e}");
                    return ExitCode::from(1);
                }
            };
            let uuid = match resolve_driver_uuid(&session, &driver).await {
                Ok(u) => u,
                Err(e) => {
                    eprintln!("resolving driver '{driver}': {e}");
                    return ExitCode::from(1);
                }
            };
            ExitCode::from((entry.run)(&rest[1..], &session, &uuid).await as u8)
        })
    }

    /// Pick the CLI: the only one registered, else the one whose interface matches `--interface`.
    fn select(&self, interface: Option<&str>) -> Result<&ClientEntry, String> {
        match (self.clis.as_slice(), interface) {
            ([], _) => Err("no clients registered in this binary".into()),
            ([only], _) => Ok(only),
            (many, Some(iface)) => many
                .iter()
                .find(|c| descriptor_interface(c.descriptor).as_deref() == Some(iface))
                .ok_or_else(|| format!("no registered client drives interface `{iface}`")),
            (_, None) => {
                Err("this binary registers multiple clients; pass `--interface <fqn>`".into())
            }
        }
    }
}

/// What a `#[client_cli]` registration's `run` dispatches: the typed CLI's `run(args, session, uuid)`.
pub type ClientRunFn = for<'a> fn(
    &'a [String],
    &'a ClientSession,
    &'a str,
) -> Pin<Box<dyn std::future::Future<Output = i32> + 'a>>;

/// One client CLI registered by `#[client_cli]`, collected at link time by [`Client::from_inventory`].
pub struct ClientRegistration {
    pub descriptor: &'static [u8],
    pub run: ClientRunFn,
}

inventory::collect!(ClientRegistration);

/// Generate the client binary's whole `fn main` from the crate's `#[client_cli]` registrations:
/// `jumpstarter_driver_runtime::client_main!();` is the entire `src/client.rs`. (The crate's lib must
/// be linked into the bin — `use <crate> as _;` next to this when the bin references nothing else.)
#[macro_export]
macro_rules! client_main {
    () => {
        fn main() -> ::std::process::ExitCode {
            $crate::Client::from_inventory().run()
        }
    };
}
