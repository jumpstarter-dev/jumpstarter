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

/// Look up a native driver by the name after the `rust:` prefix in its `type:`. A real driver
/// crate would register here (or via `inventory`); the example keeps a small match.
pub fn make_driver(
    name: &str,
    _config: &serde_json::Map<String, Json>,
) -> Option<Arc<dyn Driver>> {
    match name {
        "power" => Some(Arc::new(MockPower)),
        _ => None,
    }
}
