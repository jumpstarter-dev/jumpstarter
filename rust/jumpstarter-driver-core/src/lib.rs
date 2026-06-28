//! Jumpstarter driver-dispatch core — the binding-agnostic driver-serving machinery.
//!
//! It owns the host side the plan keeps off the Python side: native and foreign driver
//! hosting ([`driver`], [`foreign`]), the binding-agnostic foreign-host seam ([`host`]),
//! dynamic gRPC dispatch ([`dynamic_backend`]), the legacy `DriverCall` dispatch
//! ([`legacy`]), and `DriverReport` assembly ([`report`]). It carries **no** client side
//! (no `ClientSession`) and **no** FFI dependency (no uniffi/cbindgen/jni) — those live
//! only in the per-binding crates, which implement [`host::DriverApi`] and convert the
//! [`jumpstarter_codec`] DTOs to their native types.

pub mod driver;
pub mod dynamic_backend;
pub mod foreign;
pub mod host;
pub mod legacy;
pub mod report;
pub(crate) mod stream_pump;

pub use driver::{Driver, NativeDriverBackend};
pub use dynamic_backend::{DynamicBackend, DRIVER_UUID_KEY};
pub use foreign::ForeignDriver;
pub use host::{DriverApi, DriverByteChannel, DriverResultStream, DriverStreamOpen};
pub use report::assemble_report;
