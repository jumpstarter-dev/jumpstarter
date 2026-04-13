//! Integration tests for the generated ExampleBoardDevice wrapper.
//!
//! Run with:
//!   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
//!     -- cargo test -p jumpstarter-example-board-tests -- --ignored --test-threads=1
//!
//! All tests share a single ExporterSession and run sequentially.

use jumpstarter_client::ExporterSession;
use jumpstarter_example_board::devices::example_board::ExampleBoardDevice;

/// Run all device tests in a single test function to guarantee
/// shared session and sequential execution.
#[tokio::test]
#[ignore = "requires jmp shell (JUMPSTARTER_HOST)"]
async fn test_example_board() {
    let session = ExporterSession::from_env().await.unwrap();
    let mut device = ExampleBoardDevice::new(&session);

    // -- Power control --
    device.power.on().await.unwrap();
    device.power.off().await.unwrap();

    let mut stream = device.power.read().await.unwrap();
    let reading = stream.message().await.unwrap().expect("expected at least one reading");
    assert!(reading.voltage >= 0.0);
    assert!(reading.current >= 0.0);
    drop(stream);

    // -- Storage mux --
    device.storage.host().await.unwrap();
    device.storage.dut().await.unwrap();
    device.storage.off().await.unwrap();

    // -- Network echo --
    // TODO: The native gRPC bidi stream hangs due to a tonic deadlock.
    // tonic's stub.connect(outbound_stream).await blocks waiting for server
    // response headers, but the Python servicer doesn't send headers until it
    // receives inbound data — creating a deadlock. Needs restructuring to
    // produce outbound data before awaiting the response.
    //
    // let network = device.network.as_mut().expect("expected network driver");
    // let handle = network.connect_tcp().await.unwrap();
    // let mut tcp = tokio::net::TcpStream::connect(handle.local_addr()).await.unwrap();
    // use tokio::io::{AsyncReadExt, AsyncWriteExt};
    // tcp.write_all(b"hello").await.unwrap();
    // let mut buf = [0u8; 5];
    // tcp.read_exact(&mut buf).await.unwrap();
    // assert_eq!(&buf, b"hello");
}
