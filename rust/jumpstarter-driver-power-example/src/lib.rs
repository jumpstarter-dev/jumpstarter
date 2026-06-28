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

pub mod custom_client;

// Everything generated at build time (NOT committed), pulled in with one macro: `pub mod proto` (the
// stock tonic service trait + prost messages + `FILE_DESCRIPTOR_SET`), the typed [`PowerClient`], and
// the `power_host!`/`power_client!` entrypoint macros. The only committed code in this crate is the
// author's [`MockPower`] driver impl below.
jumpstarter_driver_runtime::interface!();

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

// `#[driver]` auto-registers MockPower (and sets its default client), so the host binary's whole
// `src/main.rs` is `host_main!()` — the Rust analog of the JVM `@JumpstarterDriver` annotation.
#[jumpstarter_driver_runtime::driver(client = "jumpstarter_driver_power.client.PowerClient")]
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
    //! `MockPower` (authored as `impl PowerInterface`) is served and driven by the **generated**
    //! `PowerClient` through the full `client → exporter → SHM → tonic service` loop — `on`/`off`
    //! (unary) and `read` (server-streaming) reach the driver over the real transport. The whole
    //! fixture is now [`jumpstarter_driver_harness::serve`]; the test is just drive + assert.

    use jumpstarter_driver_harness::serve;
    use tokio_stream::StreamExt as _;

    use super::*;

    /// Stream a `read()` call to completion, collecting the decoded readings.
    async fn collect_readings(
        stream: impl tokio_stream::Stream<Item = Result<proto::PowerReading, jumpstarter_codec::error::DriverCallError>>,
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
        // Serve the driver over the real SHM+UDS transport and connect a client — one call.
        let harness = serve(
            "power",
            POWER_CLIENT_CLASS,
            proto::FILE_DESCRIPTOR_SET,
            proto::power_interface_server::PowerInterfaceServer::new(MockPower::default()),
        )
        .await;

        // The GENERATED client, resolving the driver uuid from GetReport by the name label.
        let client = PowerClient::new(harness.session(), "power")
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
    }
}
