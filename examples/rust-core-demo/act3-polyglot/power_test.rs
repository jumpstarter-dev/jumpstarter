//! Act 3 of the rust-core demo, as a **native Rust test**: the typed, build-time-generated
//! [`PowerClient`] drives all three drivers of the polyglot exporter — a **Python**, a **Rust**,
//! and a **Kotlin** implementation of the same `PowerInterface` — through one lease. Together
//! with Act 1 (pytest) and Act 2 (JUnit/Kotlin) this completes the trilogy: the same interface,
//! the same transport, a native test in every language.
//!
//! This file lives in `examples/rust-core-demo/act3-polyglot/` so demo readers see exactly what
//! runs; the crate `jumpstarter-driver-power-pure-client` compiles it as an integration test via
//! a `#[path]` shim (`tests/rust_core_demo.rs`) — one source of truth, no copy to drift.
//!
//! Gated on `JUMPSTARTER_HOST` (set by `jmp shell`); skips when run outside a lease. Run it via
//! `run.sh` next to this file, or by hand:
//!
//!   ./serve.sh                                                    # terminal A
//!   jmp shell --client demo-client --selector example.com/dut=polyglot -- \
//!       cargo test --manifest-path rust/Cargo.toml \
//!       -p jumpstarter-driver-power-pure-client \
//!       --test rust_core_demo -- --nocapture                     # terminal B

use jumpstarter_client::ClientSession;
use jumpstarter_driver_power_pure_client::PowerClient;

/// The three `export:` entries of `exporter.yaml`, one per language.
const DRIVERS: [(&str, &str); 3] = [
    ("pypower", "Python  (jumpstarter_driver_power.driver.MockPower)"),
    ("rustpower", "Rust    (rust:power via jmp-rust-host)"),
    ("jvmpower", "Kotlin  (jvm:dev.jumpstarter.examples.power.KotlinPowerDriver)"),
];

#[tokio::test(flavor = "multi_thread")]
async fn one_rust_client_drives_python_rust_and_kotlin_drivers() {
    // Under `jmp shell` this is the lease's local transport socket; without it, skip (the same
    // gating idea as the JVM act's @Tag("integration")).
    let Ok(host) = std::env::var("JUMPSTARTER_HOST") else {
        eprintln!("skipping: JUMPSTARTER_HOST not set — run under `jmp shell` (see run.sh)");
        return;
    };

    let session = ClientSession::connect(host)
        .await
        .expect("connect to the leased exporter");

    for (name, language) in DRIVERS {
        // The generated typed client, resolved by the driver's name label from GetReport — the
        // caller cannot tell (and does not care) which language serves the other end.
        let power = PowerClient::new(&session, name)
            .await
            .unwrap_or_else(|e| panic!("resolve `{name}` from the report: {e}"));

        power
            .on()
            .await
            .unwrap_or_else(|e| panic!("{name}: on() failed: {e}"));
        power
            .off()
            .await
            .unwrap_or_else(|e| panic!("{name}: off() failed: {e}"));

        println!("{name:9} on()+off() OK — {language}");
    }

    println!("one Rust client, three driver languages, one lease: all green");
}
