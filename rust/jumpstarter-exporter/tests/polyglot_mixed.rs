//! Mixed-language polyglot exporter: one Python driver + one native Rust driver in the *same*
//! exporter, each in its own host subprocess, federated by the hub's `PolyglotHostFactory` +
//! `RoutingBackend` and driven over the one `DriverBackend` seam by UUID.
//!
//! Gated on `JMP_DRIVER_HOST_PYTHON` (a venv python with `jumpstarter` importable) and
//! `JMP_RUST_DRIVER_HOST` (the built `jmp-rust-host` binary). Run e.g.:
//!
//! ```sh
//! JMP_DRIVER_HOST_PYTHON=python/.venv/bin/python \
//!   JMP_RUST_DRIVER_HOST=rust/target/debug/jmp-rust-host \
//!   cargo test -p jumpstarter-exporter --test polyglot_mixed
//! ```

use jumpstarter_exporter::backend::HostFactory;
use jumpstarter_exporter::polyglot::PolyglotHostFactory;
use jumpstarter_protocol::v1::DriverCallRequest;

const CONFIG: &str = r#"apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: mixed
endpoint: grpc.example.com:443
token: dummy-token
tls:
  insecure: true
export:
  pypower:
    type: jumpstarter_driver_power.driver.MockPower
  rustpower:
    type: rust:power
"#;

#[tokio::test]
async fn federates_python_and_rust_drivers_in_one_exporter() {
    if std::env::var("JMP_DRIVER_HOST_PYTHON").is_err() || std::env::var("JMP_RUST_DRIVER_HOST").is_err()
    {
        eprintln!(
            "skipping: set JMP_DRIVER_HOST_PYTHON (a venv python) + JMP_RUST_DRIVER_HOST (jmp-rust-host)"
        );
        return;
    }

    let cfg = std::env::temp_dir().join(format!("jmp-mixed-{}.yaml", std::process::id()));
    std::fs::write(&cfg, CONFIG).unwrap();

    // Provision spawns one host per entry: a Python `jumpstarter.exporter_host` subprocess for
    // pypower and the native `jmp-rust-host` subprocess for rustpower, federated by UUID.
    let factory = PolyglotHostFactory::new(cfg.clone());
    let (backend, _guard) = factory
        .provision()
        .await
        .expect("provision the mixed exporter");

    let report = backend.get_report().await.expect("get_report");

    // One synthetic root + the two driver leaves, both advertising the Python PowerClient.
    let leaves: Vec<_> = report
        .reports
        .iter()
        .filter(|r| r.parent_uuid.is_some())
        .collect();
    let by_name = |n: &str| {
        leaves
            .iter()
            .find(|r| r.labels.get("jumpstarter.dev/name").map(String::as_str) == Some(n))
            .unwrap_or_else(|| panic!("no `{n}` leaf in {:#?}", report.reports))
    };
    let py = by_name("pypower");
    let rs = by_name("rustpower");
    assert_eq!(
        py.labels["jumpstarter.dev/client"],
        "jumpstarter_driver_power.client.PowerClient"
    );
    assert_eq!(
        rs.labels["jumpstarter.dev/client"],
        "jumpstarter_driver_power.client.PowerClient"
    );
    // Both entries are re-parented under the one synthetic root.
    assert_eq!(py.parent_uuid, rs.parent_uuid);
    assert!(py.parent_uuid.is_some());

    // Drive each driver through the federated backend by UUID — the call is routed to its own
    // host (a Python subprocess and the native Rust subprocess), and both succeed.
    for leaf in [py, rs] {
        let name = leaf.labels.get("jumpstarter.dev/name").cloned().unwrap();
        let resp = backend
            .driver_call(DriverCallRequest {
                uuid: leaf.uuid.clone(),
                method: "on".to_string(),
                args: vec![],
            })
            .await;
        assert!(resp.is_ok(), "driver_call(on) on `{name}` failed: {resp:?}");
    }

    let _ = std::fs::remove_file(&cfg);
}
