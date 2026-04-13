//! Integration tests for the generated ExampleBoardDevice wrapper.
//!
//! Run with:
//!   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
//!     -- cargo test -p jumpstarter-example-board-tests -- --ignored --test-threads=1

use jumpstarter_testing::jumpstarter_test;
use jumpstarter_example_board::devices::example_board::ExampleBoardDevice;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

// -- Power control --

#[jumpstarter_test]
async fn test_power_on(mut device: ExampleBoardDevice<'_>) {
    device.power.on().await.unwrap();
}

#[jumpstarter_test]
async fn test_power_off(mut device: ExampleBoardDevice<'_>) {
    device.power.off().await.unwrap();
}

#[jumpstarter_test]
async fn test_read_power_measurements(mut device: ExampleBoardDevice<'_>) {
    let mut stream = device.power.read().await.unwrap();
    let reading = stream.message().await.unwrap().expect("expected at least one reading");
    assert!(reading.voltage >= 0.0);
    assert!(reading.current >= 0.0);
}

// -- Storage mux --

#[jumpstarter_test]
async fn test_storage_switch_to_host(mut device: ExampleBoardDevice<'_>) {
    device.storage.host().await.unwrap();
}

#[jumpstarter_test]
async fn test_storage_switch_to_dut(mut device: ExampleBoardDevice<'_>) {
    device.storage.dut().await.unwrap();
}

#[jumpstarter_test]
async fn test_storage_off(mut device: ExampleBoardDevice<'_>) {
    device.storage.off().await.unwrap();
}

// -- Network echo --

#[jumpstarter_test]
async fn test_network_echo(mut device: ExampleBoardDevice<'_>) {
    let network = device.network.as_mut().expect("expected network driver in this exporter");
    let handle = network.connect_tcp().await.unwrap();

    let mut tcp = tokio::net::TcpStream::connect(handle.local_addr()).await.unwrap();
    tcp.write_all(b"hello").await.unwrap();
    let mut buf = [0u8; 5];
    tcp.read_exact(&mut buf).await.unwrap();
    assert_eq!(&buf, b"hello");
}
