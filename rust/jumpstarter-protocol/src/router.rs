//! Router frame rules (spec §3.5 wire helpers; `streams/router.py:31-63`).
//!
//! Only `DATA` and `GOAWAY` are ever sent today; `GOAWAY` means end-of-stream and
//! `PING` is swallowed on receive. A Rust implementation MUST treat `GOAWAY` as EOF
//! and MUST NOT emit `PING`/`RST_STREAM` to current peers (spec §2.1).

use crate::v1::{FrameType, StreamRequest, StreamResponse};

/// Build an outbound `DATA` frame carrying `payload`.
pub fn data_frame(payload: Vec<u8>) -> StreamRequest {
    StreamRequest {
        payload,
        frame_type: FrameType::Data as i32,
    }
}

/// Build an outbound `GOAWAY` frame (end-of-stream).
pub fn goaway_frame() -> StreamRequest {
    StreamRequest {
        payload: Vec::new(),
        frame_type: FrameType::Goaway as i32,
    }
}

/// What the receiver should do with an inbound frame.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FrameAction {
    /// Forward these bytes to the peer stream.
    Payload(Vec<u8>),
    /// End of stream (`GOAWAY`).
    Eof,
    /// Ignore this frame without forwarding (`PING` / unrecognized).
    Drop,
}

/// Classify an inbound `StreamResponse` per the Python receive path
/// (`streams/router.py:47-63`): `DATA` yields its payload, `GOAWAY` is EOF, `PING`
/// and anything unrecognized are dropped without forwarding.
pub fn classify(frame: StreamResponse) -> FrameAction {
    match FrameType::try_from(frame.frame_type) {
        Ok(FrameType::Data) => FrameAction::Payload(frame.payload),
        Ok(FrameType::Goaway) => FrameAction::Eof,
        Ok(FrameType::Ping) => FrameAction::Drop,
        // RST_STREAM and any unknown value are ignored.
        _ => FrameAction::Drop,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_data_and_goaway() {
        let d = data_frame(b"hello".to_vec());
        assert_eq!(d.frame_type, FrameType::Data as i32);
        assert_eq!(d.payload, b"hello");

        let g = goaway_frame();
        assert_eq!(g.frame_type, FrameType::Goaway as i32);
        assert!(g.payload.is_empty());
    }

    fn resp(frame_type: FrameType, payload: &[u8]) -> StreamResponse {
        StreamResponse {
            payload: payload.to_vec(),
            frame_type: frame_type as i32,
        }
    }

    #[test]
    fn classifies_frames() {
        assert_eq!(
            classify(resp(FrameType::Data, b"abc")),
            FrameAction::Payload(b"abc".to_vec())
        );
        assert_eq!(classify(resp(FrameType::Goaway, b"")), FrameAction::Eof);
        assert_eq!(classify(resp(FrameType::Ping, b"")), FrameAction::Drop);
        assert_eq!(classify(resp(FrameType::RstStream, b"")), FrameAction::Drop);
        // Unknown numeric frame type -> dropped.
        let unknown = StreamResponse {
            payload: b"x".to_vec(),
            frame_type: 99,
        };
        assert_eq!(classify(unknown), FrameAction::Drop);
    }
}
