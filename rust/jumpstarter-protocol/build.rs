//! Compile the Jumpstarter `.proto` files into prost/tonic Rust modules.
//!
//! Proto source of truth: the in-repo `protocol/proto/` tree. The Python and Go
//! generators currently pull from the *external* `jumpstarter-protocol` git repo
//! (python/buf.gen.yaml, controller/buf.gen.yaml) — this is spec open question 11
//! (09-rust-core-requirements.md §6 #11). We deliberately use the in-repo tree so
//! the Rust build is self-contained and matches the `path:line` citations in the
//! spec; if the external repo is declared authoritative, only this file changes.
//!
//! Third-party `google/api/*` protos (referenced by client/v1/client.proto for
//! REST/AIP annotations) are vendored under `proto/vendor/` at the exact
//! buf.lock commit. The `google/protobuf/*` well-known types are supplied by the
//! system `protoc` include path and mapped to `prost-types`.

use std::path::PathBuf;

fn main() {
    let crate_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));

    // In-repo proto tree (single source of truth — see module docs / open question 11).
    let repo_proto = crate_dir
        .join("../../protocol/proto")
        .canonicalize()
        .expect("protocol/proto tree not found relative to jumpstarter-protocol crate");

    // Vendored third-party protos (google/api/*).
    let vendor_proto = crate_dir.join("proto/vendor");

    let protos = [
        repo_proto.join("jumpstarter/v1/common.proto"),
        repo_proto.join("jumpstarter/v1/kubernetes.proto"),
        repo_proto.join("jumpstarter/v1/router.proto"),
        repo_proto.join("jumpstarter/v1/resource.proto"),
        repo_proto.join("jumpstarter/v1/jumpstarter.proto"),
        repo_proto.join("jumpstarter/client/v1/client.proto"),
    ];

    // Rebuild when any proto (ours or vendored) changes.
    println!("cargo:rerun-if-changed={}", repo_proto.display());
    println!("cargo:rerun-if-changed={}", vendor_proto.display());

    // Emit the serialized FileDescriptorSet (imports included) alongside the
    // generated modules; it backs the gRPC server-reflection service in the
    // controller-manager/router binaries (`FILE_DESCRIPTOR_SET` in lib.rs).
    let out_dir = PathBuf::from(std::env::var("OUT_DIR").expect("OUT_DIR set by cargo"));

    tonic_build::configure()
        .build_server(true)
        .build_client(true)
        .file_descriptor_set_path(out_dir.join("jumpstarter_descriptor.bin"))
        .compile_protos(&protos, &[repo_proto, vendor_proto])
        .expect("failed to compile Jumpstarter protocol definitions");
}
