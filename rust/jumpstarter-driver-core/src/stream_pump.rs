//! Shared codec-pump helpers for the byte-plane host seam.
//!
//! The in-process host ([`crate::foreign::ForeignDriver`]) ferries a driver's byte stream in both
//! directions, applying the negotiated wire codec at this single seam (uplink = DECOMPRESS so the
//! driver receives raw bytes; downlink = COMPRESS so the client decompresses). Both the legacy
//! `RouterService.Stream` path (`open_router_stream`) and the native bidi path (`forward_bidi`) run
//! the *same* codec sequencing — only their framing differs (`StreamRequest/StreamResponse` +
//! GOAWAY vs. `StreamData` + END_STREAM). Factoring the codec body here keeps the two paths from
//! diverging during the migration; once `RouterService` is retired only `forward_bidi` calls these.
//!
//! The load-bearing ordering: the decompressor tail is flushed **before** the write half-closes, and
//! the compressor footer is emitted as a final chunk **before** end-of-stream — so a driver/client
//! never sees a truncated compressed payload.

use std::sync::Arc;

use jumpstarter_compression::{Compressor, Decompressor};

use crate::host::DriverByteChannel;

/// Handle one inbound uplink payload chunk: decompress it (when a codec is active) or pass it
/// through, then write the RAW bytes to the driver channel. `up_fed` is set once any data reaches
/// the decompressor (so [`uplink_finish`] knows whether a tail flush is warranted). Returns
/// `Err(())` to signal the pump should stop without a clean finish (codec error or channel write
/// failure — the channel is already broken).
pub(crate) async fn uplink_chunk(
    dec: &mut Option<Decompressor>,
    up_fed: &mut bool,
    payload: Vec<u8>,
    chan: &Arc<dyn DriverByteChannel>,
) -> Result<(), ()> {
    match dec.as_mut() {
        Some(d) => {
            *up_fed = true;
            match d.decompress(&payload) {
                Ok(raw) => {
                    if !raw.is_empty() {
                        if let Err(e) = chan.write(raw).await {
                            tracing::debug!(error = %e, "uplink write to driver failed");
                            return Err(());
                        }
                    }
                    Ok(())
                }
                Err(e) => {
                    tracing::error!(error = %e, "uplink decompress failed; tearing down stream");
                    Err(())
                }
            }
        }
        None => {
            if let Err(e) = chan.write(payload).await {
                tracing::debug!(error = %e, "uplink write to driver failed");
                return Err(());
            }
            Ok(())
        }
    }
}

/// Finish the uplink on a **clean** end-of-stream: flush the decompressor tail (only if data
/// actually flowed — a dump has an empty uplink, and some codecs error on "finish with no input"),
/// then half-close the write side so the driver's read reaches EOF.
pub(crate) async fn uplink_finish(
    dec: Option<Decompressor>,
    up_fed: bool,
    chan: &Arc<dyn DriverByteChannel>,
) {
    if up_fed {
        if let Some(d) = dec {
            match d.finish() {
                Ok(tail) => {
                    if !tail.is_empty() {
                        if let Err(e) = chan.write(tail).await {
                            tracing::debug!(error = %e, "uplink tail write to driver failed");
                        }
                    }
                }
                Err(e) => tracing::debug!(error = %e, "uplink decompressor finish failed"),
            }
        }
    }
    if let Err(e) = chan.close_write().await {
        tracing::debug!(error = %e, "uplink close_write failed");
    }
}

/// Compress (when a codec is active) or pass through one outbound downlink chunk. `dn_fed` is set
/// once the driver produces any downlink data (a flash has an empty downlink, so its compressor must
/// never be finalized). Returns the bytes to emit, `Ok(None)` when the encoder buffered the chunk
/// (emit nothing), or `Err(msg)` on a codec error.
pub(crate) fn downlink_chunk(
    enc: &mut Option<Compressor>,
    dn_fed: &mut bool,
    payload: Vec<u8>,
) -> Result<Option<Vec<u8>>, String> {
    *dn_fed = true;
    match enc.as_mut() {
        Some(e) => match e.compress(&payload) {
            // An empty compressed chunk is a no-op (the encoder is still buffering).
            Ok(z) if z.is_empty() => Ok(None),
            Ok(z) => Ok(Some(z)),
            Err(err) => Err(format!("compression error: {err}")),
        },
        // Passthrough emits the chunk verbatim (even if empty — the driver never reads empty).
        None => Ok(Some(payload)),
    }
}

/// Finish the downlink on end-of-stream: the compressor footer (only if the driver produced data),
/// else nothing.
pub(crate) fn downlink_finish(enc: Option<Compressor>, dn_fed: bool) -> Option<Vec<u8>> {
    if dn_fed {
        if let Some(e) = enc {
            if let Ok(tail) = e.finish() {
                if !tail.is_empty() {
                    return Some(tail);
                }
            }
        }
    }
    None
}
