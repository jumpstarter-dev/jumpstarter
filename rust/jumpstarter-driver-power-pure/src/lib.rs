//! Pure DRIVER power example — the driver half of the binary-purity pair.
//!
//! This crate is the driver-only twin of `jumpstarter-driver-power-example`: the author writes
//! **only** the native `tonic` service impl (`impl PowerInterface for MockPower`) plus the
//! [`MockPower`] state, and the crate links the driver side ONLY (`jumpstarter-driver`) — never any
//! client crate. Its `build.rs` uses `jumpstarter_codegen::build::driver_interface`, so the generated
//! `proto` module carries the server trait + `FILE_DESCRIPTOR_SET` but NO typed client.
//!
//! The client half lives in `jumpstarter-driver-power-pure-client` (deps `jumpstarter-client` only).
//! The round-trip test lives there with this crate as a *dev-dependency*, so the client binary never
//! links the driver runtime — proving end-to-end binary purity. See
//! `cargo tree -p jumpstarter-driver-power-pure -e normal`: no `jumpstarter-client` appears.

// Everything generated at build time (NOT committed), pulled in with one macro: `pub mod proto` (the
// stock tonic server trait + prost messages + `FILE_DESCRIPTOR_SET`). Driver-only mode emits no typed
// client. The only committed code in this crate is the author's [`MockPower`] driver impl below.
jumpstarter_driver::interface!("jumpstarter.interfaces.power.v1");

use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};

use proto::power_interface_server::PowerInterface;
use proto::PowerReading;
use tonic::{Request, Response, Status};

/// The `jumpstarter.dev/client` class advertised for the mock power driver (the existing Python power
/// client; the native `PowerClient` in the pure-client crate drives it identically over the descriptor).
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
    pub const ON_VOLTAGE: f64 = 5.0;
    /// The nominal on-current (amps) reported while powered.
    pub const ON_CURRENT: f64 = 2.0;
    /// How many readings `read` streams.
    pub const READINGS: usize = 3;

    /// Whether the mock is currently powered on (for assertions in tests).
    pub fn is_on(&self) -> bool {
        self.powered.load(Ordering::SeqCst) != 0
    }
}

// `#[driver]` auto-registers MockPower (and sets its default client), so the host binary's whole
// `src/main.rs` is `host_main!()` — the Rust analog of the JVM `@JumpstarterDriver` annotation.
#[jumpstarter_driver::driver(client = "jumpstarter_driver_power.client.PowerClient")]
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
