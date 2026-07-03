//! Example typed DEVICE consumer — the Rust sibling of `python/examples/exporter-device-example`.
//!
//! Everything typed here is generated at build time from the committed [`exporter.yaml`]
//! (the source of truth): `build.rs` is one `jumpstarter_codegen::build::exporter_device` call
//! that resolves each configured node's interface **proto-only** (the committed
//! `interfaces/registry` + `interfaces/proto` — no driver code is loaded), and the line below
//! pulls in the `proto` messages, the typed [`PowerClient`], and the [`ExampleRigDevice`] tree
//! wrapper. Nothing generated is committed.

jumpstarter_client::device!();

#[cfg(test)]
mod round_trip_tests {
    //! Serve the pure Rust power driver over the real SHM harness and drive it through the
    //! GENERATED device wrapper: `ExampleRigDevice::new(session)` resolves the `power` node by
    //! name path from one `GetReport`, and the typed client round-trips on/off/read.

    use jumpstarter_driver_harness::serve;
    use jumpstarter_driver_power_pure::{MockPower, POWER_CLIENT_CLASS};
    use tokio_stream::StreamExt as _;

    use super::*;

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn device_wrapper_round_trip_over_shm() {
        let harness = serve(
            "power", // the exporter.yaml entry name — what the device resolves by path
            POWER_CLIENT_CLASS,
            jumpstarter_driver_power_pure::proto::FILE_DESCRIPTOR_SET,
            jumpstarter_driver_power_pure::proto::power_interface_server::PowerInterfaceServer::new(
                MockPower::default(),
            ),
        )
        .await;

        let device = ExampleRigDevice::new(harness.session())
            .await
            .expect("bind the device tree from the report");

        // Off: zero-voltage readings prove the full loop (device → typed client → SHM → driver).
        let mut stream = Box::pin(device.power.read().await.expect("read stream"));
        while let Some(reading) = stream.next().await {
            assert_eq!(reading.expect("decoded reading").voltage, 0.0);
        }

        // On → powered readings.
        device.power.on().await.expect("on");
        let mut stream = Box::pin(device.power.read().await.expect("read stream"));
        let first = stream.next().await.expect("a reading").expect("decoded");
        assert!(first.voltage > 0.0, "powered-on voltage, got {}", first.voltage);

        device.power.off().await.expect("off");
    }
}
