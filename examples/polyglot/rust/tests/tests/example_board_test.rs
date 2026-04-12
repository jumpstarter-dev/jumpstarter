//! Integration tests for the generated ExampleBoardDevice wrapper.
//!
//! Run with:
//!   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
//!     -- cargo test -p jumpstarter-example-board-tests -- --ignored

use jumpstarter_client::ExporterSession;
use jumpstarter_example_board::devices::example_board::ExampleBoardDevice;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

// -- Power control --

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
async fn test_read_power_measurements() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);
    let mut stream = device.power.read().await.unwrap();
    let reading = stream.message().await.unwrap().expect("expected at least one reading");
    assert!(reading.voltage >= 0.0);
    assert!(reading.current >= 0.0);
}

// -- Storage mux --

#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_storage_switch_to_host() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);
    device.storage.host().await.unwrap();
}

#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_storage_switch_to_dut() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);
    device.storage.dut().await.unwrap();
}

#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_storage_off() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);
    device.storage.off().await.unwrap();
}

// -- Network echo --

#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_network_echo() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);
    let network = device.network.as_mut().expect("expected network driver in this exporter");
    let handle = network.connect_tcp().await.unwrap();

    let mut tcp = tokio::net::TcpStream::connect(handle.local_addr()).await.unwrap();
    tcp.write_all(b"hello").await.unwrap();
    let mut buf = [0u8; 5];
    tcp.read_exact(&mut buf).await.unwrap();
    assert_eq!(&buf, b"hello");
}
