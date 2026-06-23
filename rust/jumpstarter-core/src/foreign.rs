//! `ForeignDriver` — the in-process driver host.
//!
//! Adapts a binding-agnostic [`DriverApi`] (implemented in Python/Kotlin/C) to the
//! exporter's proto-typed [`DriverBackend`] seam, so the Rust exporter serves driver
//! calls/streams by calling the foreign host *in process* instead of proxying gRPC to a
//! subprocess. This is the in-process counterpart to the subprocess `ChannelBackend` — same
//! behavior, no second process and no second gRPC stack.
//!
//! Rust owns everything mechanical here: the value codec (`args`/`result` proto `Value`
//! ⇄ JSON via [`crate::codec`]), `DriverReport` assembly (via [`crate::report`]), the
//! exception→`Status` mapping, and the router framing (DATA/GOAWAY + the trailing
//! `ABORTED "RouterStream: aclose"` teardown). The foreign side only runs driver method
//! bodies and produces/consumes raw bytes + JSON.

use std::sync::Arc;

use jumpstarter_transport::{DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen};
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, FrameType, GetReportResponse, LogStreamResponse,
    StreamResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::{AsciiMetadataValue, MetadataKey, MetadataMap};
use tonic::Status;

use jumpstarter_compression::{Codec, Compressor, Decompressor};

use crate::codec;
use crate::error::DriverCallError;
use crate::host::DriverApi;
use crate::report::assemble_report;

/// Mirrors `driver/base.py:27-36`: `JMP_DISABLE_COMPRESSION=1` turns the wire codec off
/// entirely (the host then advertises no accept and runs pure passthrough).
fn compression_disabled() -> bool {
    std::env::var(jumpstarter_config::env::JMP_DISABLE_COMPRESSION).as_deref() == Ok("1")
}

/// Parse the requested wire codec out of an already-materialized `request` stream-open JSON.
///
/// A driver `@exportstream` request carries `method` and no `x_jmp_content_encoding`, so this
/// returns `None` there → pure passthrough (consoles/serial/network are unchanged). Only a
/// resource request with a recognized `x_jmp_content_encoding` (and compression enabled) yields
/// a codec.
fn parse_codec(request_json: &str) -> Option<Codec> {
    if compression_disabled() {
        return None;
    }
    serde_json::from_str::<serde_json::Value>(request_json)
        .ok()
        .and_then(|v| {
            v.get("x_jmp_content_encoding")
                .and_then(|e| e.as_str())
                .map(String::from)
        })
        .and_then(|s| Codec::from_wire(&s))
}

/// Channel buffer for the in-process result/frame pumps. Small: each item is one driver
/// result or one stream frame, and the foreign side is GIL-bounded anyway.
const PUMP_BUFFER: usize = 16;

/// Wraps a [`DriverApi`] as a [`DriverBackend`].
pub struct ForeignDriver {
    api: Arc<dyn DriverApi>,
}

impl ForeignDriver {
    pub fn new(api: Arc<dyn DriverApi>) -> Self {
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
impl DriverBackend for ForeignDriver {
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
        tracing::debug!(method = %req.method, uuid = %uuid, "streaming_driver_call open");
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
                        tracing::trace!(uuid = %uuid, "streaming_driver_call item");
                        let item = match codec::json_result_to_value(&result_json) {
                            Ok(value) => Ok(StreamingDriverCallResponse {
                                uuid: uuid.clone(),
                                result: Some(value),
                            }),
                            Err(e) => {
                                tracing::debug!(uuid = %uuid, error = %e, "streaming_driver_call result encode failed");
                                Err(status_from(e.into()))
                            }
                        };
                        let is_err = item.is_err();
                        if tx.send(item).await.is_err() || is_err {
                            break;
                        }
                    }
                    Ok(None) => {
                        tracing::trace!(uuid = %uuid, "streaming_driver_call EOF");
                        break;
                    }
                    Err(e) => {
                        tracing::debug!(uuid = %uuid, error = %e, "streaming_driver_call driver error");
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
        // The requested wire codec (None for driver `@exportstream` → pure passthrough). Rust
        // ALWAYS supports the four codecs, so a recognized request is always accepted.
        let codec = parse_codec(&request_json);
        tracing::trace!(request = %request_json, codec = ?codec, "router stream open");
        let opened = self
            .api
            .open_stream(request_json)
            .await
            .map_err(status_from)?;

        // Uplink pump (client -> driver) = DECOMPRESS: when a codec is active, the client sent
        // compressed bytes; decompress them so the language driver receives RAW bytes (the same
        // single-side codec the Python driver did via `_resource_from_client_stream`). With no
        // codec this is verbatim relay (consoles/serial unchanged).
        let write_chan = opened.channel.clone();
        let mut uplink = uplink;
        let mut dec = codec.map(Decompressor::new);
        // Whether any DATA actually reached the decompressor. A dump has an EMPTY uplink (the driver
        // only produces the downlink), so finalizing an unfed decompressor must be skipped — some
        // codecs error on "finish with no input", and there is no tail to flush.
        let mut up_fed = false;
        tokio::spawn(async move {
            while let Some(frame) = uplink.next().await {
                match FrameType::try_from(frame.frame_type) {
                    Ok(FrameType::Data) => match dec.as_mut() {
                        Some(d) => {
                            up_fed = true;
                            match d.decompress(&frame.payload) {
                                Ok(raw) => {
                                    tracing::trace!(bytes = raw.len(), "uplink decompressed chunk");
                                    if !raw.is_empty() {
                                        if let Err(e) = write_chan.write(raw).await {
                                            tracing::debug!(error = %e, "uplink write to driver failed");
                                            break;
                                        }
                                    }
                                }
                                // A codec error is unrecoverable for this stream — break the pump and
                                // let the channel close propagate as the normal teardown.
                                Err(e) => {
                                    tracing::error!(error = %e, "uplink decompress failed; tearing down stream");
                                    break;
                                }
                            }
                        }
                        None => {
                            tracing::trace!(bytes = frame.payload.len(), "uplink passthrough chunk");
                            if let Err(e) = write_chan.write(frame.payload).await {
                                tracing::debug!(error = %e, "uplink write to driver failed");
                                break;
                            }
                        }
                    },
                    Ok(FrameType::Goaway) => {
                        tracing::trace!("uplink GOAWAY; half-closing");
                        // Drain the decompressor tail (gzip residual) BEFORE half-closing, so the
                        // driver sees the complete raw payload — but only if data actually flowed.
                        if up_fed {
                            if let Some(d) = dec.take() {
                                match d.finish() {
                                    Ok(tail) => {
                                        if !tail.is_empty() {
                                            if let Err(e) = write_chan.write(tail).await {
                                                tracing::debug!(error = %e, "uplink tail write to driver failed");
                                            }
                                        }
                                    }
                                    Err(e) => {
                                        tracing::debug!(error = %e, "uplink decompressor finish failed");
                                    }
                                }
                            }
                        }
                        if let Err(e) = write_chan.close_write().await {
                            tracing::debug!(error = %e, "uplink close_write failed");
                        }
                        break;
                    }
                    // PING / unknown are dropped without forwarding (router.rs::classify).
                    _ => {}
                }
            }
        });

        // Downlink pump (driver -> client) = COMPRESS: when a codec is active, the driver produced
        // RAW bytes; compress them on the way out so the client decompresses. Order on EOF is
        // load-bearing: trailing compressor footer (final DATA) → GOAWAY → ABORTED "aclose"
        // (mirrors Python `_flush()` in `send_eof`/`aclose`).
        let (tx, rx) = mpsc::channel::<Result<StreamResponse, Status>>(PUMP_BUFFER);
        let read_chan = opened.channel.clone();
        let mut enc = codec.map(Compressor::new);
        // Whether the driver produced any downlink DATA. A FLASH has an empty downlink, so the
        // compressor must NOT be finalized — emitting an empty-frame footer there would surface a
        // spurious DATA frame the client writes back into the (read-only) source via its
        // bidirectional `forward_stream`. Only a DUMP (driver sends data) gets a footer.
        let mut dn_fed = false;
        tokio::spawn(async move {
            loop {
                match read_chan.read().await {
                    Ok(Some(payload)) => {
                        dn_fed = true;
                        let out = match enc.as_mut() {
                            Some(e) => match e.compress(&payload) {
                                Ok(z) => z,
                                Err(err) => {
                                    let _ = tx
                                        .send(Err(Status::internal(format!(
                                            "compression error: {err}"
                                        ))))
                                        .await;
                                    break;
                                }
                            },
                            None => payload,
                        };
                        // An empty compressed chunk is a no-op (the encoder is still buffering).
                        if out.is_empty() && enc.is_some() {
                            continue;
                        }
                        let frame = StreamResponse {
                            payload: out,
                            frame_type: FrameType::Data as i32,
                        };
                        if tx.send(Ok(frame)).await.is_err() {
                            break;
                        }
                    }
                    Ok(None) => {
                        // Flush the compressor footer as a FINAL DATA frame before GOAWAY — but only
                        // if the driver actually produced downlink data (a flash's empty downlink
                        // emits nothing).
                        if dn_fed {
                            if let Some(e) = enc.take() {
                                if let Ok(tail) = e.finish() {
                                    if !tail.is_empty() {
                                        let frame = StreamResponse {
                                            payload: tail,
                                            frame_type: FrameType::Data as i32,
                                        };
                                        let _ = tx.send(Ok(frame)).await;
                                    }
                                }
                            }
                        }
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

        // Rust is the source of truth for accept negotiation: ALWAYS advertise the accepted
        // encoding whenever a supported codec was requested (so the client never compresses).
        // This makes host.py's accept advertisement dead. Preserves the empty-vs-present
        // negotiation tunnel.rs relays byte-for-byte.
        let mut meta = opened.initial_metadata;
        if let Some(c) = &codec {
            meta.push(("x_jmp_accept_encoding".into(), c.as_wire().into()));
        }

        Ok(RouterStreamOpen {
            initial_metadata: to_metadata(meta),
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
    use crate::host::{DriverByteChannel, DriverResultStream, DriverStreamOpen};
    use jumpstarter_protocol::value;
    use jumpstarter_protocol::v1::StreamRequest;
    use serde_json::json;
    use std::collections::HashMap;
    use std::sync::Mutex;

    /// A foreign host that echoes driver-call args, streams a small countdown, and serves
    /// a one-shot byte channel — enough to exercise the codec, report, and framing paths.
    struct MockHost;

    #[async_trait::async_trait]
    impl DriverApi for MockHost {
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
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            Ok(Arc::new(Countdown {
                remaining: Mutex::new(3),
            }))
        }

        async fn open_stream(
            &self,
            _request_json: String,
        ) -> Result<DriverStreamOpen, DriverCallError> {
            Ok(DriverStreamOpen {
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
    impl DriverResultStream for Countdown {
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
    impl DriverByteChannel for OneShot {
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

    fn host() -> ForeignDriver {
        ForeignDriver::new(Arc::new(MockHost))
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

    // --- codec seam (resource flash/dump): uplink DECOMPRESS, downlink COMPRESS ----------

    /// A host whose `open_stream` reflects the request so the codec is parsed, and whose channel
    /// records the RAW bytes the driver received (uplink) and serves a fixed RAW payload (downlink).
    struct CodecHost {
        recorded: Arc<Mutex<Vec<u8>>>,
        downlink_payload: Vec<u8>,
    }

    #[async_trait::async_trait]
    impl DriverApi for CodecHost {
        async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError> {
            Ok(vec![])
        }
        async fn driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<String, DriverCallError> {
            unreachable!()
        }
        async fn streaming_driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            unreachable!()
        }
        async fn open_stream(
            &self,
            _request_json: String,
        ) -> Result<DriverStreamOpen, DriverCallError> {
            Ok(DriverStreamOpen {
                channel: Arc::new(RecordingChannel {
                    recorded: self.recorded.clone(),
                    downlink: Mutex::new(Some(self.downlink_payload.clone())),
                }),
                initial_metadata: vec![("resource".to_string(), "{}".to_string())],
            })
        }
    }

    struct RecordingChannel {
        recorded: Arc<Mutex<Vec<u8>>>,
        downlink: Mutex<Option<Vec<u8>>>,
    }
    #[async_trait::async_trait]
    impl DriverByteChannel for RecordingChannel {
        async fn read(&self) -> Result<Option<Vec<u8>>, DriverCallError> {
            Ok(self.downlink.lock().unwrap().take())
        }
        async fn write(&self, data: Vec<u8>) -> Result<(), DriverCallError> {
            self.recorded.lock().unwrap().extend_from_slice(&data);
            Ok(())
        }
        async fn close_write(&self) -> Result<(), DriverCallError> {
            Ok(())
        }
        async fn close(&self) -> Result<(), DriverCallError> {
            Ok(())
        }
    }

    /// gzip-compress a blob in one shot via the codec (what a client would send on the wire).
    fn gzip(raw: &[u8]) -> Vec<u8> {
        let mut c = Compressor::new(Codec::Gzip);
        let mut out = c.compress(raw).unwrap();
        out.extend(c.finish().unwrap());
        out
    }

    /// gzip-decompress a blob via the codec (what a client would do on read).
    fn gunzip(blob: &[u8]) -> Vec<u8> {
        let mut d = Decompressor::new(Codec::Gzip);
        let mut out = d.decompress(blob).unwrap();
        out.extend(d.finish().unwrap());
        out
    }

    #[tokio::test]
    async fn codec_uplink_decompresses_and_downlink_compresses_and_advertises_accept() {
        let recorded = Arc::new(Mutex::new(Vec::new()));
        let raw_image = b"the quick brown fox".repeat(500);
        let raw_dump = b"DUMP payload from the device".repeat(300);
        let host = ForeignDriver::new(Arc::new(CodecHost {
            recorded: recorded.clone(),
            downlink_payload: raw_dump.clone(),
        }));

        // Client sends gzip-compressed image bytes (split across two frames), then half-closes.
        let blob = gzip(&raw_image);
        let (mid, rest) = blob.split_at(blob.len() / 2);
        let (tx, rx) = mpsc::channel::<StreamRequest>(8);
        tx.send(StreamRequest {
            payload: mid.to_vec(),
            frame_type: FrameType::Data as i32,
        })
        .await
        .unwrap();
        tx.send(StreamRequest {
            payload: rest.to_vec(),
            frame_type: FrameType::Data as i32,
        })
        .await
        .unwrap();
        tx.send(StreamRequest {
            payload: Vec::new(),
            frame_type: FrameType::Goaway as i32,
        })
        .await
        .unwrap();
        drop(tx);

        let meta =
            AsciiMetadataValue::try_from("{\"uuid\":\"u1\",\"x_jmp_content_encoding\":\"gzip\"}")
                .unwrap();
        let opened = host
            .open_router_stream(meta, ReceiverStream::new(rx))
            .await
            .unwrap();

        // Rust ALWAYS advertises accept for a supported codec.
        assert_eq!(
            opened
                .initial_metadata
                .get("x_jmp_accept_encoding")
                .and_then(|v| v.to_str().ok()),
            Some("gzip")
        );

        // Drain the downlink: concatenate the compressed DATA frames, stop at GOAWAY.
        let mut downlink = opened.downlink;
        let mut compressed_down = Vec::new();
        loop {
            match downlink.next().await {
                Some(Ok(frame)) if frame.frame_type == FrameType::Data as i32 => {
                    compressed_down.extend(frame.payload)
                }
                Some(Ok(_)) => break, // GOAWAY
                Some(Err(_)) => break, // trailing aborted
                None => break,
            }
        }

        // The driver received the RAW (decompressed) image.
        assert_eq!(*recorded.lock().unwrap(), raw_image);
        // The client would receive the RAW dump back after decompressing the downlink.
        assert_eq!(gunzip(&compressed_down), raw_dump);
    }

    /// Load a golden fixture produced by the venv Python compressors (shared with the
    /// jumpstarter-compression crate). Path is relative to this crate's manifest dir.
    fn python_golden_fixture(name: &str) -> Vec<u8> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../jumpstarter-compression/tests/fixtures")
            .join(name);
        std::fs::read(&path).unwrap_or_else(|e| panic!("read golden fixture {}: {e}", path.display()))
    }

    /// END-TO-END host decompression guard (silent-corruption): feed a *Python-zstd*
    /// golden frame through the real host router seam (the same `open_router_stream`
    /// path the live exporter uses) in multiple wire chunks, and assert the Python
    /// MockStorage-equivalent driver records EXACTLY the original uncompressed bytes.
    ///
    /// This is the airtight, no-cancellation-of-symmetric-bugs direction: the
    /// compressed bytes come from Python (backports.zstd, level 3), NOT from Rust's
    /// own compressor, so a wrong Rust decoder cannot accidentally agree with a
    /// wrong Rust encoder. Original/compressed fixtures are the same ones the
    /// jumpstarter-compression golden tests pin by sha256 in MANIFEST.json.
    #[tokio::test]
    async fn host_seam_decompresses_python_zstd_golden_to_original_bytes() {
        let original = python_golden_fixture("input_random.bin");
        let python_zstd = python_golden_fixture("random.zstd");
        // Guard the fixtures themselves so a bad checkout can't make this pass vacuously.
        assert_eq!(original.len(), 262144, "golden original len drifted");
        assert_ne!(python_zstd, original, "compressed fixture must differ from raw");

        let recorded = Arc::new(Mutex::new(Vec::new()));
        let host = ForeignDriver::new(Arc::new(CodecHost {
            recorded: recorded.clone(),
            downlink_payload: Vec::new(),
        }));

        // Send the Python zstd frame split across THREE odd-sized wire DATA frames to
        // exercise the streaming decoder's buffering across chunk boundaries, then GOAWAY.
        let (tx, rx) = mpsc::channel::<StreamRequest>(8);
        let n = python_zstd.len();
        let bounds = [0, n / 3, (2 * n) / 3, n];
        for w in bounds.windows(2) {
            tx.send(StreamRequest {
                payload: python_zstd[w[0]..w[1]].to_vec(),
                frame_type: FrameType::Data as i32,
            })
            .await
            .unwrap();
        }
        tx.send(StreamRequest {
            payload: Vec::new(),
            frame_type: FrameType::Goaway as i32,
        })
        .await
        .unwrap();
        drop(tx);

        let meta =
            AsciiMetadataValue::try_from("{\"uuid\":\"u1\",\"x_jmp_content_encoding\":\"zstd\"}")
                .unwrap();
        let opened = host
            .open_router_stream(meta, ReceiverStream::new(rx))
            .await
            .unwrap();

        // Host always advertises zstd accept-encoding for this supported codec.
        assert_eq!(
            opened
                .initial_metadata
                .get("x_jmp_accept_encoding")
                .and_then(|v| v.to_str().ok()),
            Some("zstd")
        );

        // Drain the (empty) downlink to let the uplink pump run to completion.
        let mut downlink = opened.downlink;
        while let Some(item) = downlink.next().await {
            if item.is_err() {
                break;
            }
        }

        // The driver received EXACTLY the original uncompressed bytes — i.e. the host
        // decompression seam delivered DECOMPRESSED data, byte-identical to the source.
        assert_eq!(
            *recorded.lock().unwrap(),
            original,
            "host seam did not deliver the original bytes from Python zstd"
        );
    }
}
