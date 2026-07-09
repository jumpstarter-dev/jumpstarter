//! Live integration tests against a running controller.
//!
//! Gated on `JMP_RUST_TEST_CONFIG` (path to a client config). Without it these
//! tests no-op, keeping the default suite hermetic. Run against the e2e cluster:
//!
//! ```sh
//! JMP_RUST_TEST_CONFIG=~/.config/jumpstarter/clients/test-rust-client.yaml \
//! JMP_RUST_TEST_SELECTOR='example.com/board=rust' \
//!   cargo test -p jumpstarter-lease --test live_controller -- --nocapture
//! ```

use std::time::Duration;

use jumpstarter_config::{ClientConfig, YamlConfig};
use jumpstarter_lease::{
    acquire, ClientError, ControllerClient, CreateLeaseParams, LeaseProvider, LeaseTiming,
};

fn load_config() -> Option<ClientConfig> {
    let path = std::env::var("JMP_RUST_TEST_CONFIG").ok()?;
    Some(ClientConfig::load(&path).unwrap_or_else(|e| panic!("load {path}: {e}")))
}

/// Proves TLS + auth + the controller round-trip: a `GetLease` for a non-existent
/// lease must come back as a gRPC status (NOT_FOUND), not a transport error.
#[tokio::test]
async fn live_connectivity_probe() {
    let Some(cfg) = load_config() else {
        eprintln!("skip live_connectivity_probe: JMP_RUST_TEST_CONFIG not set");
        return;
    };
    let client = ControllerClient::connect(&cfg)
        .await
        .expect("connect to controller");

    let err = client
        .get_lease("does-not-exist-rust-probe")
        .await
        .expect_err("bogus lease should error");

    match err {
        // A gRPC *status* response (rather than a transport error) proves TLS +
        // auth + the controller round-trip. The controller wraps the k8s
        // "not found" as UNKNOWN (mirrored by Python's translate_grpc_exceptions).
        ClientError::Rpc(status) => {
            eprintln!(
                "probe OK: status = {:?} ({})",
                status.code(),
                status.message()
            );
            assert!(
                status.message().contains("not found"),
                "expected a not-found message, got: {}",
                status.message()
            );
        }
        other => panic!("expected a gRPC status (connectivity OK), got: {other}"),
    }
}

/// Full lease lifecycle: acquire a lease for a live exporter matching
/// `JMP_RUST_TEST_SELECTOR`, then release it.
#[tokio::test]
async fn live_acquire_and_release() {
    let Some(cfg) = load_config() else {
        eprintln!("skip live_acquire_and_release: JMP_RUST_TEST_CONFIG not set");
        return;
    };
    let Ok(selector) = std::env::var("JMP_RUST_TEST_SELECTOR") else {
        eprintln!("skip live_acquire_and_release: JMP_RUST_TEST_SELECTOR not set");
        return;
    };

    let client = ControllerClient::connect(&cfg).await.expect("connect");

    let params = CreateLeaseParams {
        selector: Some(selector.clone()),
        duration: Duration::from_secs(120),
        ..Default::default()
    };
    let timing = LeaseTiming {
        poll_interval: Duration::from_secs(2),
        acquisition_timeout: Duration::from_secs(60),
    };

    let acquired = acquire(&client, params, None, Some(&cfg.metadata.name), timing)
        .await
        .expect("acquire lease");
    eprintln!(
        "acquired lease {} on exporter {}",
        acquired.name, acquired.exporter
    );
    assert!(!acquired.name.is_empty());
    assert!(!acquired.exporter.is_empty());

    client
        .delete_lease(&acquired.name)
        .await
        .expect("release lease");
    eprintln!("released lease {}", acquired.name);
}
