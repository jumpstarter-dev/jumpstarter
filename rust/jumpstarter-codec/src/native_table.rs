//! The native dispatch table: `(uuid, @export-name) → NativeRoute`, built from the descriptors a
//! `GetReport` ships. Shared by the **client** bridge (`jumpstarter_core::client`, which maps a
//! dynamic Python `call(...)` onto the native wire) and the **server-side legacy shim**
//! (`jumpstarter_core::legacy`, which translates an old `DriverCall` into the same native
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
/// Each instance's `descriptor_set` is a self-contained `FileDescriptorSet`. All sets are merged
/// into one pool (deduping shared files, deps-first preserved) so cross-file imports resolve; then
/// for every instance, the service declared in *its* set is resolved in the pool and each method is
/// indexed by `(uuid, @export-name) → NativeRoute`. An instance without a descriptor set, or whose
/// set fails to decode/resolve, simply contributes no native routes.
pub fn build_native_table(
    reports: &[jumpstarter_protocol::v1::DriverInstanceReport],
) -> NativeTable {
    // Merge every node's set into one pool (dedup by file name, deps-first preserved).
    let mut files = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for r in reports {
        let Some(bytes) = &r.descriptor_set else {
            continue;
        };
        let set = match FileDescriptorSet::decode(bytes.as_slice()) {
            Ok(set) => set,
            Err(e) => {
                tracing::warn!(uuid = %r.uuid, error = %e, "skipping undecodable driver descriptor set");
                continue;
            }
        };
        for file in set.file {
            if seen.insert(file.name().to_string()) {
                files.push(file);
            }
        }
    }
    let pool = match DescriptorPool::from_file_descriptor_set(FileDescriptorSet { file: files }) {
        Ok(pool) => pool,
        Err(e) => {
            tracing::warn!(error = %e, "native interface build failed; no native routes");
            DescriptorPool::new()
        }
    };

    // Index each instance's service methods by `@export` name.
    let mut table: NativeTable = HashMap::new();
    for r in reports {
        let Some(bytes) = &r.descriptor_set else {
            continue;
        };
        let Ok(set) = FileDescriptorSet::decode(bytes.as_slice()) else {
            continue;
        };
        // The interface file is the one carrying a service (deps are message-only).
        for file in &set.file {
            for svc in &file.service {
                let pkg = file.package();
                let svc_full = if pkg.is_empty() {
                    svc.name().to_string()
                } else {
                    format!("{pkg}.{}", svc.name())
                };
                let Some(service) = pool.get_service_by_name(&svc_full) else {
                    continue;
                };
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
    }
    tracing::debug!(routes = table.len(), "native dispatch table built");
    table
}
