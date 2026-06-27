//! Generated prost/tonic bindings for the Jumpstarter wire protocol.
//!
//! This crate is the single quarantine for generated code (spec
//! `09-rust-core-requirements.md` §3.5, ground rule 2): proto regeneration churns
//! exactly this crate and no downstream crate acquires a build-time protobuf
//! toolchain dependency.
//!
//! The four wire services live across two proto packages:
//! - [`jumpstarter::v1`] — `ControllerService`, `ExporterService`, `RouterService`
//!   (`protocol/proto/jumpstarter/v1/*.proto`)
//! - [`jumpstarter::client::v1`] — `ClientService`
//!   (`protocol/proto/jumpstarter/client/v1/client.proto`)
//!
//! Wire-level helpers (the `google.protobuf.Value` arg/result codec, the router
//! frame rules, and the exception↔status-code tables described in spec §2.4) will
//! be added here as pure functions over these generated types — see
//! `specs/rust-core/02-grpc-protocol.md` and `06-streams-and-router.md`.

pub mod router;
pub mod stream;
pub mod value;
pub use stream::{decode_stream_data, encode_stream_data, RESOURCE_OPEN_PATH};
pub use value::{decode_args, decode_value, encode_args, encode_value};

pub mod jumpstarter {
    /// `jumpstarter.v1` — controller/exporter/router services and shared types.
    pub mod v1 {
        // Generated code: suppress lints we do not control here (scoped to the
        // generated modules so hand-written code is still linted).
        #![allow(clippy::all)]
        #![allow(rustdoc::all)]
        tonic::include_proto!("jumpstarter.v1");
    }

    pub mod client {
        /// `jumpstarter.client.v1` — the resource-style client API service.
        pub mod v1 {
            #![allow(clippy::all)]
            #![allow(rustdoc::all)]
            tonic::include_proto!("jumpstarter.client.v1");
        }
    }
}

// Convenience re-exports at the crate root.
pub use jumpstarter::client::v1 as client_v1;
pub use jumpstarter::v1;
