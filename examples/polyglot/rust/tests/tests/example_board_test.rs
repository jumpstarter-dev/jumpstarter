//! Integration tests for the generated ExampleBoardDevice wrapper.
//!
//! Run with:
//!   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
//!     -- cargo test -p jumpstarter-example-board-tests -- --ignored --test-threads=1
//!
//! Tests share a single tokio runtime and ExporterSession.
//! Each `#[tokio::test]` creates its own runtime which kills tonic channels,
//! so we use `#[test]` with a shared runtime + block_on instead.

use std::sync::LazyLock;

use jumpstarter_client::ExporterSession;
use jumpstarter_example_board::devices::example_board::ExampleBoardDevice;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::runtime::Runtime;

/// Shared tokio runtime + session — all tests use this single runtime
/// so the tonic gRPC channel stays alive across tests.
static SHARED: LazyLock<(Runtime, ExporterSession)> = LazyLock::new(|| {
    let rt = Runtime::new().expect("failed to create tokio runtime");
    let session = rt.block_on(ExporterSession::from_env())
        .expect("JUMPSTARTER_HOST must be set — run inside jmp shell");
    (rt, session)
});

fn rt() -> &'static Runtime { &SHARED.0 }
fn session() -> &'static ExporterSession { &SHARED.1 }

// -- Power control --

#[test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
fn test_power_on() {
    rt().block_on(async {
        let mut device = ExampleBoardDevice::new(session());
        device.power.on().await.unwrap();
    });
}

#[test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
fn test_power_off() {
    rt().block_on(async {
        let mut device = ExampleBoardDevice::new(session());
        device.power.off().await.unwrap();
    });
}

#[test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
fn test_read_power_measurements() {
    rt().block_on(async {
        let mut device = ExampleBoardDevice::new(session());
        let mut stream = device.power.read().await.unwrap();
        let reading = stream.message().await.unwrap().expect("expected at least one reading");
        assert!(reading.voltage >= 0.0);
        assert!(reading.current >= 0.0);
    });
}

// -- Storage mux --

#[test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
fn test_storage_switch_to_host() {
    rt().block_on(async {
        let mut device = ExampleBoardDevice::new(session());
        device.storage.host().await.unwrap();
    });
}

#[test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
fn test_storage_switch_to_dut() {
    rt().block_on(async {
        let mut device = ExampleBoardDevice::new(session());
        device.storage.dut().await.unwrap();
    });
}

#[test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
fn test_storage_off() {
    rt().block_on(async {
        let mut device = ExampleBoardDevice::new(session());
        device.storage.off().await.unwrap();
    });
}

// -- Network echo --

#[test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
fn test_network_echo() {
    rt().block_on(async {
        let mut device = ExampleBoardDevice::new(session());
        let network = device.network.as_mut().expect("expected network driver in this exporter");
        let handle = network.connect_tcp().await.unwrap();

        let mut tcp = tokio::net::TcpStream::connect(handle.local_addr()).await.unwrap();
        tcp.write_all(b"hello").await.unwrap();
        let mut buf = [0u8; 5];
        tcp.read_exact(&mut buf).await.unwrap();
        assert_eq!(&buf, b"hello");
    });
}
