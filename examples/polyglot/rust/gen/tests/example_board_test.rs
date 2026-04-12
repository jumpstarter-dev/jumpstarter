//! Example tests for the generated ExampleBoardDevice wrapper.
//!
//! Run with:
//!   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
//!     -- cargo test --manifest-path ../examples/polyglot/rust/gen/Cargo.toml
//!
//! Or as an example binary (no test harness needed):
//!   cargo run --example test_example_board

use jumpstarter_client::ExporterSession;
use jumpstarter_example_board::devices::example_board::ExampleBoardDevice;

// Integration tests require jmp shell — marked as ignored by default.
// Run with: cargo test -- --ignored

#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_power_on() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);
    device.power.on().await.unwrap();
}

#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_power_off() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);
    device.power.off().await.unwrap();
}

#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_storage_mux() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);
    device.storage.host().await.unwrap();
    device.storage.dut().await.unwrap();
    device.storage.off().await.unwrap();
}

#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_optional_network() {
    let session = ExporterSession::from_env().await.unwrap();
    let device = ExampleBoardDevice::new(&session);
    // network is optional — may be None
    if let Some(ref _network) = device.network {
        // network.connect() opens a bidi byte stream
    }
}
