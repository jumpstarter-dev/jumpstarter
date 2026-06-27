//! Build-time codegen for the `power` interface — NOTHING generated is committed.
//!
//! Two steps, both into `OUT_DIR`:
//!   1. `tonic_build` emits the stock `PowerInterface` **server** trait
//!      (`power_interface_server::PowerInterface`) the author implements, the prost message types,
//!      and the serialized `FILE_DESCRIPTOR_SET` (the single source-of-truth descriptor).
//!   2. `jumpstarter-codegen`'s `RustGenerator` consumes that `FILE_DESCRIPTOR_SET` and emits the
//!      typed `PowerClient` — so the only code committed in this crate is the author's driver
//!      implementation; the interface stubs and the client are fully generated during the build.
//!
//! The `google/protobuf/empty.proto` well-known type is resolved by the system `protoc`'s bundled
//! include path (the same path `jumpstarter-protocol/build.rs` relies on).

use std::path::PathBuf;

use jumpstarter_codegen::engine::interfaces_from_descriptor_set;
use jumpstarter_codegen::languages::rust::RustGenerator;
use jumpstarter_codegen::languages::LanguageGenerator;

fn main() {
    let crate_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let interfaces_proto = crate_dir
        .join("../../interfaces/proto")
        .canonicalize()
        .expect("interfaces/proto tree not found relative to the power example crate");
    let power_proto = interfaces_proto.join("jumpstarter/interfaces/power/v1/power.proto");

    let out_dir = PathBuf::from(std::env::var("OUT_DIR").expect("OUT_DIR set by cargo"));
    let fds_path = out_dir.join("power.fds");

    println!("cargo:rerun-if-changed={}", power_proto.display());

    // 1. Stock tonic/prost output + the FileDescriptorSet.
    tonic_build::configure()
        .build_server(true)
        .build_client(false)
        .file_descriptor_set_path(&fds_path)
        .compile_protos(&[power_proto], &[interfaces_proto])
        .expect("failed to compile the power interface proto");

    // 2. The typed client, generated from the FileDescriptorSet (not committed).
    let fds = std::fs::read(&fds_path).expect("read the generated FileDescriptorSet");
    let interfaces =
        interfaces_from_descriptor_set(&fds).expect("walk the power FileDescriptorSet");
    let generator = RustGenerator;
    for iface in &interfaces {
        for (name, source) in generator.generate_client(iface) {
            std::fs::write(out_dir.join(&name), source)
                .unwrap_or_else(|e| panic!("write generated {name}: {e}"));
        }
    }
}
