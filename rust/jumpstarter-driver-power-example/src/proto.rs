//! The stock `tonic`/`prost` output for the power interface, included from `OUT_DIR`.
//!
//! `build.rs` runs `tonic_build` over `interfaces/.../power.proto`, emitting the package module
//! (the `PowerInterface` server trait under `power_interface_server`, the `PowerReading` message,
//! etc.) and the serialized `FILE_DESCRIPTOR_SET`. The generated `PowerBackend`/`PowerClient`
//! reference this module as `crate::proto`.

// The generated package module: `tonic::include_proto!` pulls in everything declared in
// `package jumpstarter.interfaces.power.v1` (the `PowerInterface` server trait + message types).
tonic::include_proto!("jumpstarter.interfaces.power.v1");

/// The self-contained serialized `FileDescriptorSet` for the power interface (the power proto plus
/// its `google/protobuf/empty.proto` dependency), written by `build.rs`. The generated
/// `PowerBackend` advertises this over `GetReport` so a client can decode the interface and drive
/// the driver over the native wire — the single descriptor source of truth.
pub const FILE_DESCRIPTOR_SET: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/power.fds"));
