//! End-to-end smoke test of the Rust typed client against a real exporter.
//!
//! Run with:
//!   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
//!     -- cargo run -p jumpstarter-example-board-tests --example test_example_board

use jumpstarter_client::ExporterSession;
use jumpstarter_example_board::devices::example_board::ExampleBoardDevice;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let session = ExporterSession::from_env().await?;
    println!("Connected to exporter");

    let drivers: Vec<_> = session.report().instances().iter()
        .filter_map(|d| d.labels().get("jumpstarter.dev/name").cloned())
        .collect();
    println!("Discovered drivers: {:?}", drivers);

    let mut device = ExampleBoardDevice::new(&session);

    println!("\n--- Power Tests ---");
    device.power.on().await?;
    println!("power.on(): OK");
    device.power.off().await?;
    println!("power.off(): OK");

    let mut stream = device.power.read().await?;
    if let Some(reading) = stream.message().await? {
        println!("power.read(): voltage={}, current={}", reading.voltage, reading.current);
    }

    println!("\n--- Storage Mux Tests ---");
    device.storage.host().await?;
    println!("storage.host(): OK");
    device.storage.dut().await?;
    println!("storage.dut(): OK");
    device.storage.off().await?;
    println!("storage.off(): OK");

    println!("\n--- Optional Network ---");
    match &device.network {
        Some(_) => println!("network driver available"),
        None => println!("network driver is None (optional)"),
    }

    println!("\n=== All Rust tests PASSED! ===");
    Ok(())
}
