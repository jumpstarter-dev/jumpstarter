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
use jumpstarter_protocol::v1::{DialResponse, StreamRequest};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;

use crate::channel;
use crate::error::ClientError;

/// Read chunk size for the uplink (matches the spirit of the Python 32-item
/// object-stream buffers; the exact value is not wire-visible).
const CHUNK: usize = 32 * 1024;

/// Bridge `local` with the router tunnel described by `dial`.
///
/// Returns when both directions have closed.
pub async fn bridge<S>(local: S, dial: &DialResponse, tls: &TlsConfig) -> Result<(), ClientError>
where
    S: AsyncRead + AsyncWrite + Unpin,
{
    let svc = channel::connect_router(&dial.router_endpoint, &dial.router_token, tls).await?;
    let mut router = RouterServiceClient::new(svc);

    let (tx, rx) = mpsc::channel::<StreamRequest>(32);
    let outbound = ReceiverStream::new(rx);
    let mut inbound = router.stream(outbound).await?.into_inner();

    let (mut read_half, mut write_half) = tokio::io::split(local);

    // local -> router: DATA frames, GOAWAY on EOF.
    let uplink = async move {
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
    };

    // router -> local: write DATA payloads, stop on GOAWAY / stream end.
    let downlink = async move {
        while let Some(frame) = inbound.next().await {
            match frame {
                Ok(f) => match classify(f) {
                    FrameAction::Payload(p) => {
                        if write_half.write_all(&p).await.is_err() {
                            break;
                        }
                    }
                    FrameAction::Eof => break,
                    FrameAction::Drop => {}
                },
                Err(_) => break,
            }
        }
        // Signal EOF to the local peer.
        let _ = write_half.shutdown().await;
    };

    // Like Python's forward_stream task group, run both directions to completion.
    tokio::join!(uplink, downlink);
    Ok(())
}
