//! Example **custom client** — the client-side analog of subclassing Python's `DriverClient`.
//!
//! [`CyclingPowerClient`] wraps the codegen-generated [`crate::PowerClient`] and adds a client-side
//! convenience method ([`cycle`](CyclingPowerClient::cycle)) that the interface itself doesn't have —
//! "custom interfaces on the client side". [`PowerCli`] is its `j` CLI (`on`/`off`/`cycle`), driving
//! the **typed** client over native gRPC (not the JSON `driver_call` path). Native `j` dispatches to
//! [`PowerCli::run`] for a driver advertising `rust:powercli` ([`PowerCli::CLIENT_CLASS`]).

use std::time::Duration;

use clap::Parser;
use jumpstarter_core::error::DriverCallError;
use jumpstarter_core::ClientSession;

use crate::PowerClient;

/// A custom client composing the generated [`PowerClient`] with extra convenience methods. (Rust has
/// no inheritance, so a custom client *wraps* the generated one and delegates — the idiom matching a
/// Python `DriverClient` subclass / a Kotlin `open class` subclass.)
pub struct CyclingPowerClient<'a> {
    inner: PowerClient<'a>,
}

impl<'a> CyclingPowerClient<'a> {
    /// Wrap a generated [`PowerClient`].
    pub fn new(inner: PowerClient<'a>) -> Self {
        Self { inner }
    }

    /// Build directly from a session + known driver uuid (what the `j` registry hands us).
    pub fn with_uuid(session: &'a ClientSession, uuid: String) -> Self {
        Self::new(PowerClient::with_uuid(session, uuid))
    }

    /// Delegate: power on.
    pub async fn on(&self) -> Result<(), DriverCallError> {
        self.inner.on().await
    }

    /// Delegate: power off.
    pub async fn off(&self) -> Result<(), DriverCallError> {
        self.inner.off().await
    }

    /// Delegate: read the streamed power readings to completion.
    pub async fn read(&self) -> Result<Vec<crate::proto::PowerReading>, DriverCallError> {
        use tokio_stream::StreamExt as _;
        let mut stream = Box::pin(self.inner.read().await?);
        let mut readings = Vec::new();
        while let Some(item) = stream.next().await {
            readings.push(item?);
        }
        Ok(readings)
    }

    /// Custom client-side method (NOT an interface RPC): power-cycle — off, wait, on.
    pub async fn cycle(&self, wait: Duration) -> Result<(), DriverCallError> {
        self.off().await?;
        tokio::time::sleep(wait).await;
        self.on().await?;
        Ok(())
    }
}

/// The `j` CLI for the custom power client: `j <driver> {on | off | cycle [--wait N]}`.
///
/// `#[client_cli]` auto-registers this CLI, so the client binary's whole `src/client.rs` is
/// `client_main!()` — the mirror of the host `#[driver]` (and the JVM `@JumpstarterClientCli`).
#[jumpstarter_driver_runtime::client_cli]
#[derive(Parser)]
#[command(name = "")]
pub enum PowerCli {
    /// Power on.
    On,
    /// Power off.
    Off,
    /// Read the power state (prints each voltage/current reading).
    Read,
    /// Power cycle: off, wait, on.
    Cycle {
        /// Seconds to wait between off and on.
        #[arg(long, default_value_t = 2)]
        wait: u64,
    },
}

impl PowerCli {
    /// The `jumpstarter.dev/client` label this custom client is registered under (native `j` routes
    /// a driver advertising this to [`PowerCli::run`]).
    pub const CLIENT_CLASS: &'static str = "rust:powercli";

    /// Dispatch a `j <driver> <subcmd>` invocation — the exact signature native `j`'s native-client
    /// registry calls (`run(args, session, uuid)`). Drives the typed [`CyclingPowerClient`].
    pub async fn run(args: &[String], session: &ClientSession, uuid: &str) -> i32 {
        // `j` strips the driver name; clap wants a leading argv[0], so prepend an empty one.
        let parsed = match PowerCli::try_parse_from(
            std::iter::once(String::new()).chain(args.iter().cloned()),
        ) {
            Ok(cli) => cli,
            Err(e) => {
                let _ = e.print();
                return 2;
            }
        };
        let power = CyclingPowerClient::with_uuid(session, uuid.to_string());
        let result = match parsed {
            PowerCli::On => power.on().await,
            PowerCli::Off => power.off().await,
            PowerCli::Cycle { wait } => power.cycle(Duration::from_secs(wait)).await,
            PowerCli::Read => match power.read().await {
                Ok(readings) => {
                    for r in readings {
                        println!("voltage={} current={}", r.voltage, r.current);
                    }
                    Ok(())
                }
                Err(e) => Err(e),
            },
        };
        match result {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("error: {e}");
                1
            }
        }
    }
}

#[cfg(test)]
mod tests {
    //! Drive the custom client + its CLI through the local harness — `cycle` (a client-side method,
    //! off+on) leaves the driver powered on, and `PowerCli::run(["cycle"])` does the same via the CLI
    //! dispatch path, all over the real client → exporter → SHM → tonic-service loop.

    use jumpstarter_driver_harness::serve;
    use tokio_stream::StreamExt as _;

    use super::*;
    use crate::{proto, MockPower};

    async fn powered_on(client: &PowerClient<'_>) -> bool {
        let mut stream = Box::pin(client.read().await.expect("read"));
        let first = stream.next().await.expect("a reading").expect("decoded");
        first.voltage > 0.0
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn custom_cycle_and_cli_through_harness() {
        let harness = serve(
            "power",
            PowerCli::CLIENT_CLASS,
            proto::FILE_DESCRIPTOR_SET,
            proto::power_interface_server::PowerInterfaceServer::new(MockPower::default()),
        )
        .await;
        let typed = PowerClient::new(harness.session(), "power").await.unwrap();
        let uuid = typed.uuid().to_string();

        // The custom client-side method: cycle leaves it ON.
        let custom = CyclingPowerClient::with_uuid(harness.session(), uuid.clone());
        custom.off().await.unwrap();
        assert!(!powered_on(&typed).await, "off before cycle");
        custom.cycle(Duration::from_millis(10)).await.unwrap();
        assert!(powered_on(&typed).await, "cycle ends powered on");

        // The CLI dispatch path: `j <driver> cycle --wait 0` reaches the same typed client.
        custom.off().await.unwrap();
        assert_eq!(
            PowerCli::run(&["cycle".into(), "--wait".into(), "0".into()], harness.session(), &uuid).await,
            0,
        );
        assert!(powered_on(&typed).await, "CLI cycle ends powered on");
    }
}
