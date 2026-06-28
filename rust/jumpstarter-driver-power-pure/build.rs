//! Build-time codegen for the `power` interface — **driver-only**.
//!
//! This is a PURE driver crate (host bin, no client), so it uses `build::driver_interface` to emit
//! ONLY the tonic server trait + messages + `FILE_DESCRIPTOR_SET` (no typed client). Nothing
//! generated here references the client side, so the host binary stays binary-pure.

fn main() {
    jumpstarter_codegen::build::driver_interface("jumpstarter/interfaces/power/v1/power.proto");
}
