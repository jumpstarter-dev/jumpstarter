//! Example native (Rust) drivers + a registry, demonstrating that a driver in a language other
//! than Python can be hosted in the same exporter, driven by the existing clients.
//!
//! `MockPower` advertises the **Python** `PowerClient`, so a client drives a native Rust driver
//! with `j <name> on` exactly as it would a Python `MockPower` — proving the per-driver hub is
//! language-neutral end to end.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use jumpstarter_core::driver::Driver;
use jumpstarter_core::error::DriverCallError;
use jumpstarter_core::ClientSession;
use jumpstarter_driver_macros::DriverClient;
use serde_json::Value as Json;

/// A native power driver advertising the Python `PowerClient`, so `j <name> on|off` works.
pub struct MockPower;

#[async_trait]
impl Driver for MockPower {
    fn client(&self) -> String {
        "jumpstarter_driver_power.client.PowerClient".to_string()
    }

    fn methods(&self) -> HashMap<String, String> {
        HashMap::from([
            ("on".to_string(), "turn the (mock) power on".to_string()),
            ("off".to_string(), "turn the (mock) power off".to_string()),
        ])
    }

    async fn call(&self, method: &str, _args: Vec<Json>) -> Result<Json, DriverCallError> {
        match method {
            // PowerClient.on()/off() expect no return value.
            "on" | "off" => Ok(Json::Null),
            other => Err(DriverCallError::Unimplemented(format!(
                "MockPower (rust) has no method {other}"
            ))),
        }
    }
}

/// A native power driver whose *client* is also native (`rust:powerclient`), so `j <name> on`
/// drives it with **no Python at all** — the client-side mirror of the polyglot story.
pub struct MockPowerNativeClient;

#[async_trait]
impl Driver for MockPowerNativeClient {
    fn client(&self) -> String {
        // A native (Rust) client class — `j` drives this in-process, no Python.
        "rust:powerclient".to_string()
    }

    fn methods(&self) -> HashMap<String, String> {
        HashMap::from([
            ("on".to_string(), "turn the (mock) power on".to_string()),
            ("off".to_string(), "turn the (mock) power off".to_string()),
        ])
    }

    async fn call(&self, method: &str, _args: Vec<Json>) -> Result<Json, DriverCallError> {
        match method {
            "on" | "off" => Ok(Json::Null),
            other => Err(DriverCallError::Unimplemented(format!(
                "MockPower (rust) has no method {other}"
            ))),
        }
    }
}

/// Look up a native driver by the name after the `rust:` prefix in its `type:`. A real driver
/// crate would register here (or via `inventory`); the example keeps a small match. `power`
/// advertises the Python `PowerClient`; `powerrs` advertises the native `rust:powerclient`.
pub fn make_driver(
    name: &str,
    _config: &serde_json::Map<String, Json>,
) -> Option<Arc<dyn Driver>> {
    match name {
        "power" => Some(Arc::new(MockPower)),
        "powerrs" => Some(Arc::new(MockPowerNativeClient)),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// Native (Rust) client — the client-side analog, via clap + #[derive(DriverClient)].
// ---------------------------------------------------------------------------

/// The native client for `rust:powerclient` (the `powerrs` driver). A clap CLI whose subcommands
/// are driver calls: `j <driver> on` → `driver_call("on")`. The `DriverClient` derive writes the
/// dispatch + `run`; the author only declares the command surface — the Rust analog of a Python
/// `DriverClient`'s click group.
#[derive(clap::Parser, DriverClient)]
#[command(name = "")]
#[client(class = "rust:powerclient")]
pub enum PowerClient {
    /// Turn the power on.
    On,
    /// Turn the power off.
    Off,
}

/// The native-client registry native `j` consults: run the native (Rust) client for `class` if one
/// is registered here, else `None` (the driver's client is a Python client, handled elsewhere).
pub async fn run_client(
    class: &str,
    args: &[String],
    session: &ClientSession,
    uuid: &str,
) -> Option<i32> {
    match class {
        PowerClient::CLIENT_CLASS => Some(PowerClient::run(args, session, uuid).await),
        _ => None,
    }
}
