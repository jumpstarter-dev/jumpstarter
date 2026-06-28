//! Build-time codegen for the `power` interface — one call: compile the proto (tonic server stubs +
//! descriptor), generate the typed client + the `power_host!`/`power_client!` macros, and write the
//! `jumpstarter_generated.rs` aggregator the crate's `lib.rs` includes. NOTHING generated is committed.

fn main() {
    jumpstarter_codegen::build::driver_interface("jumpstarter/interfaces/power/v1/power.proto");
}
