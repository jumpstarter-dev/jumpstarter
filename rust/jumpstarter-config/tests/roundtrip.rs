//! Round-trip and differential tests for the config models.
//!
//! Fixtures under `fixtures/configs/` are produced by the **real Python config
//! save** path (`fixtures/generate_config_golden.py`). For each `<name>.yaml`
//! there is a `<name>.py-roundtrip.yaml` — Python's own reload→re-save of it.
//!
//! Regenerate (and review the diff) with:
//!
//! ```sh
//! python/.venv/bin/python \
//!   rust/jumpstarter-config/tests/fixtures/generate_config_golden.py
//! ```

use std::fmt::Debug;
use std::fs;
use std::path::PathBuf;

use jumpstarter_config::{
    ClientConfig, DriverInstance, ExporterConfig, GrpcOptionValue, OnFailure, UserConfig,
    YamlConfig,
};

fn configs_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/configs")
}

fn read(name: &str) -> String {
    fs::read_to_string(configs_dir().join(name)).unwrap_or_else(|e| panic!("read {name}: {e}"))
}

/// Parse a Python-saved fixture, assert it round-trips through the Rust codec
/// unchanged, and assert Rust's parse agrees with Python's own reload of it.
fn check<T>(name: &str) -> T
where
    T: YamlConfig + PartialEq + Debug,
{
    let yaml = read(&format!("{name}.yaml"));
    let parsed =
        T::from_yaml(&yaml).unwrap_or_else(|e| panic!("[{name}] parse failed: {e}\n{yaml}"));

    // Round-trip: serialize -> parse -> identical.
    let reserialized = parsed.to_yaml().expect("serialize");
    let reparsed = T::from_yaml(&reserialized)
        .unwrap_or_else(|e| panic!("[{name}] re-parse failed: {e}\n{reserialized}"));
    assert_eq!(parsed, reparsed, "[{name}] round-trip changed the data");

    // Differential: parsing Python's reload must yield the same data.
    let py = read(&format!("{name}.py-roundtrip.yaml"));
    let py_parsed = T::from_yaml(&py)
        .unwrap_or_else(|e| panic!("[{name}] py-roundtrip parse failed: {e}\n{py}"));
    assert_eq!(
        parsed, py_parsed,
        "[{name}] Rust parse disagrees with Python reload"
    );

    parsed
}

#[test]
fn all_client_fixtures_roundtrip() {
    check::<ClientConfig>("client_full");
    check::<ClientConfig>("client_minimal");
    check::<ClientConfig>("client_leases_and_refresh");
}

#[test]
fn all_exporter_fixtures_roundtrip() {
    check::<ExporterConfig>("exporter_tree");
    check::<ExporterConfig>("exporter_minimal");
}

#[test]
fn all_user_fixtures_roundtrip() {
    check::<UserConfig>("user_set");
    check::<UserConfig>("user_empty");
}

/// Every `*.yaml` fixture (excluding the `.py-roundtrip` companions) must parse
/// into the type named by its `kind` — guards against forgetting a fixture above.
#[test]
fn every_fixture_parses_by_kind() {
    let mut seen = 0;
    for entry in fs::read_dir(configs_dir()).unwrap() {
        let path = entry.unwrap().path();
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        if !name.ends_with(".yaml") || name.ends_with(".py-roundtrip.yaml") {
            continue;
        }
        let yaml = fs::read_to_string(&path).unwrap();
        let probe: serde_yaml_ng::Value = serde_yaml_ng::from_str(&yaml).unwrap();
        let kind = probe.get("kind").and_then(|k| k.as_str()).unwrap_or("");
        match kind {
            "ClientConfig" => {
                ClientConfig::from_yaml(&yaml).unwrap_or_else(|e| panic!("{name}: {e}"));
            }
            "ExporterConfig" => {
                ExporterConfig::from_yaml(&yaml).unwrap_or_else(|e| panic!("{name}: {e}"));
            }
            "UserConfig" => {
                UserConfig::from_yaml(&yaml).unwrap_or_else(|e| panic!("{name}: {e}"));
            }
            other => panic!("{name}: unknown kind {other:?}"),
        }
        seen += 1;
    }
    assert!(seen >= 7, "expected to parse all fixtures, only saw {seen}");
}

#[test]
fn client_full_semantics() {
    let c = check::<ClientConfig>("client_full");
    assert_eq!(c.metadata.namespace.as_deref(), Some("lab"));
    assert_eq!(c.metadata.name, "client-1");
    assert!(c.tls.insecure);
    assert_eq!(c.tls.ca, "CERTDATA");
    assert_eq!(c.drivers.allow, vec!["jumpstarter_driver_*", "mypkg.*"]);
    assert!(!c.drivers.r#unsafe);
    // Mixed int/str grpcOptions are both preserved with their types.
    assert_eq!(
        c.grpc_options.get("grpc.max_receive_message_length"),
        Some(&GrpcOptionValue::Int(16777216))
    );
    assert_eq!(
        c.grpc_options.get("grpc.primary_user_agent"),
        Some(&GrpcOptionValue::Str("jmp".to_string()))
    );
    // leases omitted in the fixture -> defaults.
    assert_eq!(c.leases.acquisition_timeout, 7200);
}

#[test]
fn client_leases_and_refresh_semantics() {
    let c = check::<ClientConfig>("client_leases_and_refresh");
    assert_eq!(c.refresh_token.as_deref(), Some("ref-token"));
    // Non-default lease block is preserved.
    assert_eq!(c.leases.acquisition_timeout, 3600);
}

#[test]
fn exporter_tree_driver_variants() {
    let e = check::<ExporterConfig>("exporter_tree");
    assert_eq!(e.description.as_deref(), Some("lab exporter"));
    assert_eq!(
        e.grpc_options.get("grpc.keepalive_time_ms"),
        Some(&GrpcOptionValue::Int(30000))
    );

    // Base driver with typed config values.
    match e.export.get("power").expect("power") {
        DriverInstance::Base(b) => {
            assert_eq!(b.r#type, "jumpstarter_driver_power.driver.MockPower");
            assert_eq!(b.description.as_deref(), Some("power port"));
            assert_eq!(b.config.get("voltage").and_then(|v| v.as_i64()), Some(5));
            assert_eq!(
                b.config.get("enabled").and_then(|v| v.as_bool()),
                Some(true)
            );
            assert_eq!(b.config.get("rate").and_then(|v| v.as_f64()), Some(0.5));
            assert_eq!(
                b.config
                    .get("tags")
                    .and_then(|v| v.as_array())
                    .map(|a| a.len()),
                Some(2)
            );
        }
        other => panic!("power should be Base, got {other:?}"),
    }

    // Composite with a nested Base child.
    match e.export.get("bucket").expect("bucket") {
        DriverInstance::Composite(c) => match c.children.get("serial").expect("serial") {
            DriverInstance::Base(b) => {
                assert_eq!(b.r#type, "jumpstarter_driver_pyserial.driver.PySerial");
                assert_eq!(
                    b.config.get("url").and_then(|v| v.as_str()),
                    Some("loop://")
                );
                assert_eq!(
                    b.methods_description.get("read").map(String::as_str),
                    Some("reads bytes")
                );
            }
            other => panic!("serial should be Base, got {other:?}"),
        },
        other => panic!("bucket should be Composite, got {other:?}"),
    }

    // Proxy.
    match e.export.get("alias_ref").expect("alias_ref") {
        DriverInstance::Proxy(p) => assert_eq!(p.reference, "power"),
        other => panic!("alias_ref should be Proxy, got {other:?}"),
    }

    // Hooks.
    let before = e.hooks.before_lease.as_ref().expect("beforeLease");
    assert_eq!(before.script, "j power on");
    assert_eq!(before.timeout, 30);
    assert_eq!(before.on_failure, OnFailure::EndLease);
    assert!(before.exec.is_none());

    let after = e.hooks.after_lease.as_ref().expect("afterLease");
    assert_eq!(after.exec.as_deref(), Some("/bin/bash"));
    assert_eq!(after.timeout, 120);
    assert_eq!(after.on_failure, OnFailure::Warn);

    // Failure detection defaults.
    assert_eq!(e.failure_detection.max_rapid_failures, 5);
    assert_eq!(e.failure_detection.rapid_failure_window, 60);
}

#[test]
fn user_config_semantics() {
    let empty = check::<UserConfig>("user_empty");
    assert!(empty.current_client().is_none());

    // A user config with a selected client parses the alias string.
    let yaml =
        "apiVersion: jumpstarter.dev/v1alpha1\nkind: UserConfig\nconfig:\n  current-client: prod\n";
    let u = UserConfig::from_yaml(yaml).unwrap();
    assert_eq!(u.current_client(), Some("prod"));
}
