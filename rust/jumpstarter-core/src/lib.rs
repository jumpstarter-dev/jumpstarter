//! Jumpstarter core facade — the stable, binding-agnostic Rust API that every language
//! binding (Python via UniFFI now; Kotlin/Java and C/C++ later) wraps.
//!
//! It owns the mechanical work the plan moves off the Python side: the value codec
//! ([`codec`]), `DriverReport` assembly ([`report`]), the driver-call error taxonomy
//! ([`error`]), and the binding-agnostic foreign-host seam ([`host`]). It carries **no**
//! FFI dependency (no uniffi/cbindgen/jni) — those live only in the per-binding crates,
//! which implement [`host::DriverApi`] and convert these DTOs to their native types.

pub mod driver;
pub mod dynamic_backend;
pub mod foreign;
pub mod host;
pub mod legacy;
pub mod report;
pub(crate) mod stream_pump;

// Transitional facade: the neutral protocol plumbing (value/dispatch codec, native dispatch
// table, error taxonomy, cross-boundary DTOs) now lives in `jumpstarter-codec`. Core re-exports
// the modules and symbols it previously owned so every existing consumer keeps compiling against
// `jumpstarter_core::{error, dto, dynamic, native_table, ...}` until a later phase migrates each
// onto the codec crate directly.
pub use jumpstarter_codec::{dto, dynamic, error, native_table};

// Transitional facade: the CLIENT side (the consumer `ClientSession`, the programmatic
// `ControllerSession`/`LeaseTransport`, and `resolve_driver_uuid`) now lives in `jumpstarter-client`.
// Core re-exports it so every existing client consumer (cli, mcp, harness, core-uniffi) keeps
// compiling against `jumpstarter_core::{ClientSession, ControllerSession, …}` until a later phase
// migrates each onto the client crate directly.
pub use jumpstarter_client::resolve_driver_uuid;
pub use jumpstarter_client::{
    ClientByteStream, ClientLogStream, ClientNativeStream, ClientResultStream, ClientSession,
};
pub use jumpstarter_client::{ControllerSession, LeaseTransport};
pub use driver::{Driver, NativeDriverBackend};
pub use dynamic_backend::{DynamicBackend, DRIVER_UUID_KEY};
pub use foreign::ForeignDriver;
pub use host::{
    DriverByteChannel, DriverApi, DriverResultStream, DriverStreamOpen,
};
pub use jumpstarter_codec::{
    decode_response, encode_request, export_name_for, DriverNode, DynamicMethod,
};
pub use jumpstarter_codec::{ControllerError, DriverCallError};
pub use report::assemble_report;
