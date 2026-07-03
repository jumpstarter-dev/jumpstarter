//! Build-script helper (feature `build-helper`) for a proto-first interface crate — collapses the
//! per-crate `build.rs` + module wiring to two lines.
//!
//! Three modes let a crate generate exactly the side(s) it wants, so a pure crate never pulls the
//! other side in:
//!
//! - [`driver_interface`] — **driver-only**: the tonic server trait + messages + `FILE_DESCRIPTOR_SET`
//!   (no typed client). For a pure driver crate (deps `jumpstarter-driver`).
//! - [`client_interface`] — **client-only**: the prost messages + `FILE_DESCRIPTOR_SET` + the
//!   `RustGenerator` typed client (no server trait). For a pure client crate (deps `jumpstarter-client`).
//! - [`interface`] — **combined**: both the server trait and the typed client, for a single combined
//!   crate (deps both). This is the original behavior.
//!
//! A driver crate's `build.rs` is just:
//! ```ignore
//! fn main() {
//!     jumpstarter_codegen::build::driver_interface("jumpstarter/interfaces/power/v1/power.proto");
//! }
//! ```
//! and its `src/lib.rs` includes everything generated with one line:
//! ```ignore
//! jumpstarter_driver::interface!();   // -> pub mod proto { … } (+ the typed client for client/combined)
//! ```
//!
//! Each helper compiles the interface proto (stock `tonic-build`: the requested server/client stubs +
//! `FILE_DESCRIPTOR_SET`), optionally generates the typed client + entrypoint macros, and writes a
//! `jumpstarter_generated.rs` aggregator (the `proto` module + the client includes + re-exports) that
//! `jumpstarter_driver::interface!()` pulls in.

use std::fmt::Write as _;
use std::path::PathBuf;

use crate::engine::interfaces_from_descriptor_set;
use crate::languages::rust::RustGenerator;
use crate::languages::LanguageGenerator;

/// Which side(s) of the interface a build helper emits.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    /// Server trait + messages + descriptor; no typed client.
    Driver,
    /// Messages + descriptor + typed client; no server trait.
    Client,
    /// Both the server trait and the typed client.
    Combined,
}

/// **Driver-only**: compile `proto_rel` for the tonic server trait + messages + `FILE_DESCRIPTOR_SET`
/// (no typed client) and write the `jumpstarter_generated.rs` aggregator. Call from a pure driver
/// crate's `build.rs` (deps `jumpstarter-driver`).
pub fn driver_interface(proto_rel: &str) {
    build_interface(proto_rel, Mode::Driver);
}

/// **Client-only**: compile `proto_rel` for the prost messages + `FILE_DESCRIPTOR_SET` (no server
/// trait) plus the `RustGenerator` typed client + entrypoint macros, and write the
/// `jumpstarter_generated.rs` aggregator. Call from a pure client crate's `build.rs` (deps
/// `jumpstarter-client`).
pub fn client_interface(proto_rel: &str) {
    build_interface(proto_rel, Mode::Client);
}

/// **Combined**: compile `proto_rel` for BOTH the tonic server trait and the `RustGenerator` typed
/// client (+ messages, descriptor, entrypoint macros), and write the `jumpstarter_generated.rs`
/// aggregator. Call from a single combined crate's `build.rs` (deps both `jumpstarter-driver` and
/// `jumpstarter-client`).
pub fn interface(proto_rel: &str) {
    build_interface(proto_rel, Mode::Combined);
}

/// Compile `proto_rel` (a path under the `interfaces/proto` root, itself resolved as
/// `../../interfaces/proto` relative to the crate's `CARGO_MANIFEST_DIR`) for the requested `mode`
/// and emit, into `OUT_DIR`: the requested tonic stubs + descriptor set, the typed client(s) +
/// entrypoint macros (client/combined modes), and the `jumpstarter_generated.rs` aggregator.
fn build_interface(proto_rel: &str, mode: Mode) {
    let manifest = PathBuf::from(env_var("CARGO_MANIFEST_DIR"));
    let interfaces_proto = manifest
        .join("../../interfaces/proto")
        .canonicalize()
        .expect("interfaces/proto tree not found (../../interfaces/proto from the crate)");
    let proto = interfaces_proto.join(proto_rel);
    let out_dir = PathBuf::from(env_var("OUT_DIR"));
    // Key the generated artifacts by the proto file stem (e.g. `power.proto` -> `power`), so the
    // include macro names the interface explicitly — `interface!(power)` includes `power_generated.rs`
    // — and several interfaces in one crate don't clobber each other's descriptor/aggregator.
    let stem = PathBuf::from(proto_rel)
        .file_stem()
        .and_then(|s| s.to_str())
        .expect("proto path has a file stem")
        .to_string();
    let fds_path = out_dir.join(format!("{stem}.fds"));

    println!("cargo:rerun-if-changed={}", proto.display());

    let wants_server = matches!(mode, Mode::Driver | Mode::Combined);
    let wants_client = matches!(mode, Mode::Client | Mode::Combined);

    // Stock tonic/prost output + the FileDescriptorSet (the single descriptor source of truth). A
    // driver crate gets the server trait; a client crate gets only the prost messages (server off).
    tonic_build::configure()
        .build_server(wants_server)
        .build_client(false)
        .file_descriptor_set_path(&fds_path)
        .compile_protos(&[&proto], &[&interfaces_proto])
        .expect("failed to compile the interface proto");

    let fds = std::fs::read(&fds_path).expect("read the generated FileDescriptorSet");
    let interfaces = interfaces_from_descriptor_set(&fds).expect("walk the FileDescriptorSet");
    let generator = RustGenerator;

    // Assemble the aggregator: one `proto` module (the tonic output + FILE_DESCRIPTOR_SET) plus,
    // for client/combined modes, each generated typed-client file, re-exported.
    let mut agg = String::new();
    let mut packages = std::collections::BTreeSet::new();
    let mut client_files = Vec::new();
    for iface in &interfaces {
        if wants_client {
            for (name, source) in generator.generate_client(iface) {
                std::fs::write(out_dir.join(&name), source)
                    .unwrap_or_else(|e| panic!("write generated {name}: {e}"));
                client_files.push(name);
            }
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
                     include_bytes!(concat!(env!(\"OUT_DIR\"), \"/{stem}.fds\"));\n\
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

    // Key the aggregator by the canonical proto PACKAGE (what `interface!("<package>")` names and
    // `tonic::include_proto!` uses), falling back to the file stem for a package-less proto. The fds
    // keeps its stem name (it's written by tonic before the package is known; only this aggregator,
    // which references it, needs to be addressable by package).
    let pkg_key = packages.iter().next().cloned().unwrap_or_else(|| stem.clone());
    std::fs::write(out_dir.join(format!("{pkg_key}_generated.rs")), agg)
        .unwrap_or_else(|e| panic!("write the {pkg_key}_generated.rs aggregator: {e}"));
}

/// **Device**: from a crate-local exporter config, generate the typed device wrapper + its
/// per-interface typed clients into `OUT_DIR` (aggregator `jumpstarter_device_generated.rs`,
/// pulled in by `jumpstarter_client::device!()`). Resolution is proto-only via the monorepo's
/// committed `interfaces/registry` + `interfaces/proto` (strict: an unresolvable node fails the
/// build — the config is the contract). A consumer crate's `build.rs` is just:
/// ```ignore
/// fn main() {
///     jumpstarter_codegen::build::exporter_device("exporter.yaml");
/// }
/// ```
pub fn exporter_device(config_rel: &str) {
    use jumpstarter_config::{DriverRegistry, ExporterConfig, YamlConfig};

    let manifest = PathBuf::from(env_var("CARGO_MANIFEST_DIR"));
    let repo_root = manifest
        .join("../..")
        .canonicalize()
        .expect("monorepo root not found (../.. from the crate)");
    let proto_root = repo_root.join("interfaces/proto");
    let registry_dir = repo_root.join("interfaces/registry");
    let config_path = manifest.join(config_rel);
    let out_dir = PathBuf::from(env_var("OUT_DIR"));

    println!("cargo:rerun-if-changed={}", config_path.display());
    println!("cargo:rerun-if-changed={}", registry_dir.display());

    let config = ExporterConfig::load(&config_path)
        .unwrap_or_else(|e| panic!("load {}: {e}", config_path.display()));
    let registry = DriverRegistry::load_path(&registry_dir)
        .unwrap_or_else(|e| panic!("load registry {}: {e}", registry_dir.display()));
    let device = crate::resolver::resolve_device(&config, &registry, &proto_root, true)
        .unwrap_or_else(|e| panic!("resolve {}: {e}", config_path.display()));

    // The generated Rust clients reference `crate::proto` — one proto package per crate for now
    // (matching `interface!`); a multi-package device needs per-package module wiring.
    let packages: std::collections::BTreeSet<String> = device
        .interfaces
        .values()
        .map(|i| i.fqn.rsplit_once('.').expect("service FQN").0.to_string())
        .collect();
    assert!(
        packages.len() <= 1,
        "exporter_device: all interfaces must share one proto package for now, got {packages:?}"
    );

    // Stock prost messages (no server, no grpc client) for every referenced proto.
    let protos: Vec<PathBuf> = device
        .interfaces
        .values()
        .map(|i| {
            let p = proto_root.join(&i.proto_path);
            println!("cargo:rerun-if-changed={}", p.display());
            p
        })
        .collect();
    let fds_path = out_dir.join("device.fds");
    tonic_build::configure()
        .build_server(false)
        .build_client(false)
        .file_descriptor_set_path(&fds_path)
        .compile_protos(&protos, &[&proto_root])
        .expect("failed to compile the device's interface protos");

    let (files, warnings) = crate::device::generate_device(
        &device,
        "rust",
        &crate::device::DeviceOptions::default(),
    )
    .expect("generate device wrapper");
    for warning in &warnings {
        println!("cargo:warning=jumpstarter device: {warning}");
    }

    let mut agg = String::new();
    if let Some(pkg) = packages.iter().next() {
        let _ = writeln!(
            agg,
            "pub mod proto {{\n    \
                 tonic::include_proto!(\"{pkg}\");\n    \
                 pub const FILE_DESCRIPTOR_SET: &[u8] =\n        \
                     include_bytes!(concat!(env!(\"OUT_DIR\"), \"/device.fds\"));\n\
             }}"
        );
    }
    for (i, (name, source)) in files.iter().enumerate() {
        std::fs::write(out_dir.join(name), source)
            .unwrap_or_else(|e| panic!("write generated {name}: {e}"));
        let _ = writeln!(
            agg,
            "#[doc(hidden)]\nmod __jmp_device_{i} {{\n    \
                 include!(concat!(env!(\"OUT_DIR\"), \"/{name}\"));\n\
             }}\npub use __jmp_device_{i}::*;"
        );
    }
    std::fs::write(out_dir.join("jumpstarter_device_generated.rs"), agg)
        .expect("write the jumpstarter_device_generated.rs aggregator");
}

fn env_var(key: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| panic!("{key} not set (run from a cargo build script)"))
}
