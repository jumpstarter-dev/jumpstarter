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
use jumpstarter_transport::demux::DRIVER_UUID_KEY;
use tonic::metadata::{AsciiMetadataValue, MetadataMap};

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

    // Drive both drivers through the federated backend *concurrently* by UUID over the **native**
    // gRPC path (`forward_unary`, the production path post-cutover). Each call routes to its own
    // host — a Python `jumpstarter.exporter_host` subprocess (served by `ForeignDriver`) and the
    // native `jmp-rust-host` subprocess (served by `NativeDriverBackend`) — so the two run in
    // genuinely separate OS processes (separate GILs / runtimes): no cross-driver serialization.
    // Both serve `PowerInterface.On(Empty)->Empty` natively from their advertised descriptor, with
    // zero generated servicer, proving native dispatch is language-neutral end to end.
    let call = |uuid: String| {
        let backend = backend.clone();
        async move {
            let mut md = MetadataMap::new();
            md.insert(DRIVER_UUID_KEY, AsciiMetadataValue::try_from(uuid).unwrap());
            backend
                .forward_unary(
                    "/jumpstarter.interfaces.power.v1.PowerInterface/On",
                    md,
                    bytes::Bytes::new(),
                )
                .await
        }
    };
    let (py_resp, rs_resp) = tokio::join!(call(py.uuid.clone()), call(rs.uuid.clone()));
    assert!(py_resp.is_ok(), "concurrent native On on pypower failed: {py_resp:?}");
    assert!(rs_resp.is_ok(), "concurrent native On on rustpower failed: {rs_resp:?}");

    let _ = std::fs::remove_file(&cfg);
}

const PY_CONFIG: &str = r#"apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: pyonly
endpoint: grpc.example.com:443
token: dummy-token
tls:
  insecure: true
export:
  pypower:
    type: jumpstarter_driver_power.driver.MockPower
"#;

/// Regression guard for the native-gRPC `forward_unary` federation chain: a native per-driver
/// unary call must reach a Python driver *through the hub*. The hub federates even a single entry
/// through `RoutingBackend` → `ShmChannelBackend` → the host's `ChannelBackend`, and every one of
/// those backends must forward the opaque call (the default trait impl declines with "native
/// unary forwarding not supported by this backend"). This caught the e2e regression where
/// `RoutingBackend` and `ShmChannelBackend` lacked the override, so a federated native call died at
/// the hub even though the host-direct path worked. Python-only, so it needs just
/// `JMP_DRIVER_HOST_PYTHON`.
#[tokio::test]
async fn native_unary_forwards_through_the_hub_to_a_python_driver() {
    if std::env::var("JMP_DRIVER_HOST_PYTHON").is_err() {
        eprintln!("skipping: set JMP_DRIVER_HOST_PYTHON (a venv python with `jumpstarter`)");
        return;
    }

    let cfg = std::env::temp_dir().join(format!("jmp-pyonly-{}.yaml", std::process::id()));
    std::fs::write(&cfg, PY_CONFIG).unwrap();

    let factory = PolyglotHostFactory::new(cfg.clone());
    let (backend, _guard) = factory
        .provision()
        .await
        .expect("provision the python-only exporter");

    let report = backend.get_report().await.expect("get_report");
    let pypower = report
        .reports
        .iter()
        .find(|r| r.labels.get("jumpstarter.dev/name").map(String::as_str) == Some("pypower"))
        .unwrap_or_else(|| panic!("no `pypower` leaf in {:#?}", report.reports));

    // A native unary call to `PowerInterface.On(Empty) -> Empty`: empty request body, the driver
    // uuid carried in the demux header exactly as the live demux sets it. Routed by the hub to the
    // owning entry, opaque-forwarded over the host UDS, dynamically dispatched to `MockPower.on()`.
    let mut metadata = MetadataMap::new();
    metadata.insert(
        DRIVER_UUID_KEY,
        AsciiMetadataValue::try_from(pypower.uuid.as_str()).unwrap(),
    );
    let result = backend
        .forward_unary(
            "/jumpstarter.interfaces.power.v1.PowerInterface/On",
            metadata,
            bytes::Bytes::new(),
        )
        .await;

    assert!(
        result.is_ok(),
        "native forward_unary(On) through the federated hub failed: {result:?}"
    );
    let (_init, body, _trailers) = result.unwrap();
    // `On` returns `Empty`, so the response message is zero bytes.
    assert!(body.is_empty(), "expected empty On() response, got {} bytes", body.len());

    let _ = std::fs::remove_file(&cfg);
}

const RUST_CONFIG: &str = r#"apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: rustonly
endpoint: grpc.example.com:443
token: dummy-token
tls:
  insecure: true
export:
  rustpower:
    type: rust:power
"#;

/// The native-gRPC `forward_unary` path must reach a **Rust** driver through the hub, exactly as it
/// reaches a Python one. The chain is hub `RoutingBackend` → `ShmChannelBackend` → the
/// `jmp-rust-host` subprocess's `ChannelBackend` → that host's demux → `NativeDriverBackend` →
/// `DynamicBackend` → `MockPower.on()` — all from the driver's advertised `descriptor_set`, no
/// generated servicer. Rust-only, so it needs just `JMP_RUST_DRIVER_HOST`.
#[tokio::test]
async fn native_unary_forwards_through_the_hub_to_a_rust_driver() {
    if std::env::var("JMP_RUST_DRIVER_HOST").is_err() {
        eprintln!("skipping: set JMP_RUST_DRIVER_HOST (the built `jmp-rust-host` binary)");
        return;
    }

    let cfg = std::env::temp_dir().join(format!("jmp-rustonly-{}.yaml", std::process::id()));
    std::fs::write(&cfg, RUST_CONFIG).unwrap();

    let factory = PolyglotHostFactory::new(cfg.clone());
    let (backend, _guard) = factory
        .provision()
        .await
        .expect("provision the rust-only exporter");

    let report = backend.get_report().await.expect("get_report");
    let rustpower = report
        .reports
        .iter()
        .find(|r| r.labels.get("jumpstarter.dev/name").map(String::as_str) == Some("rustpower"))
        .unwrap_or_else(|| panic!("no `rustpower` leaf in {:#?}", report.reports));
    // The native Rust driver advertises its interface descriptor over the report (so the client can
    // decode it) — the same way a Python driver does.
    assert!(
        rustpower.descriptor_set.is_some(),
        "native Rust driver must advertise a descriptor_set"
    );

    let mut metadata = MetadataMap::new();
    metadata.insert(
        DRIVER_UUID_KEY,
        AsciiMetadataValue::try_from(rustpower.uuid.as_str()).unwrap(),
    );
    let result = backend
        .forward_unary(
            "/jumpstarter.interfaces.power.v1.PowerInterface/On",
            metadata,
            bytes::Bytes::new(),
        )
        .await;

    assert!(
        result.is_ok(),
        "native forward_unary(On) to the rust driver through the hub failed: {result:?}"
    );
    let (_init, body, _trailers) = result.unwrap();
    assert!(body.is_empty(), "expected empty On() response, got {} bytes", body.len());

    let _ = std::fs::remove_file(&cfg);
}

/// The per-entry `host:` launcher (`{ bin, args }`) must dispatch through the hub exactly like the
/// default resolution, letting one exporter pin different/mixed hosts. We pin `rustpower` to the
/// configured host binary via `host:` (not the process-wide env, which `host:` takes precedence
/// over). Gated on `JMP_RUST_DRIVER_HOST` only to locate a built host binary.
#[tokio::test]
async fn native_unary_forwards_through_the_hub_via_per_entry_host() {
    let Ok(host_bin) = std::env::var("JMP_RUST_DRIVER_HOST") else {
        eprintln!("skipping: set JMP_RUST_DRIVER_HOST (a built native host binary) to locate one");
        return;
    };

    // A config whose entry pins `host:` to the binary; clear the env so only `host:` can resolve it.
    let cfg = std::env::temp_dir().join(format!("jmp-hostspec-{}.yaml", std::process::id()));
    std::fs::write(
        &cfg,
        format!(
            "apiVersion: jumpstarter.dev/v1alpha1\nkind: ExporterConfig\n\
metadata:\n  namespace: default\n  name: hostspec\n\
endpoint: grpc.example.com:443\ntoken: dummy\ntls:\n  insecure: true\n\
export:\n  rustpower:\n    type: rust:jumpstarter-driver-power-example\n    host: {host_bin}\n"
        ),
    )
    .unwrap();
    std::env::remove_var("JMP_RUST_DRIVER_HOST");

    let (backend, _guard) = PolyglotHostFactory::new(cfg.clone())
        .provision()
        .await
        .expect("provision via per-entry host:");

    let report = backend.get_report().await.expect("get_report");
    let rustpower = report
        .reports
        .iter()
        .find(|r| r.labels.get("jumpstarter.dev/name").map(String::as_str) == Some("rustpower"))
        .unwrap_or_else(|| panic!("no `rustpower` leaf in {:#?}", report.reports));

    let mut metadata = MetadataMap::new();
    metadata.insert(
        DRIVER_UUID_KEY,
        AsciiMetadataValue::try_from(rustpower.uuid.as_str()).unwrap(),
    );
    let result = backend
        .forward_unary(
            "/jumpstarter.interfaces.power.v1.PowerInterface/On",
            metadata,
            bytes::Bytes::new(),
        )
        .await;
    assert!(result.is_ok(), "forward_unary(On) via host: failed: {result:?}");

    let _ = std::fs::remove_file(&cfg);
}
