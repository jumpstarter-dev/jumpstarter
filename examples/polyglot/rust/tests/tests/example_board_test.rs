//! Integration tests for the generated ExampleBoardDevice wrapper.
//!
//! Run with:
//!   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
//!     -- cargo test -p jumpstarter-example-board-tests -- --ignored
//!
//! Tests are marked `#[ignore]` because they require a running exporter via `jmp shell`.

use jumpstarter_client::ExporterSession;
use jumpstarter_example_board::devices::example_board::ExampleBoardDevice;

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
    if let Some(ref _network) = device.network {
        // network.connect() opens a bidi byte stream
    }
}
