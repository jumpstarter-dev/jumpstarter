//! Jumpstarter codec — the neutral protocol plumbing shared by the client and driver sides.
//!
//! It owns the descriptor-driven request/result codec ([`dynamic`]), the native dispatch
//! table ([`native_table`]), the driver-call/controller error taxonomy ([`error`]), and the
//! binding-agnostic DTOs ([`dto`]) that cross the foreign-host boundary. It carries **no**
//! transport, compression, driver-serving, or client machinery — both a pure client crate and
//! a pure driver crate can depend on it without pulling in the other side.

pub mod dto;
pub mod dynamic;
pub mod error;
pub mod native_table;

pub use dto::DriverNode;
pub use dynamic::{
    decode_response, encode_request, encode_result, export_name_for, request_bytes_to_args_json,
    DynamicMethod,
};
pub use error::{ControllerError, DriverCallError};
pub use native_table::{build_native_table, NativeRoute, NativeTable};
