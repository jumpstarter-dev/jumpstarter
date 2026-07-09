//! The rust-core demo's Act-3 Rust test lives in the examples tree so demo readers see exactly
//! what runs (`examples/rust-core-demo/act3-polyglot/power_test.rs`); this shim compiles it as
//! this crate's integration test — one source of truth, no copy to drift. (The JVM analog is the
//! external gradle test srcDir for the demo's `PowerNativeIT.kt`.)
#[path = "../../../examples/rust-core-demo/act3-polyglot/power_test.rs"]
mod power_test;
