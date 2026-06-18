//! The binding-agnostic foreign-host seam.
//!
//! `ForeignHostApi` is the JSON/bytes-based interface a foreign host (Python now,
//! Kotlin/C later) provides. Each per-binding crate implements it thinly:
//! `jumpstarter-core-uniffi` backs it with a UniFFI `#[uniffi::export(with_foreign)]`
//! `DriverHost`; `jumpstarter-core-capi` backs it with a C function-pointer vtable.
//! The exporter routes a lease's driver tree through a `dyn ForeignHostApi`, so neither
//! the exporter nor this facade knows or cares which language implements it.
//!
//! Note: the driver-call parameter is `method_name`, not `method` — a foreign-trait
//! param named `method` triggers a UniFFI 0.28 Python codegen bug (a generated local
//! shadows it). See the spike findings.

use std::sync::Arc;

use async_trait::async_trait;

use crate::dto::DriverNode;
use crate::error::DriverCallError;

/// The driver-level surface a foreign host exposes. Args/results are plain JSON strings
/// (Rust applies the proto-`Value` codec — see [`crate::codec`]).
#[async_trait]
pub trait ForeignHostApi: Send + Sync {
    /// Introspect the whole driver tree as a flat node list (Rust assembles the proto
    /// report from this).
    async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError>;

    /// Invoke an `@export` call. `args_json` is a JSON array; returns the JSON result.
    async fn driver_call(
        &self,
        uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<String, DriverCallError>;

    /// Invoke an `@export` streaming call; results are pulled JSON-at-a-time.
    async fn streaming_driver_call(
        &self,
        uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<Arc<dyn ForeignResultStream>, DriverCallError>;

    /// Open a bidirectional byte channel to an `@exportstream`/resource handle. The
    /// JSON request carries the target uuid + kind (`common/streams.py:14-33`).
    async fn open_stream(&self, request_json: String) -> Result<ForeignStreamOpen, DriverCallError>;
}

/// A pull-style stream of JSON results for `streaming_driver_call`.
#[async_trait]
pub trait ForeignResultStream: Send + Sync {
    /// Next JSON result, or `None` at end of stream.
    async fn next(&self) -> Result<Option<String>, DriverCallError>;
}

/// A bidirectional byte plane for one router `Stream`. Rust owns the wire framing
/// (DATA/GOAWAY); this carries only raw payloads.
#[async_trait]
pub trait ForeignByteChannel: Send + Sync {
    /// Next inbound payload, or `None` at EOF (Rust emits GOAWAY).
    async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError>;
    /// Write one payload toward the driver (awaits the host's bounded pipe → backpressure).
    async fn write(&self, data: Vec<u8>) -> Result<(), DriverCallError>;
    /// Signal client→driver EOF (the client half-closed).
    async fn close_write(&self) -> Result<(), DriverCallError>;
    /// Tear down. Idempotent.
    async fn close(&self) -> Result<(), DriverCallError>;
}

/// Result of [`ForeignHostApi::open_stream`]: the byte channel plus the resource
/// initial-metadata to relay before any downlink frame (`["resource",
/// "x_jmp_accept_encoding"]`; empty for driver streams).
pub struct ForeignStreamOpen {
    pub channel: Arc<dyn ForeignByteChannel>,
    pub initial_metadata: Vec<(String, String)>,
}
