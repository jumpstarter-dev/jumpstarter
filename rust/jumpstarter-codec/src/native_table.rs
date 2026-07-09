//! The native dispatch table: `(uuid, @export-name) → NativeRoute`, built from the descriptors a
//! `GetReport` ships. Shared by the **client** bridge (`jumpstarter_client::client`, which maps a
//! dynamic Python `call(...)` onto the native wire) and the **server-side legacy shim**
//! (`jumpstarter_driver_core::legacy`, which translates an old `DriverCall` into the same native
//! dispatch). Both resolve `(uuid, method)` to a method path + input/output message descriptors.

use std::collections::HashMap;

use prost::Message as _;
use prost_reflect::prost_types::FileDescriptorSet;
use prost_reflect::{DescriptorPool, MessageDescriptor};

use crate::dynamic::export_name_for;

/// A resolved native route for one `(uuid, @export-method)`: the gRPC method path plus the
/// input/output message descriptors used to encode the request / decode the response.
#[derive(Clone)]
pub struct NativeRoute {
    pub path: String,
    pub input: MessageDescriptor,
    pub output: MessageDescriptor,
}

/// The native dispatch table: `(uuid, @export-name) → NativeRoute`, built once from the descriptors
/// `GetReport` ships. A driver/method missing here has no native surface.
pub type NativeTable = HashMap<(String, String), NativeRoute>;

/// Build the native dispatch table from a `GetReport`'s driver reports.
///
/// Each instance's `descriptor_set` is a self-contained `FileDescriptorSet` — the single source of
/// truth for *that* driver — so each instance is resolved in its **own** `DescriptorPool` and each
/// method indexed by `(uuid, @export-name) → NativeRoute`. Per-instance pools keep drivers fully
/// independent: two drivers may ship the same interface under different file names (e.g. Python's
/// synthesized `power.proto` next to the committed `jumpstarter/interfaces/power/v1/power.proto`
/// from a Rust/JVM host) without colliding, and one undecodable/unresolvable set only costs that
/// driver its routes, never anyone else's. Identical sets (byte-equal) share one pool build.
pub fn build_native_table(
    reports: &[jumpstarter_protocol::v1::DriverInstanceReport],
) -> NativeTable {
    // One pool per distinct descriptor set; `None` caches a decode/resolve failure so a bad set
    // shared by several instances warns once.
    let mut pools: HashMap<&[u8], Option<DescriptorPool>> = HashMap::new();
    let mut table: NativeTable = HashMap::new();
    for r in reports {
        let Some(bytes) = &r.descriptor_set else {
            continue;
        };
        let pool = pools.entry(bytes.as_slice()).or_insert_with(|| {
            let set = match FileDescriptorSet::decode(bytes.as_slice()) {
                Ok(set) => set,
                Err(e) => {
                    tracing::warn!(uuid = %r.uuid, error = %e, "skipping undecodable driver descriptor set");
                    return None;
                }
            };
            match DescriptorPool::from_file_descriptor_set(set) {
                Ok(pool) => Some(pool),
                Err(e) => {
                    tracing::warn!(uuid = %r.uuid, error = %e, "driver descriptor set does not resolve; no native routes for this driver");
                    None
                }
            }
        });
        let Some(pool) = pool else {
            continue;
        };
        // Every service in the pool is this driver's own (deps are message-only files).
        for service in pool.services() {
            let svc_full = service.full_name();
            for method in service.methods() {
                let export = export_name_for(method.name());
                let path = format!("/{svc_full}/{}", method.name());
                table.insert(
                    (r.uuid.clone(), export),
                    NativeRoute {
                        path,
                        input: method.input(),
                        output: method.output(),
                    },
                );
            }
        }
    }
    tracing::debug!(routes = table.len(), "native dispatch table built");
    table
}

#[cfg(test)]
mod tests {
    use super::*;
    use prost_reflect::prost_types::{
        DescriptorProto, FileDescriptorProto, MethodDescriptorProto, ServiceDescriptorProto,
    };

    /// A self-contained power-interface descriptor set whose interface file is named `file_name`.
    /// Package/symbols are identical across names — exactly how a Python host (synthesized
    /// `power.proto`) and a Rust/JVM host (committed `jumpstarter/interfaces/power/v1/power.proto`)
    /// each ship the same interface.
    fn power_set_named(file_name: &str) -> Vec<u8> {
        let empty_file = FileDescriptorProto {
            name: Some("google/protobuf/empty.proto".into()),
            package: Some("google.protobuf".into()),
            message_type: vec![DescriptorProto {
                name: Some("Empty".into()),
                ..Default::default()
            }],
            syntax: Some("proto3".into()),
            ..Default::default()
        };
        let unary = |name: &str| MethodDescriptorProto {
            name: Some(name.into()),
            input_type: Some(".google.protobuf.Empty".into()),
            output_type: Some(".google.protobuf.Empty".into()),
            ..Default::default()
        };
        let power_file = FileDescriptorProto {
            name: Some(file_name.into()),
            package: Some("jumpstarter.interfaces.power.v1".into()),
            dependency: vec!["google/protobuf/empty.proto".into()],
            message_type: vec![DescriptorProto {
                name: Some("PowerReading".into()),
                ..Default::default()
            }],
            service: vec![ServiceDescriptorProto {
                name: Some("PowerInterface".into()),
                method: vec![unary("On"), unary("Off")],
                ..Default::default()
            }],
            syntax: Some("proto3".into()),
            ..Default::default()
        };
        FileDescriptorSet {
            file: vec![empty_file, power_file],
        }
        .encode_to_vec()
    }

    fn report(uuid: &str, set: Vec<u8>) -> jumpstarter_protocol::v1::DriverInstanceReport {
        jumpstarter_protocol::v1::DriverInstanceReport {
            uuid: uuid.into(),
            descriptor_set: Some(set),
            ..Default::default()
        }
    }

    /// Regression: several drivers shipping the SAME interface (same package + message names)
    /// under DIFFERENT file names must each keep their native routes. The old merged-pool build
    /// died on the duplicate symbol ("PowerReading is already defined") and emptied the table for
    /// every driver in the lease.
    #[test]
    fn same_interface_under_different_file_names_routes_all_drivers() {
        let reports = [
            report("py-1", power_set_named("power.proto")),
            report(
                "rs-1",
                power_set_named("jumpstarter/interfaces/power/v1/power.proto"),
            ),
            report(
                "jvm-1",
                power_set_named("jumpstarter/interfaces/power/v1/power.proto"),
            ),
        ];
        let table = build_native_table(&reports);
        for uuid in ["py-1", "rs-1", "jvm-1"] {
            for method in ["on", "off"] {
                let route = table
                    .get(&(uuid.to_string(), method.to_string()))
                    .unwrap_or_else(|| panic!("missing route {uuid}/{method}"));
                assert_eq!(
                    route.path,
                    format!(
                        "/jumpstarter.interfaces.power.v1.PowerInterface/{}",
                        if method == "on" { "On" } else { "Off" }
                    )
                );
            }
        }
    }

    /// One driver's broken descriptor set must not cost any other driver its routes.
    #[test]
    fn bad_descriptor_set_only_affects_its_own_driver() {
        let reports = [
            report("bad-1", b"not a descriptor set".to_vec()),
            report("ok-1", power_set_named("power.proto")),
        ];
        let table = build_native_table(&reports);
        assert!(table.get(&("bad-1".into(), "on".into())).is_none());
        assert!(table.get(&("ok-1".into(), "on".into())).is_some());
    }
}
