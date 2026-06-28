//! The Jumpstarter driver-CLIENT library + author-facing entrypoint.
//!
//! This crate is the CLIENT side of the framework: it consumes drivers over the transport
//! ([`ClientSession`] — the driver-call surface) and drives the controller/lease lifecycle
//! programmatically ([`ControllerSession`]/[`LeaseTransport`]), but it never *serves* a driver.
//! It also owns the author-facing client-CLI entrypoint — [`Client`]/[`client_cli`]/[`client_main!`]
//! — the client-side mirror of the host `Host`/`#[driver]`/`host_main!` (which live in
//! `jumpstarter-driver`).
//!
//! It deps only the neutral plumbing (`jumpstarter-codec`), the transport seam, compression, config,
//! and the lease primitives — NO `jumpstarter-driver-core`/`jumpstarter-exporter` — so a crate that
//! deps only `jumpstarter-client` is a *pure client*: its binary never compiles the driver-serving
//! runtime.

use std::pin::Pin;

pub mod client;
pub mod controller;

pub use client::resolve_driver_uuid;
pub use client::{
    ClientByteStream, ClientLogStream, ClientNativeStream, ClientResultStream, ClientSession,
};
pub use controller::{ControllerSession, LeaseTransport};

/// `#[client_cli]` — on a typed CLI: auto-registers it, so the client binary's `main` is just
/// [`client_main!`]. The client-side mirror of the host `#[driver]` (and the JVM `@JumpstarterClientCli`).
pub use jumpstarter_driver_macros::client_cli;

/// Re-exported so the `#[client_cli]`-generated registrations can reach `inventory::submit!`.
#[doc(hidden)]
pub use inventory;

/// The full name (`<package>.<Service>`) of the first service in a serialized `FileDescriptorSet` —
/// the interface a registered client drives. Used to select among a crate's clients at runtime.
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

// ── Client-CLI entrypoint ──────────────────────────────────────────────────────────────────────
// The client-side mirror of the host `Host`/`#[driver]`/`host_main!`, living here so a client crate
// imports ALL its entrypoint glue from `jumpstarter_client`. Built over this crate's client
// primitives (`ClientSession`, `resolve_driver_uuid`); `descriptor_interface` matches the driver.

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
/// the `--interface <fqn>` match). One client runs per process. The mirror of the host `Host`.
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
/// `jumpstarter_client::client_main!();` is the entire `src/client.rs`. (The crate's lib must
/// be linked into the bin — `use <crate> as _;` next to this when the bin references nothing else.)
#[macro_export]
macro_rules! client_main {
    () => {
        fn main() -> ::std::process::ExitCode {
            $crate::Client::from_inventory().run()
        }
    };
}
