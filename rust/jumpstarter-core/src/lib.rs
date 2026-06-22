//! Jumpstarter core facade — the stable, binding-agnostic Rust API that every language
//! binding (Python via UniFFI now; Kotlin/Java and C/C++ later) wraps.
//!
//! It owns the mechanical work the plan moves off the Python side: the value codec
//! ([`codec`]), `DriverReport` assembly ([`report`]), the driver-call error taxonomy
//! ([`error`]), and the binding-agnostic foreign-host seam ([`host`]). It carries **no**
//! FFI dependency (no uniffi/cbindgen/jni) — those live only in the per-binding crates,
//! which implement [`host::DriverApi`] and convert these DTOs to their native types.

pub mod client;
pub mod codec;
pub mod controller;
pub mod driver;
pub mod dto;
pub mod error;
pub mod foreign;
pub mod host;
pub mod report;

pub use client::{ClientByteStream, ClientLogStream, ClientResultStream, ClientSession};
pub use controller::{ControllerSession, LeaseTransport};
pub use driver::{Driver, NativeDriverBackend};
pub use dto::DriverNode;
pub use error::{CodecError, ControllerError, DriverCallError};
pub use foreign::ForeignDriver;
pub use host::{
    DriverByteChannel, DriverApi, DriverResultStream, DriverStreamOpen,
};
pub use report::assemble_report;
