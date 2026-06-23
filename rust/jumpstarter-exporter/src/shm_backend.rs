//! Shared-memory transport for the local hub↔driver-host byte plane — the **default** path between
//! the hub and each per-driver host subprocess (that hop is always same-machine).
//!
//! The polyglot hub adds a second gRPC hop (hub→host subprocess) that a monolithic exporter doesn't
//! have: each bulk chunk pays prost encode/decode + h2 framing twice. This backend replaces that hop
//! with an SPSC shared-memory [`Ring`] in **both directions**, so it costs a memcpy:
//!   * **uplink** (client→hub→host→driver): client DATA payloads are diverted into an uplink ring;
//!     the host reads the ring instead of gRPC DATA frames.
//!   * **downlink** (driver→host→hub→client): the host diverts its driver output into a downlink
//!     ring (see `tunnel.rs`); this backend opens that ring **here at the hub**, reads it, and
//!     re-exposes normal [`StreamResponse`] frames + the trailing status, so everything above this
//!     backend — the hub's RouterServer and the (possibly remote) client — stays **pure gRPC** and
//!     never sees a ring. SHM is therefore fully contained in the hub↔host hop.
//!
//! This is the **only** supported hub↔driver-host byte-plane transport — there is no gRPC fallback
//! and no opt-out. The control RPCs (`get_report`/`driver_call`/…) still ride the inner gRPC
//! [`ChannelBackend`] over the host UDS; only the bulk router byte stream uses the ring. The host
//! side auto-activates purely on the presence of the [`SHM_UP_KEY`] metadata (only this backend sets
//! it), so no separate host flag is needed.

use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, FrameType, GetReportResponse, LogStreamResponse,
    StreamRequest, StreamResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use jumpstarter_shm::bridge::{RingReader, RingWriter};
use jumpstarter_shm::wire::{RING_CAP, RING_CHUNK, SHM_DOWN_KEY, SHM_UP_KEY};
use jumpstarter_shm::Ring;
use jumpstarter_transport::{
    ChannelBackend, DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen,
};
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::AsciiMetadataValue;
use tonic::transport::Channel;
use tonic::{Request, Status};

/// A [`DriverBackend`] that wraps a host gRPC [`Channel`] and routes a router stream's **uplink**
/// bytes through a shared-memory ring. Every other method delegates to a plain [`ChannelBackend`].
pub struct ShmChannelBackend {
    inner: ChannelBackend,
    channel: Channel,
}

impl ShmChannelBackend {
    pub fn new(channel: Channel) -> Self {
        Self {
            inner: ChannelBackend::new(channel.clone()),
            channel,
        }
    }
}

#[tonic::async_trait]
impl DriverBackend for ShmChannelBackend {
    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        self.inner.get_report().await
    }

    async fn driver_call(&self, req: DriverCallRequest) -> Result<DriverCallResponse, Status> {
        self.inner.driver_call(req).await
    }

    async fn streaming_driver_call(
        &self,
        req: StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status> {
        self.inner.streaming_driver_call(req).await
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        self.inner.log_stream().await
    }

    async fn open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        // 1. Create the uplink ring at a unique temp path; the host opens + unlinks it. SHM is the
        //    only supported byte-plane transport, so a ring failure is a hard error (no gRPC fallback).
        let path = std::env::temp_dir().join(format!("jmp-shm-{}.up", uuid::Uuid::new_v4()));
        let ring = Ring::create(&path, RING_CAP)
            .map_err(|e| Status::internal(format!("shm uplink ring create: {e}")))?;
        let mut writer = RingWriter::spawn(ring, 8);

        // 2. Drain the client uplink: DATA payloads → ring, GOAWAY / end → close ring (EOF). We
        //    keep consuming the uplink so client-side HTTP/2 flow control advances.
        let mut up = uplink;
        let cleanup_path = path.clone();
        tokio::spawn(async move {
            while let Some(frame) = up.next().await {
                if frame.frame_type == FrameType::Data as i32 {
                    if writer.send(frame.payload).await.is_err() {
                        break;
                    }
                } else if frame.frame_type == FrameType::Goaway as i32 {
                    break;
                }
            }
            writer.close(); // EOF to the host consumer
            // Best-effort: the host unlinks right after open (mmap survives unlink); this covers
            // the case where the host never opened the ring (open error before unlink).
            let _ = std::fs::remove_file(&cleanup_path);
        });

        // 3. Open the host gRPC stream carrying the ring path in metadata + an EMPTY uplink (the
        //    host reads bytes from the ring, not gRPC). The host still returns its initial metadata,
        //    downlink, and trailing status over gRPC.
        let (_tx, rx) = tokio::sync::mpsc::channel::<StreamRequest>(1);
        let mut host_req = Request::new(ReceiverStream::new(rx));
        host_req.metadata_mut().insert("request", request_meta);
        host_req.metadata_mut().insert(
            SHM_UP_KEY,
            AsciiMetadataValue::try_from(path.to_string_lossy().as_ref())
                .map_err(|_| Status::internal("shm ring path is not valid ascii metadata"))?,
        );
        let mut client = RouterServiceClient::new(self.channel.clone())
            .max_decoding_message_size(64 * 1024 * 1024)
            .max_encoding_message_size(64 * 1024 * 1024);
        let host_resp = client.stream(host_req).await?;
        let mut initial_metadata = host_resp.metadata().clone();

        // 4. Downlink: if the host diverted its driver output into a downlink ring (it does whenever
        //    the uplink was SHM — see `tunnel.rs`), consume that ring HERE at the hub and re-expose
        //    normal `StreamResponse` frames. The `SHM_DOWN_KEY` is stripped so nothing above this
        //    backend (the hub's RouterServer, the client) ever sees the ring — SHM stays contained in
        //    this hop. Without a downlink ring (fallback host), relay the gRPC downlink unchanged.
        let down_path = initial_metadata
            .get(SHM_DOWN_KEY)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned);
        let downlink: ResponseStream<StreamResponse> = match down_path {
            Some(path) => {
                initial_metadata.remove(SHM_DOWN_KEY);
                match Ring::open(std::path::Path::new(&path), RING_CAP) {
                    Ok(ring) => {
                        let _ = std::fs::remove_file(&path); // consumer unlinks; mmap survives
                        let mut reader = RingReader::spawn(ring, RING_CHUNK, 8);
                        let mut host_stream = host_resp.into_inner();
                        let (tx, rx) =
                            tokio::sync::mpsc::channel::<Result<StreamResponse, Status>>(16);
                        tokio::spawn(async move {
                            // Drain the ring → DATA frames.
                            while let Some(chunk) = reader.recv().await {
                                let frame = StreamResponse {
                                    payload: chunk,
                                    frame_type: FrameType::Data as i32,
                                };
                                if tx.send(Ok(frame)).await.is_err() {
                                    return;
                                }
                            }
                            // Ring EOF: forward a real trailing error if the host sent one over gRPC;
                            // otherwise synthesize the clean GOAWAY + ABORTED "aclose" the non-SHM
                            // downlink ends with, so the client sees identical end-of-stream framing.
                            let mut forwarded = false;
                            while let Some(item) = host_stream.next().await {
                                match item {
                                    Ok(frame) => {
                                        let _ = tx.send(Ok(frame)).await;
                                    }
                                    Err(status) => {
                                        forwarded = true;
                                        let _ = tx.send(Err(status)).await;
                                        break;
                                    }
                                }
                            }
                            if !forwarded {
                                let goaway = StreamResponse {
                                    payload: Vec::new(),
                                    frame_type: FrameType::Goaway as i32,
                                };
                                let _ = tx.send(Ok(goaway)).await;
                                let _ = tx.send(Err(Status::aborted("RouterStream: aclose"))).await;
                            }
                        });
                        Box::pin(ReceiverStream::new(rx))
                    }
                    Err(e) => {
                        tracing::warn!(error = %e, "shm downlink ring open failed; using gRPC downlink");
                        Box::pin(host_resp.into_inner())
                    }
                }
            }
            None => Box::pin(host_resp.into_inner()),
        };
        Ok(RouterStreamOpen {
            initial_metadata,
            downlink,
        })
    }
}
