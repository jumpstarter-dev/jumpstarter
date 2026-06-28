//! Pure CLIENT power example — the client half of the binary-purity pair.
//!
//! This crate is the client-only twin of `jumpstarter-driver-power-example`: it carries the typed
//! [`PowerClient`] (generated at build time by `jumpstarter_codegen::build::client_interface` — the
//! messages + descriptor + typed client, NO server trait) and a [`PowerCli`] (`#[client_cli]`) `j`
//! CLI driving it, and it links the client side ONLY (`jumpstarter-client` + the neutral
//! `jumpstarter-codec`) — never any driver crate. Its binary's `main` is one `client_main!()`.
//!
//! The driver half lives in `jumpstarter-driver-power-pure` (deps `jumpstarter-driver` only). The
//! round-trip test below serves THAT driver via the harness and drives it with this crate's typed
//! client — but the driver crate + harness are *dev-dependencies*, so the shipped client binary never
//! links the driver runtime. See `cargo tree -p jumpstarter-driver-power-pure-client -e normal`: no
//! `jumpstarter-driver`/`jumpstarter-exporter` appears.

use std::time::Duration;

use clap::Parser;
use jumpstarter_client::ClientSession;
use jumpstarter_codec::error::DriverCallError;

// Everything generated at build time (NOT committed), pulled in with one macro: `pub mod proto` (the
// prost messages + `FILE_DESCRIPTOR_SET`) and the typed [`PowerClient`]. Client-only mode emits no
// server trait. The only committed client code in this crate is [`CyclingPowerClient`]/[`PowerCli`].
jumpstarter_client::interface!("jumpstarter.interfaces.power.v1");

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

/// The `j` CLI for the pure power client: `j <driver> {on | off | read | cycle [--wait N]}`.
///
/// `#[client_cli]` auto-registers this CLI, so the client binary's whole `src/main.rs` is
/// `client_main!()` — the mirror of the host `#[driver]` (and the JVM `@JumpstarterClientCli`).
#[jumpstarter_client::client_cli]
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
    /// The `jumpstarter.dev/client` label this custom client is registered under (native `j` routes a
    /// driver advertising this to [`PowerCli::run`]).
    pub const CLIENT_CLASS: &'static str = "rust:powercli-pure";

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
mod round_trip_tests {
    //! End-to-end purity proof: the PURE DRIVER crate (`jumpstarter-driver-power-pure`, a
    //! dev-dependency) is served over the real SHM+UDS transport via the harness, and THIS crate's
    //! generated [`PowerClient`] + custom [`CyclingPowerClient`]/[`PowerCli`] drive it through the
    //! full `client → exporter → SHM → tonic service` loop. The driver crate + harness are
    //! dev-dependencies, so they never enter the shipped client binary.

    use jumpstarter_driver_harness::serve;
    use jumpstarter_driver_power_pure::{MockPower, POWER_CLIENT_CLASS};

    use super::*;
    use crate::proto;

    async fn powered_on(client: &PowerClient<'_>) -> bool {
        use tokio_stream::StreamExt as _;
        let mut stream = Box::pin(client.read().await.expect("read"));
        let first = stream.next().await.expect("a reading").expect("decoded");
        first.voltage > 0.0
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn on_off_read_round_trip_pure_pair_over_shm() {
        // Serve the PURE DRIVER over the real SHM+UDS transport and connect a client — one call.
        // The descriptor is THIS crate's own client-only `FILE_DESCRIPTOR_SET`, and the served
        // service comes from the dev-dep driver crate (whose driver-only build emits the server type).
        let harness = serve(
            "power",
            POWER_CLIENT_CLASS,
            proto::FILE_DESCRIPTOR_SET,
            jumpstarter_driver_power_pure::proto::power_interface_server::PowerInterfaceServer::new(
                MockPower::default(),
            ),
        )
        .await;

        // The GENERATED client, resolving the driver uuid from GetReport by the name label.
        let client = PowerClient::new(harness.session(), "power")
            .await
            .expect("resolve power client from report");

        // Initially off: a Read streams zero-voltage readings (proving the call reached the driver
        // through client → exporter/demux → SHM → tonic service, all the way back).
        assert!(!powered_on(&client).await, "off -> 0 V");

        // on() (unary) flips the driver; Read now streams powered readings.
        client.on().await.expect("on() unary");
        assert!(powered_on(&client).await, "powered-on voltage must be > 0");

        // The custom client-side `cycle` (off+on) leaves the driver powered on, and the CLI dispatch
        // path reaches the same typed client.
        let uuid = client.uuid().to_string();
        let custom = CyclingPowerClient::with_uuid(harness.session(), uuid.clone());
        custom.off().await.expect("off()");
        assert!(!powered_on(&client).await, "off before cycle");
        custom.cycle(Duration::from_millis(10)).await.expect("cycle");
        assert!(powered_on(&client).await, "cycle ends powered on");

        custom.off().await.expect("off()");
        assert_eq!(
            PowerCli::run(&["cycle".into(), "--wait".into(), "0".into()], harness.session(), &uuid).await,
            0,
        );
        assert!(powered_on(&client).await, "CLI cycle ends powered on");
    }
}
