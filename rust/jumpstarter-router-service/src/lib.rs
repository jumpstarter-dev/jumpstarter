//! The production Jumpstarter router service: a line-by-line behavioral port
//! of the Go router (`controller/internal/service/router_service.go` and
//! `router_support.go`), per specs/rust-core/06-streams-and-router.md §3 and
//! specs/rust-core/02-grpc-protocol.md §6.2/§9.
//!
//! `jumpstarter.v1.RouterService` has a single bidirectional `Stream` RPC:
//! the first authenticated caller for a given JWT `sub` (the rendezvous key)
//! parks; the second caller with the same `sub` pairs with it, and the router
//! then copies `payload` + `frame_type` verbatim in both directions without
//! interpreting any frame (GOAWAY half-close, PING, RST_STREAM and unknown
//! frame types all pass through untouched — this fidelity is load-bearing
//! for the Python peers, spec 06 §14).
//!
//! Module map:
//!
//! - [`auth`] — bearer extraction + HS256/384/512 router-token validation
//!   with golang-jwt v5 parity (`iat` validated only if present; any JWT
//!   failure is `INVALID_ARGUMENT "invalid jwt token"`).
//! - [`compression`] — the Go gzip codec registration
//!   (`cmd/router/main.go:34`): [`RouterService::into_server`] enables gzip
//!   on the tonic service, and [`compression::MirrorGzipLayer`] restores
//!   grpc-go's mirror-only response compression.
//! - [`service`] — the rendezvous map (`DashMap` port of Go's
//!   `pending sync.Map` with `LoadOrStore`/`CompareAndDelete` semantics) and
//!   the tonic `RouterService` implementation.
//! - [`forward`] — the two-pipe verbatim forwarder (`router_support.go`).
//! - [`keepalive`] — the `config.LoadGrpcConfiguration` port producing the
//!   gRPC server keepalive options the router binary applies.

pub mod auth;
pub mod compression;
pub mod forward;
pub mod keepalive;
pub mod service;

pub use service::RouterService;
