//! Build-time codegen for the `power` interface — **client-only**.
//!
//! This is a PURE client crate (client CLI bin, no driver), so it uses `build::client_interface` to
//! emit ONLY the prost messages + `FILE_DESCRIPTOR_SET` + the typed `PowerClient` (no server trait).
//! Nothing generated here references the driver side, so the client binary stays binary-pure.

fn main() {
    jumpstarter_codegen::build::client_interface("jumpstarter/interfaces/power/v1/power.proto");
}
