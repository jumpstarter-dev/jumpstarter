//! Jumpstarter client runtime for Rust.
//!
//! Provides session management, driver discovery, UUID metadata interception,
//! and stream channels for communicating with Jumpstarter exporters under
//! `jmp shell`.

pub mod interceptor;
pub mod portforward;
pub mod report;
pub mod session;
pub mod stream_channel;

/// Generated protobuf/gRPC types for the Jumpstarter protocol.
pub mod proto {
    pub mod jumpstarter {
        pub mod v1 {
            tonic::include_proto!("jumpstarter.v1");
        }
    }
}

pub use interceptor::UuidInterceptor;
pub use portforward::TcpPortforwardAdapter;
pub use portforward::UdpPortforwardAdapter;
pub use report::DriverReport;
pub use session::ExporterSession;
pub use stream_channel::StreamChannel;
