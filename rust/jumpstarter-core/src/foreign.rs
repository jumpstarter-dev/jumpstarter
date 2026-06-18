//! `ForeignDriverHost` — the in-process driver host.
//!
//! Adapts a binding-agnostic [`ForeignHostApi`] (implemented in Python/Kotlin/C) to the
//! exporter's proto-typed [`DriverHostBackend`] seam, so the Rust exporter serves driver
//! calls/streams by calling the foreign host *in process* instead of proxying gRPC to a
//! subprocess. This is the replacement for `SlimHostBackend` — same behavior, no second
//! process and no second gRPC stack.
//!
//! Rust owns everything mechanical here: the value codec (`args`/`result` proto `Value`
//! ⇄ JSON via [`crate::codec`]), `DriverReport` assembly (via [`crate::report`]), the
//! exception→`Status` mapping, and the router framing (DATA/GOAWAY + the trailing
//! `ABORTED "RouterStream: aclose"` teardown). The foreign side only runs driver method
//! bodies and produces/consumes raw bytes + JSON.

use std::sync::Arc;

use jumpstarter_exporter::backend::{
    DriverHostBackend, FrameUplink, ResponseStream, RouterStreamOpen,
};
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, FrameType, GetReportResponse, LogStreamResponse,
    StreamResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::{AsciiMetadataValue, MetadataKey, MetadataMap};
use tonic::Status;

use crate::codec;
use crate::error::DriverCallError;
use crate::host::ForeignHostApi;
use crate::report::assemble_report;

/// Channel buffer for the in-process result/frame pumps. Small: each item is one driver
/// result or one stream frame, and the foreign side is GIL-bounded anyway.
const PUMP_BUFFER: usize = 16;

/// Wraps a [`ForeignHostApi`] as a [`DriverHostBackend`].
pub struct ForeignDriverHost {
    api: Arc<dyn ForeignHostApi>,
}

impl ForeignDriverHost {
    pub fn new(api: Arc<dyn ForeignHostApi>) -> Self {
        Self { api }
    }
}

/// Map a foreign driver-call error to the `tonic::Status` remote clients observe — the
/// same code+message the Python `context.abort(...)` table produced.
fn status_from(e: DriverCallError) -> Status {
    match e {
        DriverCallError::Unimplemented(m) => Status::unimplemented(m),
        DriverCallError::InvalidArgument(m) => Status::invalid_argument(m),
        DriverCallError::DeadlineExceeded(m) => Status::deadline_exceeded(m),
        DriverCallError::NotFound(m) => Status::not_found(m),
        DriverCallError::Unknown(m) => Status::unknown(m),
    }
}

#[tonic::async_trait]
impl DriverHostBackend for ForeignDriverHost {
    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        let nodes = self.api.describe().await.map_err(status_from)?;
        Ok(assemble_report(&nodes))
    }

    async fn driver_call(&self, req: DriverCallRequest) -> Result<DriverCallResponse, Status> {
        let args_json = codec::args_to_json(&req.args).map_err(|e| status_from(e.into()))?;
        let result_json = self
            .api
            .driver_call(req.uuid.clone(), req.method, args_json)
            .await
            .map_err(status_from)?;
        let result = codec::json_result_to_value(&result_json).map_err(|e| status_from(e.into()))?;
        Ok(DriverCallResponse {
            uuid: req.uuid,
            result: Some(result),
        })
    }

    async fn streaming_driver_call(
        &self,
        req: StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status> {
        let args_json = codec::args_to_json(&req.args).map_err(|e| status_from(e.into()))?;
        let uuid = req.uuid.clone();
        let results = self
            .api
            .streaming_driver_call(req.uuid, req.method, args_json)
            .await
            .map_err(status_from)?;

        let (tx, rx) = mpsc::channel::<Result<StreamingDriverCallResponse, Status>>(PUMP_BUFFER);
        tokio::spawn(async move {
            loop {
                match results.next().await {
                    Ok(Some(result_json)) => {
                        let item = match codec::json_result_to_value(&result_json) {
                            Ok(value) => Ok(StreamingDriverCallResponse {
                                uuid: uuid.clone(),
                                result: Some(value),
                            }),
                            Err(e) => Err(status_from(e.into())),
                        };
                        let is_err = item.is_err();
                        if tx.send(item).await.is_err() || is_err {
                            break;
                        }
                    }
                    Ok(None) => break,
                    Err(e) => {
                        let _ = tx.send(Err(status_from(e))).await;
                        break;
                    }
                }
            }
        });
        Ok(Box::pin(ReceiverStream::new(rx)))
    }

    async fn open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        let request_json = request_meta
            .to_str()
            .map_err(|_| Status::unknown("malformed `request` stream metadata"))?
            .to_string();
        let opened = self
            .api
            .open_stream(request_json)
            .await
            .map_err(status_from)?;

        // Uplink pump (client -> driver): classify each frame and feed the byte channel.
        let write_chan = opened.channel.clone();
        let mut uplink = uplink;
        tokio::spawn(async move {
            while let Some(frame) = uplink.next().await {
                match FrameType::try_from(frame.frame_type) {
                    Ok(FrameType::Data) => {
                        if write_chan.write(frame.payload).await.is_err() {
                            break;
                        }
                    }
                    Ok(FrameType::Goaway) => {
                        let _ = write_chan.close_write().await;
                        break;
                    }
                    // PING / unknown are dropped without forwarding (router.rs::classify).
                    _ => {}
                }
            }
        });

        // Downlink pump (driver -> client): DATA frames, then on EOF a GOAWAY frame plus
        // the trailing ABORTED "RouterStream: aclose" status (Rust synthesizes what the
        // Python host emitted on aclose; tunnel.rs relays it verbatim).
        let (tx, rx) = mpsc::channel::<Result<StreamResponse, Status>>(PUMP_BUFFER);
        let read_chan = opened.channel.clone();
        tokio::spawn(async move {
            loop {
                match read_chan.read().await {
                    Ok(Some(payload)) => {
                        let frame = StreamResponse {
                            payload,
                            frame_type: FrameType::Data as i32,
                        };
                        if tx.send(Ok(frame)).await.is_err() {
                            break;
                        }
                    }
                    Ok(None) => {
                        let goaway = StreamResponse {
                            payload: Vec::new(),
                            frame_type: FrameType::Goaway as i32,
                        };
                        let _ = tx.send(Ok(goaway)).await;
                        let _ = tx.send(Err(Status::aborted("RouterStream: aclose"))).await;
                        break;
                    }
                    Err(e) => {
                        let _ = tx.send(Err(status_from(e))).await;
                        break;
                    }
                }
            }
            let _ = read_chan.close().await;
        });

        Ok(RouterStreamOpen {
            initial_metadata: to_metadata(opened.initial_metadata),
            downlink: Box::pin(ReceiverStream::new(rx)),
        })
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        // Driver/system LogStream aggregation is deferred (the exporter already streams
        // hook logs itself); an idle stream keeps the client merge well-defined.
        Ok(Box::pin(tokio_stream::empty()))
    }
}

/// Convert the foreign host's allow-listed initial metadata into a tonic `MetadataMap`
/// (tunnel.rs further filters to the resource keys before relaying to the client).
fn to_metadata(entries: Vec<(String, String)>) -> MetadataMap {
    let mut md = MetadataMap::new();
    for (key, value) in entries {
        if let (Ok(key), Ok(value)) = (
            MetadataKey::from_bytes(key.as_bytes()),
            AsciiMetadataValue::try_from(value),
        ) {
            md.insert(key, value);
        }
    }
    md
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dto::DriverNode;
    use crate::host::{ForeignByteChannel, ForeignResultStream, ForeignStreamOpen};
    use jumpstarter_protocol::value;
    use jumpstarter_protocol::v1::StreamRequest;
    use serde_json::json;
    use std::collections::HashMap;
    use std::sync::Mutex;

    /// A foreign host that echoes driver-call args, streams a small countdown, and serves
    /// a one-shot byte channel — enough to exercise the codec, report, and framing paths.
    struct MockHost;

    #[async_trait::async_trait]
    impl ForeignHostApi for MockHost {
        async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError> {
            Ok(vec![DriverNode::root(
                "u1",
                HashMap::from([("jumpstarter.dev/client".to_string(), "pkg.C".to_string())]),
                Some("mock".to_string()),
                HashMap::new(),
            )])
        }

        async fn driver_call(
            &self,
            _uuid: String,
            method_name: String,
            args_json: String,
        ) -> Result<String, DriverCallError> {
            if method_name == "boom" {
                return Err(DriverCallError::Unimplemented("nope".to_string()));
            }
            // Echo: return the args array verbatim as the result.
            Ok(args_json)
        }

        async fn streaming_driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<Arc<dyn ForeignResultStream>, DriverCallError> {
            Ok(Arc::new(Countdown {
                remaining: Mutex::new(3),
            }))
        }

        async fn open_stream(
            &self,
            _request_json: String,
        ) -> Result<ForeignStreamOpen, DriverCallError> {
            Ok(ForeignStreamOpen {
                channel: Arc::new(OneShot {
                    sent: Mutex::new(false),
                }),
                initial_metadata: vec![("resource".to_string(), "{}".to_string())],
            })
        }
    }

    struct Countdown {
        remaining: Mutex<u32>,
    }
    #[async_trait::async_trait]
    impl ForeignResultStream for Countdown {
        async fn next(&self) -> Result<Option<String>, DriverCallError> {
            let mut r = self.remaining.lock().unwrap();
            if *r == 0 {
                Ok(None)
            } else {
                *r -= 1;
                Ok(Some(format!("{}", *r)))
            }
        }
    }

    struct OneShot {
        sent: Mutex<bool>,
    }
    #[async_trait::async_trait]
    impl ForeignByteChannel for OneShot {
        async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
            let mut sent = self.sent.lock().unwrap();
            if *sent {
                Ok(None)
            } else {
                *sent = true;
                Ok(Some(b"hello".to_vec()))
            }
        }
        async fn write(&self, _data: Vec<u8>) -> Result<(), DriverCallError> {
            Ok(())
        }
        async fn close_write(&self) -> Result<(), DriverCallError> {
            Ok(())
        }
        async fn close(&self) -> Result<(), DriverCallError> {
            Ok(())
        }
    }

    fn host() -> ForeignDriverHost {
        ForeignDriverHost::new(Arc::new(MockHost))
    }

    #[tokio::test]
    async fn get_report_assembles_from_describe() {
        let report = host().get_report().await.unwrap();
        assert_eq!(report.reports.len(), 1);
        assert_eq!(report.reports[0].uuid, "u1");
        assert_eq!(report.reports[0].description.as_deref(), Some("mock"));
    }

    #[tokio::test]
    async fn driver_call_round_trips_the_codec() {
        let req = DriverCallRequest {
            uuid: "u1".to_string(),
            method: "echo".to_string(),
            args: value::encode_args(&[json!("on"), json!(42)]),
        };
        let resp = host().driver_call(req).await.unwrap();
        // Echo returns the args array as the result; ints collapse to f64 on the wire.
        let result = value::decode_value(resp.result.as_ref().unwrap());
        assert_eq!(result, json!(["on", 42.0]));
    }

    #[tokio::test]
    async fn driver_call_maps_errors_to_status() {
        let req = DriverCallRequest {
            uuid: "u1".to_string(),
            method: "boom".to_string(),
            args: vec![],
        };
        let status = host().driver_call(req).await.unwrap_err();
        assert_eq!(status.code(), tonic::Code::Unimplemented);
    }

    #[tokio::test]
    async fn streaming_call_yields_each_result() {
        let req = StreamingDriverCallRequest {
            uuid: "u1".to_string(),
            method: "count".to_string(),
            args: vec![],
        };
        let mut stream = host().streaming_driver_call(req).await.unwrap();
        let mut values = Vec::new();
        while let Some(item) = stream.next().await {
            values.push(value::decode_value(item.unwrap().result.as_ref().unwrap()));
        }
        assert_eq!(values, vec![json!(2.0), json!(1.0), json!(0.0)]);
    }

    #[tokio::test]
    async fn router_stream_frames_bytes_and_synthesizes_aclose() {
        // Empty uplink (client sends nothing, then half-closes).
        let (_tx, rx) = mpsc::channel::<StreamRequest>(1);
        let meta = AsciiMetadataValue::try_from("{\"uuid\":\"u1\"}").unwrap();
        let opened = host()
            .open_router_stream(meta, ReceiverStream::new(rx))
            .await
            .unwrap();

        // Initial metadata carries the resource key.
        assert!(opened.initial_metadata.get("resource").is_some());

        // Downlink: one DATA("hello"), then GOAWAY, then the trailing aclose status.
        let mut downlink = opened.downlink;
        let first = downlink.next().await.unwrap().unwrap();
        assert_eq!(first.frame_type, FrameType::Data as i32);
        assert_eq!(first.payload, b"hello");

        let goaway = downlink.next().await.unwrap().unwrap();
        assert_eq!(goaway.frame_type, FrameType::Goaway as i32);

        let trailing = downlink.next().await.unwrap().unwrap_err();
        assert_eq!(trailing.code(), tonic::Code::Aborted);
        assert_eq!(trailing.message(), "RouterStream: aclose");

        assert!(downlink.next().await.is_none());
    }
}
