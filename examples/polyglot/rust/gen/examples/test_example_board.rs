//! End-to-end test of the Rust typed client against a real exporter.
//!
//! Run with:
//!   cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml \
//!     -- cargo run --example test_example_board --manifest-path ../examples/polyglot/rust/gen/Cargo.toml

use jumpstarter_client::ExporterSession;
use jumpstarter_example_board::devices::example_board::ExampleBoardDevice;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let session = ExporterSession::from_env().await?;
    println!("Connected to exporter");

    let report = session.report();
    let drivers: Vec<_> = report.instances().iter()
        .filter_map(|d| d.labels().get("jumpstarter.dev/name").map(|n| n.to_owned()))
        .collect();
    println!("Discovered drivers: {:?}", drivers);

    let mut device = ExampleBoardDevice::new(&session);

    // -- Power tests --
    println!("\n--- Power Tests ---");

    device.power.on().await?;
    println!("power.on(): OK");

    device.power.off().await?;
    println!("power.off(): OK");

    let mut stream = device.power.read().await?;
    if let Some(reading) = stream.message().await? {
        println!("power.read(): voltage={}, current={}", reading.voltage, reading.current);
    }

    // -- Storage Mux tests --
    println!("\n--- Storage Mux Tests ---");

    device.storage.host().await?;
    println!("storage.host(): OK");

    device.storage.dut().await?;
    println!("storage.dut(): OK");

    device.storage.off().await?;
    println!("storage.off(): OK");

    // -- Optional Network --
    println!("\n--- Optional Network ---");
    match &device.network {
        Some(_) => println!("network driver available"),
        None => println!("network driver is None (optional)"),
    }

    println!("\n=== All Rust tests PASSED! ===");
    Ok(())
}
