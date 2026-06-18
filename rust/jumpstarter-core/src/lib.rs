//! Jumpstarter core facade — the stable, binding-agnostic Rust API that every language
//! binding (Python via UniFFI now; Kotlin/Java and C/C++ later) wraps.
//!
//! It owns the mechanical work the plan moves off the Python side: the value codec
//! ([`codec`]), `DriverReport` assembly ([`report`]), the driver-call error taxonomy
//! ([`error`]), and the binding-agnostic foreign-host seam ([`host`]). It carries **no**
//! FFI dependency (no uniffi/cbindgen/jni) — those live only in the per-binding crates,
//! which implement [`host::ForeignHostApi`] and convert these DTOs to their native types.

pub mod client;
pub mod codec;
pub mod dto;
pub mod error;
pub mod foreign;
pub mod host;
pub mod report;

pub use client::{ClientByteStream, ClientLogStream, ClientResultStream, ClientSession};
pub use dto::DriverNode;
pub use error::{CodecError, DriverCallError};
pub use foreign::ForeignDriverHost;
pub use host::{
    ForeignByteChannel, ForeignHostApi, ForeignResultStream, ForeignStreamOpen,
};
pub use report::assemble_report;
