//! Live end-to-end test of the transport host: the Rust client acquires a lease,
//! serves the `JUMPSTARTER_HOST` Unix socket, and a real Python `j` driver call is
//! tunneled through it to the exporter.
//!
//! Gated on `JMP_RUST_TEST_CONFIG` + `JMP_RUST_TEST_SELECTOR` + `JMP_RUST_TEST_JMP`
//! (path to the venv `j`). Run against the e2e cluster:
//!
//! ```sh
//! JMP_RUST_TEST_CONFIG=~/.config/jumpstarter/clients/test-rust-client.yaml \
//! JMP_RUST_TEST_SELECTOR='example.com/board=rust' \
//! JMP_RUST_TEST_JMP=$PWD/python/.venv/bin/j \
//!   cargo test -p jumpstarter-lease --test live_transport -- --nocapture
//! ```

use std::time::Duration;

use jumpstarter_config::{ClientConfig, YamlConfig};
use jumpstarter_lease::{
    acquire, transport, ControllerClient, CreateLeaseParams, LeaseProvider, LeaseTiming,
};

/// Acquire a lease, serve the transport socket, run `j power on` through it.
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn driver_call_tunnels_through_rust_socket() {
    let (Ok(cfg_path), Ok(selector), Ok(jmp)) = (
        std::env::var("JMP_RUST_TEST_CONFIG"),
        std::env::var("JMP_RUST_TEST_SELECTOR"),
        std::env::var("JMP_RUST_TEST_JMP"),
    ) else {
        eprintln!("skip: set JMP_RUST_TEST_CONFIG, JMP_RUST_TEST_SELECTOR, JMP_RUST_TEST_JMP");
        return;
    };
    let cfg = ClientConfig::load(&cfg_path).expect("load config");

    let client = ControllerClient::connect(&cfg).await.expect("connect");

    // Acquire a lease for the live exporter.
    let acquired = acquire(
        &client,
        CreateLeaseParams {
            selector: Some(selector),
            duration: Duration::from_secs(180),
            ..Default::default()
        },
        None,
        Some(&cfg.metadata.name),
        LeaseTiming {
            poll_interval: Duration::from_secs(2),
            acquisition_timeout: Duration::from_secs(60),
        },
    )
    .await
    .expect("acquire lease");
    eprintln!("acquired lease {} on {}", acquired.name, acquired.exporter);

    // Serve the JUMPSTARTER_HOST socket (tunnels each connection via the router).
    let host = transport::serve_default(client.clone(), acquired.name.clone(), cfg.tls.clone())
        .await
        .expect("serve transport");
    let jumpstarter_host = host.jumpstarter_host();
    eprintln!("JUMPSTARTER_HOST={jumpstarter_host}");

    // Run a real Python `j power on` through the Rust-served socket.
    let output = tokio::process::Command::new(&jmp)
        .args(["power", "on"])
        .env("JUMPSTARTER_HOST", &jumpstarter_host)
        .env("JMP_DRIVERS_ALLOW", "UNSAFE")
        .output()
        .await
        .expect("spawn j");

    eprintln!("j exit: {:?}", output.status);
    eprintln!("j stdout:\n{}", String::from_utf8_lossy(&output.stdout));
    eprintln!("j stderr:\n{}", String::from_utf8_lossy(&output.stderr));

    // Release before asserting so the lease is freed either way.
    let _ = client.delete_lease(&acquired.name).await;
    drop(host);

    assert!(
        output.status.success(),
        "`j power on` through the Rust socket failed"
    );
}
