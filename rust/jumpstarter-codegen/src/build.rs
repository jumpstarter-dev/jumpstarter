//! Build-script helper (feature `build-helper`) for a proto-first **driver crate** — collapses the
//! per-crate `build.rs` + module wiring to two lines.
//!
//! A driver crate's `build.rs` is just:
//! ```ignore
//! fn main() {
//!     jumpstarter_codegen::build::driver_interface("jumpstarter/interfaces/power/v1/power.proto");
//! }
//! ```
//! and its `src/lib.rs` includes everything generated with one line:
//! ```ignore
//! jumpstarter_codegen::include_generated!();   // -> pub mod proto { … } + the typed client + macros
//! ```
//!
//! [`driver_interface`] compiles the interface proto (stock `tonic-build`: server stubs +
//! `FILE_DESCRIPTOR_SET`), generates the typed client + the `<short>_host!`/`<short>_client!` macros,
//! and writes a `jumpstarter_generated.rs` aggregator (the `proto` module + the client includes +
//! re-exports) that [`crate::include_generated`] pulls in.

use std::fmt::Write as _;
use std::path::PathBuf;

use crate::engine::interfaces_from_descriptor_set;
use crate::languages::rust::RustGenerator;
use crate::languages::LanguageGenerator;

/// Compile `proto_rel` (a path under the `interfaces/proto` root, itself resolved as
/// `../../interfaces/proto` relative to the crate's `CARGO_MANIFEST_DIR`) and emit, into `OUT_DIR`:
/// the tonic server stubs + descriptor set, the typed client(s) + entrypoint macros, and the
/// `jumpstarter_generated.rs` aggregator. Call from a driver crate's `build.rs`.
pub fn driver_interface(proto_rel: &str) {
    let manifest = PathBuf::from(env_var("CARGO_MANIFEST_DIR"));
    let interfaces_proto = manifest
        .join("../../interfaces/proto")
        .canonicalize()
        .expect("interfaces/proto tree not found (../../interfaces/proto from the crate)");
    let proto = interfaces_proto.join(proto_rel);
    let out_dir = PathBuf::from(env_var("OUT_DIR"));
    let fds_path = out_dir.join("interface.fds");

    println!("cargo:rerun-if-changed={}", proto.display());

    // Stock tonic/prost output + the FileDescriptorSet (the single descriptor source of truth).
    tonic_build::configure()
        .build_server(true)
        .build_client(false)
        .file_descriptor_set_path(&fds_path)
        .compile_protos(&[&proto], &[&interfaces_proto])
        .expect("failed to compile the interface proto");

    let fds = std::fs::read(&fds_path).expect("read the generated FileDescriptorSet");
    let interfaces = interfaces_from_descriptor_set(&fds).expect("walk the FileDescriptorSet");
    let generator = RustGenerator;

    // Generate the client(s) + macros into OUT_DIR, and assemble the aggregator: one `proto` module
    // (the tonic output + FILE_DESCRIPTOR_SET) plus each generated client file, re-exported.
    let mut agg = String::new();
    let mut packages = std::collections::BTreeSet::new();
    let mut client_files = Vec::new();
    for iface in &interfaces {
        for (name, source) in generator.generate_client(iface) {
            std::fs::write(out_dir.join(&name), source)
                .unwrap_or_else(|e| panic!("write generated {name}: {e}"));
            client_files.push(name);
        }
        packages.insert(iface.proto_package.clone());
    }

    // The `proto` module: the tonic-generated package + the descriptor. (One package per crate is the
    // norm; if several, the first owns `proto` — multi-package crates can still wire the rest by hand.)
    if let Some(pkg) = packages.iter().next() {
        let _ = writeln!(
            agg,
            "pub mod proto {{\n    \
                 tonic::include_proto!(\"{pkg}\");\n    \
                 pub const FILE_DESCRIPTOR_SET: &[u8] =\n        \
                     include_bytes!(concat!(env!(\"OUT_DIR\"), \"/interface.fds\"));\n\
             }}"
        );
    }
    // Each generated client (`use crate::proto;` inside resolves to the module above); re-exported.
    for (i, name) in client_files.iter().enumerate() {
        let _ = writeln!(
            agg,
            "#[doc(hidden)]\nmod __jmp_generated_{i} {{\n    \
                 include!(concat!(env!(\"OUT_DIR\"), \"/{name}\"));\n\
             }}\npub use __jmp_generated_{i}::*;"
        );
    }

    std::fs::write(out_dir.join("jumpstarter_generated.rs"), agg)
        .expect("write the jumpstarter_generated.rs aggregator");
}

fn env_var(key: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| panic!("{key} not set (run from a cargo build script)"))
}
