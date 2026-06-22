//! The native (Rust) driver SDK: the author-facing [`Driver`] trait and the
//! [`NativeDriverBackend`] that serves it.
//!
//! A native driver is the Rust analogue of a Python `@export` driver: the author implements
//! [`Driver`] (its client class + `@export` methods), and the core serves it **directly** —
//! no FFI, no subprocess gRPC into another language. [`NativeDriverBackend`] assembles the
//! `DriverReport`, applies the value codec, and maps errors to `tonic::Status`, presenting the
//! same proto [`DriverBackend`] seam the hub federates. A native driver-host binary embeds this
//! to serve one driver on a UDS, exactly like the Python `jumpstarter.exporter_host`.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, DriverInstanceReport, GetReportResponse,
    LogStreamResponse, StreamingDriverCallResponse,
};
use jumpstarter_protocol::value;
use jumpstarter_transport::{DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen};
use serde_json::Value as Json;
use tonic::metadata::AsciiMetadataValue;
use tonic::Status;

use crate::error::DriverCallError;

const CLIENT_LABEL: &str = "jumpstarter.dev/client";
const NAME_LABEL: &str = "jumpstarter.dev/name";
/// The Composite the hub strips and re-roots (same shape a Python single-entry host produces).
const COMPOSITE_CLIENT: &str = "jumpstarter_driver_composite.client.CompositeClient";

/// A native Rust driver — the author-facing surface, mirroring a Python `@export` driver. The
/// core serves it directly: it assembles the report from [`Driver::client`]/[`Driver::methods`]
/// and dispatches [`Driver::call`] with the value codec applied around it.
#[async_trait]
pub trait Driver: Send + Sync {
    /// The client class (`jumpstarter.dev/client`) the client uses to drive this driver — e.g.
    /// `jumpstarter_driver_power.client.PowerClient` to be driven by the existing Python client.
    fn client(&self) -> String;

    /// Optional per-`@export`-method help text (`methods_description`).
    fn methods(&self) -> HashMap<String, String> {
        HashMap::new()
    }

    /// Invoke an `@export` method with decoded JSON args, returning a JSON result.
    async fn call(&self, method: &str, args: Vec<Json>) -> Result<Json, DriverCallError>;
}

/// Serves a single native [`Driver`] over the proto [`DriverBackend`] seam. The driver is wrapped
/// in a Composite root (the same shape a Python single-entry host produces) so the hub's
/// `RoutingBackend` stitches native and Python entries uniformly.
pub struct NativeDriverBackend {
    driver: Arc<dyn Driver>,
    driver_uuid: String,
    report: GetReportResponse,
}

impl NativeDriverBackend {
    /// Serve `driver` as the top-level entry named `entry_name`.
    pub fn new(entry_name: &str, driver: Arc<dyn Driver>) -> Self {
        let root_uuid = uuid::Uuid::new_v4().to_string();
        let driver_uuid = uuid::Uuid::new_v4().to_string();
        let reports = vec![
            DriverInstanceReport {
                uuid: root_uuid.clone(),
                parent_uuid: None,
                labels: HashMap::from([(CLIENT_LABEL.to_string(), COMPOSITE_CLIENT.to_string())]),
                description: None,
                methods_description: HashMap::new(),
            },
            DriverInstanceReport {
                uuid: driver_uuid.clone(),
                parent_uuid: Some(root_uuid),
                labels: HashMap::from([
                    (CLIENT_LABEL.to_string(), driver.client()),
                    (NAME_LABEL.to_string(), entry_name.to_string()),
                ]),
                description: None,
                methods_description: driver.methods(),
            },
        ];
        Self {
            driver,
            driver_uuid,
            report: GetReportResponse {
                reports,
                ..Default::default()
            },
        }
    }
}

#[tonic::async_trait]
impl DriverBackend for NativeDriverBackend {
    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        Ok(self.report.clone())
    }

    async fn driver_call(&self, req: DriverCallRequest) -> Result<DriverCallResponse, Status> {
        if req.uuid != self.driver_uuid {
            return Err(Status::unknown(format!("unknown driver uuid: {}", req.uuid)));
        }
        let args = value::decode_args(&req.args);
        let result = self.driver.call(&req.method, args).await?;
        Ok(DriverCallResponse {
            uuid: req.uuid,
            result: Some(value::encode_value(&result)),
        })
    }

    async fn streaming_driver_call(
        &self,
        _req: jumpstarter_protocol::v1::StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status> {
        Err(Status::unimplemented(
            "streaming driver calls are not yet supported by native Rust drivers",
        ))
    }

    async fn open_router_stream(
        &self,
        _request_meta: AsciiMetadataValue,
        _uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        Err(Status::unimplemented(
            "byte streams are not yet supported by native Rust drivers",
        ))
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        Ok(Box::pin(tokio_stream::empty()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// A minimal native driver: echoes its first arg, advertises a client class, and rejects
    /// unknown methods.
    struct Echo;

    #[async_trait]
    impl Driver for Echo {
        fn client(&self) -> String {
            "pkg.client.EchoClient".to_string()
        }
        fn methods(&self) -> HashMap<String, String> {
            HashMap::from([("echo".to_string(), "echo the first argument".to_string())])
        }
        async fn call(&self, method: &str, args: Vec<Json>) -> Result<Json, DriverCallError> {
            match method {
                "echo" => Ok(args.into_iter().next().unwrap_or(Json::Null)),
                other => Err(DriverCallError::Unimplemented(format!("no method {other}"))),
            }
        }
    }

    fn backend() -> NativeDriverBackend {
        NativeDriverBackend::new("thing", Arc::new(Echo))
    }

    #[tokio::test]
    async fn serves_wrapped_report() {
        let report = backend().get_report().await.unwrap();
        // A Composite root (no parent) + the driver leaf carrying its name + client labels.
        assert_eq!(report.reports.len(), 2);
        let root = report.reports.iter().find(|r| r.parent_uuid.is_none()).unwrap();
        assert_eq!(root.labels[CLIENT_LABEL], COMPOSITE_CLIENT);
        let leaf = report.reports.iter().find(|r| r.parent_uuid.is_some()).unwrap();
        assert_eq!(leaf.parent_uuid.as_deref(), Some(root.uuid.as_str()));
        assert_eq!(leaf.labels[NAME_LABEL], "thing");
        assert_eq!(leaf.labels[CLIENT_LABEL], "pkg.client.EchoClient");
        assert_eq!(leaf.methods_description["echo"], "echo the first argument");
    }

    #[tokio::test]
    async fn dispatches_calls_with_the_codec() {
        let backend = backend();
        let uuid = backend
            .report
            .reports
            .iter()
            .find(|r| r.parent_uuid.is_some())
            .unwrap()
            .uuid
            .clone();

        let resp = backend
            .driver_call(DriverCallRequest {
                uuid: uuid.clone(),
                method: "echo".to_string(),
                args: value::encode_args(&[json!("hi")]),
            })
            .await
            .unwrap();
        assert_eq!(value::decode_value(resp.result.as_ref().unwrap()), json!("hi"));

        // An unknown method maps to UNIMPLEMENTED via DriverCallError -> Status.
        let err = backend
            .driver_call(DriverCallRequest {
                uuid,
                method: "nope".to_string(),
                args: vec![],
            })
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unimplemented);

        // An unknown uuid is UNKNOWN.
        let err = backend
            .driver_call(DriverCallRequest {
                uuid: "bad".to_string(),
                method: "echo".to_string(),
                args: vec![],
            })
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unknown);
    }
}
