//! Server-side **backwards-compatibility** shim for the legacy `DriverCall`/`StreamingDriverCall`
//! RPCs (the `google.protobuf.Value` codec the native cutover removed from the client).
//!
//! New clients speak native per-interface gRPC exclusively. OLD clients that still issue the
//! generic `DriverCall(uuid, method, Value[] args)` are served here **for convenience**: the
//! exporter translates each legacy call into the *same* native dispatch the new clients use —
//! `Value[]` args → JSON → native request bytes → [`DriverBackend::forward_unary`] → native
//! response bytes → JSON → `Value` result. There is no separate legacy dispatch path in the
//! backends; this is a pure translation layer at the exporter's front door, built from the same
//! per-driver descriptors `GetReport` ships.

use std::str::FromStr;
use std::sync::Arc;

use bytes::Bytes;
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, DriverInstanceReport, StreamingDriverCallRequest,
    StreamingDriverCallResponse,
};
use jumpstarter_protocol::value;
use jumpstarter_transport::demux::DRIVER_UUID_KEY;
use jumpstarter_transport::{DriverBackend, ResponseStream};
use serde_json::Value as Json;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::{AsciiMetadataValue, MetadataMap};
use tonic::Status;

use jumpstarter_codec::dynamic::{decode_response, encode_request};
use jumpstarter_codec::native_table::{build_native_table, NativeRoute, NativeTable};

/// Translates legacy `DriverCall`/`StreamingDriverCall` requests into native per-driver gRPC
/// dispatch. Built once per lease from the driver reports (the `(uuid, method) → route` table),
/// then drives an `Arc<dyn DriverBackend>` via `forward_unary`/`forward_stream`.
pub struct LegacyDispatch {
    table: NativeTable,
}

impl LegacyDispatch {
    /// Build the dispatch table from the lease's driver reports (each carrying a self-contained
    /// `FileDescriptorSet`). A driver without a descriptor contributes no legacy route — its old
    /// calls then return `UNIMPLEMENTED`, exactly as a native call would.
    pub fn from_reports(reports: &[DriverInstanceReport]) -> Self {
        Self {
            table: build_native_table(reports),
        }
    }

    #[allow(clippy::result_large_err)]
    fn route(&self, uuid: &str, method: &str) -> Result<&NativeRoute, Status> {
        self.table.get(&(uuid.to_string(), method.to_string())).ok_or_else(|| {
            Status::unimplemented(format!(
                "no native route for {uuid}/{method} (legacy DriverCall): driver ships no descriptor"
            ))
        })
    }

    /// Translate a legacy unary `DriverCall` into a native `forward_unary` and back.
    pub async fn driver_call(
        &self,
        backend: &dyn DriverBackend,
        req: DriverCallRequest,
    ) -> Result<DriverCallResponse, Status> {
        let route = self.route(&req.uuid, &req.method)?;
        let body = encode_args(route, &req.args)?;
        let (_init, resp_bytes, _trailers) = backend
            .forward_unary(&route.path, uuid_metadata(&req.uuid)?, body)
            .await?;
        let result = decode_result(route, &resp_bytes)?;
        Ok(DriverCallResponse {
            uuid: req.uuid,
            result: Some(result),
        })
    }

    /// Translate a legacy `StreamingDriverCall` into a native `forward_stream`, mapping each native
    /// response message back to a legacy `Value` result.
    pub async fn streaming_driver_call(
        &self,
        backend: Arc<dyn DriverBackend>,
        req: StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status> {
        let route = self.route(&req.uuid, &req.method)?.clone();
        let body = encode_args(&route, &req.args)?;
        let (_init, mut stream) = backend
            .forward_stream(&route.path, uuid_metadata(&req.uuid)?, body)
            .await?;

        let uuid = req.uuid.clone();
        let (tx, rx) = mpsc::channel::<Result<StreamingDriverCallResponse, Status>>(16);
        tokio::spawn(async move {
            while let Some(item) = stream.next().await {
                let mapped = match item {
                    Ok(bytes) => {
                        decode_result(&route, &bytes).map(|result| StreamingDriverCallResponse {
                            uuid: uuid.clone(),
                            result: Some(result),
                        })
                    }
                    Err(status) => Err(status),
                };
                let is_err = mapped.is_err();
                if tx.send(mapped).await.is_err() || is_err {
                    break;
                }
            }
        });
        Ok(Box::pin(ReceiverStream::new(rx)))
    }
}

/// Legacy `Value[]` args → JSON array → native request message bytes.
#[allow(clippy::result_large_err)]
fn encode_args(route: &NativeRoute, args: &[prost_types::Value]) -> Result<Bytes, Status> {
    let args: Vec<Json> = value::decode_args(args);
    let args_json = serde_json::to_string(&args)
        .map_err(|e| Status::internal(format!("encode legacy args: {e}")))?;
    let body = encode_request(&route.input, &args_json).map_err(Status::from)?;
    Ok(Bytes::from(body))
}

/// Native response message bytes → JSON result → legacy `Value`.
#[allow(clippy::result_large_err)]
fn decode_result(route: &NativeRoute, bytes: &[u8]) -> Result<prost_types::Value, Status> {
    let result_json = decode_response(&route.output, bytes).map_err(Status::from)?;
    let json: Json = serde_json::from_str(&result_json)
        .map_err(|e| Status::internal(format!("decode native result: {e}")))?;
    Ok(value::encode_value(&json))
}

/// The `x-jumpstarter-driver-uuid` header the native demux routes by.
#[allow(clippy::result_large_err)]
fn uuid_metadata(uuid: &str) -> Result<MetadataMap, Status> {
    let mut md = MetadataMap::new();
    let value = AsciiMetadataValue::from_str(uuid)
        .map_err(|e| Status::invalid_argument(format!("invalid driver uuid {uuid:?}: {e}")))?;
    md.insert(DRIVER_UUID_KEY, value);
    Ok(md)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::driver::empty_interface_descriptor_set;
    use jumpstarter_protocol::v1::{GetReportResponse, LogStreamResponse};
    use jumpstarter_transport::{FrameUplink, RouterStreamOpen};
    use std::sync::Mutex;

    /// A backend that records the `forward_unary` it received and returns a canned response — proving
    /// the legacy shim translated the `DriverCall` into the right native call.
    struct RecordingBackend {
        got: Mutex<Option<(String, String, Vec<u8>)>>, // (path, uuid-header, body)
        response: Vec<u8>,
    }

    #[tonic::async_trait]
    impl DriverBackend for RecordingBackend {
        async fn get_report(&self) -> Result<GetReportResponse, Status> {
            unreachable!()
        }
        async fn forward_unary(
            &self,
            path: &str,
            metadata: MetadataMap,
            body: Bytes,
        ) -> Result<(MetadataMap, Bytes, MetadataMap), Status> {
            let uuid = metadata
                .get(DRIVER_UUID_KEY)
                .and_then(|v| v.to_str().ok())
                .unwrap_or_default()
                .to_string();
            *self.got.lock().unwrap() = Some((path.to_string(), uuid, body.to_vec()));
            Ok((
                MetadataMap::new(),
                Bytes::from(self.response.clone()),
                MetadataMap::new(),
            ))
        }
        async fn open_router_stream(
            &self,
            _request_meta: AsciiMetadataValue,
            _uplink: FrameUplink,
        ) -> Result<RouterStreamOpen, Status> {
            unreachable!()
        }
        async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
            unreachable!()
        }
    }

    /// A legacy `DriverCall(uuid, "on", [])` is translated into a native `forward_unary` on the
    /// driver's `PowerInterface/On` method (empty request), and the native (empty) response decodes
    /// back to a legacy `Value` result — old clients reach the driver through the native path.
    #[tokio::test]
    async fn legacy_driver_call_translates_to_native_forward_unary() {
        let descriptor = empty_interface_descriptor_set(
            "jumpstarter.interfaces.power.v1",
            "PowerInterface",
            &["On"],
        );
        let report = DriverInstanceReport {
            uuid: "power-1".into(),
            descriptor_set: Some(descriptor),
            ..Default::default()
        };
        let dispatch = LegacyDispatch::from_reports(&[report]);

        let backend = RecordingBackend {
            got: Mutex::new(None),
            response: Vec::new(), // On() returns Empty → empty response bytes
        };
        let resp = dispatch
            .driver_call(
                &backend,
                DriverCallRequest {
                    uuid: "power-1".into(),
                    method: "on".into(),
                    args: vec![],
                },
            )
            .await
            .expect("legacy driver_call dispatches");

        // Translated to the native On call: the PowerInterface path, the uuid header, empty body.
        let (path, uuid, body) = backend.got.lock().unwrap().clone().unwrap();
        assert_eq!(path, "/jumpstarter.interfaces.power.v1.PowerInterface/On");
        assert_eq!(uuid, "power-1");
        assert!(body.is_empty(), "On() takes Empty → empty request bytes");

        // The empty native response decodes back to a legacy `Value` (null result).
        assert_eq!(resp.uuid, "power-1");
        assert!(resp.result.is_some());
    }

    /// A legacy call to a driver with no descriptor (no native route) is `UNIMPLEMENTED`.
    #[tokio::test]
    async fn legacy_driver_call_without_route_is_unimplemented() {
        let dispatch = LegacyDispatch::from_reports(&[DriverInstanceReport {
            uuid: "u".into(),
            descriptor_set: None,
            ..Default::default()
        }]);
        let backend = RecordingBackend {
            got: Mutex::new(None),
            response: Vec::new(),
        };
        let err = dispatch
            .driver_call(
                &backend,
                DriverCallRequest {
                    uuid: "u".into(),
                    method: "on".into(),
                    args: vec![],
                },
            )
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unimplemented);
    }
}
