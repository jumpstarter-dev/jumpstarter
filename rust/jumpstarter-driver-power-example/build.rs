//! Build-time codegen for the `power` interface — one call: compile the proto (tonic server stubs +
//! descriptor), generate the typed client + the `power_host!`/`power_client!` macros, and write the
//! `jumpstarter_generated.rs` aggregator the crate's `lib.rs` includes. NOTHING generated is committed.
//!
//! This is a **combined** crate (host bin + client bin), so it uses `build::interface` to emit BOTH
//! the server trait and the typed client.

fn main() {
    jumpstarter_codegen::build::interface("jumpstarter/interfaces/power/v1/power.proto");
}
