//! The controller's gRPC service surface, mirroring
//! `controller/internal/service/{controller_service.go, router_support.go,
//! client/v1/client_service.go}`.
//!
//! Byte-for-byte wire compatibility is the contract (spec 02): the error
//! codes/strings in [`errors`] are matched by deployed Python/Rust/Java
//! clients, the [`listen_registry`] supersede/drain semantics preserve
//! lossless exporter reconnect, and [`status_stream`] reproduces the explicit
//! initial event + 10s heartbeat + dead-watch watchdog the exporter liveness
//! model depends on.

pub mod client_service;
pub mod controller_service;
pub mod dial;
pub mod errors;
pub mod listen_registry;
pub mod login;
pub mod server;
pub mod status_stream;
