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
use jumpstarter_exporter::session::{self, SessionRouter};
use jumpstarter_exporter::SlimHost;
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use jumpstarter_protocol::v1::{DriverCallRequest, StreamRequest};

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

/// inc1: the Rust ExporterServiceServer proxies GetReport + DriverCall into the slim
/// host. Hits the Rust server with a Rust client to isolate the server from
/// grpcio-client interop.
#[tokio::test]
async fn rust_server_proxies_getreport_and_drivercall() {
    if std::env::var("JMP_DRIVER_HOST_PYTHON").is_err() {
        eprintln!("skipping: set JMP_DRIVER_HOST_PYTHON to a python with `jumpstarter` importable");
        return;
    }

    let cfg_path = std::env::temp_dir().join(format!("jmp-srv-test-{}.yaml", std::process::id()));
    std::fs::write(&cfg_path, CONFIG).unwrap();

    let host = SlimHost::spawn(&cfg_path).await.expect("spawn slim host");
    let router = SessionRouter::build(uds_channel(host.socket()).await.unwrap())
        .await
        .expect("build router");

    let dir = std::env::temp_dir().join(format!("jmp-srv-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let main = dir.join("m.sock");
    let hook = dir.join("h.sock");
    let _server = session::serve(router, &main, &hook).expect("serve");

    let mut client = ExporterServiceClient::new(uds_channel(main.to_str().unwrap()).await.unwrap());

    // GetReport through the Rust server.
    let report = client.get_report(()).await.expect("GetReport").into_inner();
    let power = report
        .reports
        .iter()
        .find(|r| r.labels.get("jumpstarter.dev/name").map(String::as_str) == Some("power"))
        .expect("power leaf");

    // DriverCall power.on through the Rust server -> proxied to the slim host.
    let resp = client
        .driver_call(DriverCallRequest {
            uuid: power.uuid.clone(),
            method: "on".to_string(),
            args: vec![],
        })
        .await;
    assert!(resp.is_ok(), "DriverCall(power.on) failed: {resp:?}");

    // Unknown uuid -> UNKNOWN.
    let bad = client
        .driver_call(DriverCallRequest {
            uuid: "00000000-0000-0000-0000-000000000000".to_string(),
            method: "on".to_string(),
            args: vec![],
        })
        .await;
    assert_eq!(bad.unwrap_err().code(), tonic::Code::Unknown);

    let _ = std::fs::remove_file(&cfg_path);
    let _ = std::fs::remove_dir_all(&dir);
}

/// inc2: the inner RouterService.Stream proxy relays the host's resource-handle
/// initial metadata to the client BEFORE the client sends any frame (the resource
/// handshake + metadata-before-frame deadlock regression, in one).
#[tokio::test]
async fn router_stream_relays_resource_initial_metadata() {
    if std::env::var("JMP_DRIVER_HOST_PYTHON").is_err() {
        eprintln!("skipping: set JMP_DRIVER_HOST_PYTHON to a python with `jumpstarter` importable");
        return;
    }

    let cfg_path =
        std::env::temp_dir().join(format!("jmp-stream-test-{}.yaml", std::process::id()));
    std::fs::write(&cfg_path, CONFIG).unwrap();

    let host = SlimHost::spawn(&cfg_path).await.expect("spawn slim host");
    let router = SessionRouter::build(uds_channel(host.socket()).await.unwrap())
        .await
        .expect("build router");

    let dir = std::env::temp_dir().join(format!("jmp-stream-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let main = dir.join("m.sock");
    let _server = session::serve(router.clone(), &main, &dir.join("h.sock")).expect("serve");

    // The power leaf's uuid; any driver can host a resource handle.
    let report = ExporterServiceClient::new(uds_channel(main.to_str().unwrap()).await.unwrap())
        .get_report(())
        .await
        .unwrap()
        .into_inner();
    let uuid = report
        .reports
        .iter()
        .find(|r| r.parent_uuid.is_some())
        .expect("a leaf")
        .uuid
        .clone();

    // Open a resource Stream with a *pending* uplink (never send a frame), so a
    // returned response proves the host's initial metadata is relayed before any
    // client byte. A deadlocked proxy would hang here.
    let mut req = tonic::Request::new(tokio_stream::pending::<StreamRequest>());
    let request_json = format!(r#"{{"kind":"resource","uuid":"{uuid}"}}"#);
    req.metadata_mut()
        .insert("request", request_json.parse().unwrap());

    let resp = tokio::time::timeout(
        std::time::Duration::from_secs(10),
        RouterServiceClient::new(uds_channel(main.to_str().unwrap()).await.unwrap()).stream(req),
    )
    .await
    .expect("stream() returned before the timeout (no metadata deadlock)")
    .expect("stream() ok");

    // The host minted a resource handle and sent it as `resource` initial metadata.
    assert!(
        resp.metadata().get("resource").is_some(),
        "expected a `resource` handle in the relayed initial metadata, got {:?}",
        resp.metadata()
    );

    let _ = std::fs::remove_file(&cfg_path);
    let _ = std::fs::remove_dir_all(&dir);
}

/// inc2: an unknown driver uuid on a Stream is rejected at the boundary with UNKNOWN.
#[tokio::test]
async fn router_stream_unknown_uuid_is_unknown() {
    if std::env::var("JMP_DRIVER_HOST_PYTHON").is_err() {
        eprintln!("skipping: set JMP_DRIVER_HOST_PYTHON to a python with `jumpstarter` importable");
        return;
    }

    let cfg_path = std::env::temp_dir().join(format!("jmp-stream-bad-{}.yaml", std::process::id()));
    std::fs::write(&cfg_path, CONFIG).unwrap();
    let host = SlimHost::spawn(&cfg_path).await.expect("spawn slim host");
    let router = SessionRouter::build(uds_channel(host.socket()).await.unwrap())
        .await
        .unwrap();
    let dir = std::env::temp_dir().join(format!("jmp-stream-bad-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let main = dir.join("m.sock");
    let _server = session::serve(router, &main, &dir.join("h.sock")).unwrap();

    let mut req = tonic::Request::new(tokio_stream::pending::<StreamRequest>());
    req.metadata_mut().insert(
        "request",
        r#"{"kind":"resource","uuid":"00000000-0000-0000-0000-000000000000"}"#
            .parse()
            .unwrap(),
    );
    let err = RouterServiceClient::new(uds_channel(main.to_str().unwrap()).await.unwrap())
        .stream(req)
        .await
        .unwrap_err();
    assert_eq!(err.code(), tonic::Code::Unknown);

    let _ = std::fs::remove_file(&cfg_path);
    let _ = std::fs::remove_dir_all(&dir);
}
