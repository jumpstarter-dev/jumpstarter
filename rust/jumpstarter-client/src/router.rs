//! Router stream bridge (`common/streams.py:connect_router_stream`,
//! `streams/common.py:forward_stream`, `streams/router.py`).
//!
//! Opens a `RouterService.Stream` to the dialed exporter and forwards raw bytes
//! both ways between it and a local connection: local reads become `DATA` frames,
//! inbound `DATA` payloads are written back, and `GOAWAY` (or stream end) means EOF
//! in either direction. The Rust side never parses the tunneled gRPC — it is a pure
//! byte bridge, so an unmodified gRPC client speaks end-to-end with the exporter.

use jumpstarter_config::TlsConfig;
use jumpstarter_protocol::router::{classify, data_frame, goaway_frame, FrameAction};
use jumpstarter_protocol::v1::router_service_client::RouterServiceClient;
use jumpstarter_protocol::v1::StreamRequest;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;

use crate::channel;
use crate::error::ClientError;

/// Read chunk size for the uplink (matches the spirit of the Python 32-item
/// object-stream buffers; the exact value is not wire-visible).
const CHUNK: usize = 32 * 1024;

/// Bridge `local` with a `RouterService.Stream` tunnel at `endpoint`, authenticated
/// with the per-stream `token`. Used by both the client (local socket → router) and
/// the exporter (router → session socket) — `DialResponse` and `ListenResponse`
/// carry the same `{router_endpoint, router_token}`.
///
/// Returns when both directions have closed.
///
/// The uplink (`local` → router) is started **before** awaiting the response
/// stream: the router only sends response headers once it has a frame to forward,
/// so the request body must be able to flow while we await — otherwise two bridges
/// whose peers are both waiting-to-send deadlock at `stream().await`.
pub async fn bridge<S>(
    local: S,
    endpoint: &str,
    token: &str,
    tls: &TlsConfig,
) -> Result<(), ClientError>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let svc = channel::connect_router(endpoint, token, tls).await?;
    let mut router = RouterServiceClient::new(svc);

    let (tx, rx) = mpsc::channel::<StreamRequest>(32);
    let outbound = ReceiverStream::new(rx);

    let (mut read_half, mut write_half) = tokio::io::split(local);

    // local -> router: DATA frames, GOAWAY on EOF. Spawned now so the request body
    // flows during `router.stream(...).await`.
    let uplink = tokio::spawn(async move {
        let mut buf = vec![0u8; CHUNK];
        loop {
            match read_half.read(&mut buf).await {
                Ok(0) => {
                    let _ = tx.send(goaway_frame()).await;
                    break;
                }
                Ok(n) => {
                    if tx.send(data_frame(buf[..n].to_vec())).await.is_err() {
                        break;
                    }
                }
                Err(_) => {
                    let _ = tx.send(goaway_frame()).await;
                    break;
                }
            }
        }
    });

    let mut inbound = router.stream(outbound).await?.into_inner();
    tracing::debug!("router stream opened");

    // router -> local: write DATA payloads, stop on GOAWAY / stream end.
    let mut downlink_bytes: u64 = 0;
    while let Some(frame) = inbound.next().await {
        match frame {
            Ok(f) => match classify(f) {
                FrameAction::Payload(p) => {
                    downlink_bytes += p.len() as u64;
                    if write_half.write_all(&p).await.is_err() {
                        break;
                    }
                }
                FrameAction::Eof => break,
                FrameAction::Drop => {}
            },
            Err(e) => {
                tracing::debug!(downlink_bytes, error = %e, "downlink: inbound error");
                break;
            }
        }
    }
    tracing::debug!(downlink_bytes, "downlink done");
    let _ = write_half.shutdown().await;
    uplink.abort();
    Ok(())
}
