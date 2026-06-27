//! Jumpstarter core facade — the stable, binding-agnostic Rust API that every language
//! binding (Python via UniFFI now; Kotlin/Java and C/C++ later) wraps.
//!
//! It owns the mechanical work the plan moves off the Python side: the value codec
//! ([`codec`]), `DriverReport` assembly ([`report`]), the driver-call error taxonomy
//! ([`error`]), and the binding-agnostic foreign-host seam ([`host`]). It carries **no**
//! FFI dependency (no uniffi/cbindgen/jni) — those live only in the per-binding crates,
//! which implement [`host::DriverApi`] and convert these DTOs to their native types.

pub mod client;
pub mod controller;
pub mod driver;
pub mod dto;
pub mod dynamic;
pub mod dynamic_backend;
pub mod error;
pub mod foreign;
pub mod host;
pub mod legacy;
pub mod native_table;
pub mod report;
pub(crate) mod stream_pump;

pub use client::{
    ClientByteStream, ClientLogStream, ClientNativeStream, ClientResultStream, ClientSession,
};
pub use controller::{ControllerSession, LeaseTransport};
pub use driver::{Driver, NativeDriverBackend};
pub use dto::DriverNode;
pub use dynamic::{decode_response, encode_request, DynamicMethod};
pub use dynamic_backend::{export_name_for, DynamicBackend, DRIVER_UUID_KEY};
pub use error::{ControllerError, DriverCallError};
pub use foreign::ForeignDriver;
pub use host::{
    DriverByteChannel, DriverApi, DriverResultStream, DriverStreamOpen,
};
pub use report::assemble_report;
