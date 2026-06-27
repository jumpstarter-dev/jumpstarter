//! Native byte-stream framing — the `StreamData` envelope carried over native gRPC bidi
//! methods (`@exportstream` interface methods + [`ResourceService.Open`]).
//!
//! This is the native replacement for the [`crate::router`] `StreamRequest`/`StreamResponse`
//! framing. The opaque demux carries each frame as the *encoded* `StreamData` message bytes
//! (via the identity `BytesCodec`), so the encode/decode happens at the stream's ends — the
//! client byte stream and the host's `forward_bidi` serving path. There is no in-band
//! `frame_type`: end-of-stream is HTTP/2 END_STREAM + the gRPC trailer status (`OK` = clean
//! EOF, a non-`OK` status such as `DATA_LOSS` = truncation).
//!
//! `StreamData{bytes payload = 1}` is wire-identical wherever it is defined (the static
//! [`crate::v1::StreamData`] and the per-interface `StreamData` the Python descriptor builder
//! emits for `@exportstream`), so these helpers serve both.

use prost::bytes::Bytes;
use prost::Message as _;

use crate::v1::StreamData;

/// Encode a payload chunk into the wire bytes of one `StreamData` message — the body of a
/// single native byte-stream frame.
pub fn encode_stream_data(payload: Vec<u8>) -> Bytes {
    StreamData { payload }.encode_to_vec().into()
}

/// Decode one `StreamData` message's wire bytes back into its payload chunk.
pub fn decode_stream_data(bytes: &[u8]) -> Result<Vec<u8>, prost::DecodeError> {
    Ok(StreamData::decode(bytes)?.payload)
}

/// The well-known native method path for a resource byte channel
/// ([`crate::v1::ResourceService`] `Open`). The opaque demux routes it by the
/// `x-jumpstarter-driver-uuid` header; the host's serving path matches on this path to
/// reconstruct a resource `open_stream` request.
pub const RESOURCE_OPEN_PATH: &str = "/jumpstarter.v1.ResourceService/Open";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stream_data_round_trips() {
        let bytes = encode_stream_data(b"hello world".to_vec());
        assert_eq!(decode_stream_data(&bytes).unwrap(), b"hello world");
    }

    #[test]
    fn empty_payload_round_trips() {
        // A zero-length chunk encodes to empty bytes (proto3 default) and decodes back to empty.
        let bytes = encode_stream_data(Vec::new());
        assert!(bytes.is_empty());
        assert!(decode_stream_data(&bytes).unwrap().is_empty());
    }
}
