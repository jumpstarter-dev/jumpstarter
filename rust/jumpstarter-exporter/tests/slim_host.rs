//! inc0 integration test (native-exporter migration): the slim driver host serves a
//! driver-level `ExporterService` for the *whole* config tree on a single socket.
//!
//! Gated on `JMP_DRIVER_HOST_PYTHON` (a Python with `jumpstarter` importable), since
//! it spawns a real Python subprocess. Run with e.g.:
//!
//! ```sh
//! JMP_DRIVER_HOST_PYTHON=python/.venv/bin/python \
//!   cargo test -p jumpstarter-exporter --test slim_host
//! ```

use jumpstarter_exporter::control::uds_channel;
use jumpstarter_exporter::SlimHost;
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;

const CONFIG: &str = r#"apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: slim-host-test
endpoint: grpc.example.com:443
token: dummy-token
tls:
  insecure: true
export:
  power:
    type: jumpstarter_driver_power.driver.MockPower
"#;

#[tokio::test]
async fn slim_host_serves_whole_tree_getreport() {
    if std::env::var("JMP_DRIVER_HOST_PYTHON").is_err() {
        eprintln!("skipping: set JMP_DRIVER_HOST_PYTHON to a python with `jumpstarter` importable");
        return;
    }

    let cfg_path = std::env::temp_dir().join(format!("jmp-slim-test-{}.yaml", std::process::id()));
    std::fs::write(&cfg_path, CONFIG).unwrap();

    let host = SlimHost::spawn(&cfg_path).await.expect("spawn slim host");
    let channel = uds_channel(host.socket())
        .await
        .expect("connect to host UDS");
    let report = ExporterServiceClient::new(channel)
        .get_report(())
        .await
        .expect("GetReport")
        .into_inner();

    // The whole tree is hosted in one process: a Composite root (absent parent_uuid)
    // plus the MockPower leaf carrying its client-class label.
    let roots: Vec<_> = report
        .reports
        .iter()
        .filter(|r| r.parent_uuid.is_none())
        .collect();
    assert_eq!(
        roots.len(),
        1,
        "expected exactly one root, got {:#?}",
        report.reports
    );

    // The MockPower driver advertises the power client class on its leaf, parented to
    // the Composite root.
    let power_leaf = report.reports.iter().find(|r| {
        r.labels.get("jumpstarter.dev/client").map(String::as_str)
            == Some("jumpstarter_driver_power.client.PowerClient")
    });
    let power_leaf = power_leaf
        .unwrap_or_else(|| panic!("expected a PowerClient leaf, got {:#?}", report.reports));
    assert_eq!(
        power_leaf.parent_uuid.as_deref(),
        Some(roots[0].uuid.as_str()),
        "power leaf should be parented to the Composite root"
    );
    assert_eq!(
        power_leaf
            .labels
            .get("jumpstarter.dev/name")
            .map(String::as_str),
        Some("power")
    );

    let _ = std::fs::remove_file(&cfg_path);
}
