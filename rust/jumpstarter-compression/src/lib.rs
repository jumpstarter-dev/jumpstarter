//! Streaming compression codecs for the Jumpstarter resource byte plane.
//!
//! Rust is the sole owner of resource-stream (de)compression for all four
//! codecs (gzip / xz / bz2 / zstd). This crate provides synchronous,
//! chunk-driven [`Compressor`] / [`Decompressor`] types that mirror the Python
//! `CompressedStream.send`/`receive`/`_flush` pattern in
//! `streams/encoding.py`: one `compress(chunk)` per inbound chunk plus a single
//! terminal `finish()` (= Python `compressor.flush()`) at EOF; symmetric for
//! decompression, with the gzip tail flush surfaced via `Decompressor::finish`.
//!
//! The codecs are sync and runtime-agnostic — they are driven from the
//! exporter's existing spawned tokio pump tasks. Per-chunk work is CPU-bounded
//! and short; if profiling ever shows it blocking, the caller can wrap a call in
//! `spawn_blocking`, but no async surface is baked in here.
//!
//! ## Wire-format contract
//!
//! The contract with Python is *round-trip*, not byte-identity. Rust must
//! decompress Python's output to the original bytes, and Python must decompress
//! Rust's output back to the original. (Notably the gzip OS header byte differs:
//! Python emits the platform zlib value, flate2 emits `0xFF`. Both are valid
//! gzip and inter-decompress fine.) See the golden tests.

#![forbid(unsafe_code)]

use std::io::{self, Write};

use bzip2::write::{BzDecoder, BzEncoder};
use bzip2::Compression as BzCompression;
use flate2::write::{GzDecoder, GzEncoder};
use flate2::Compression as GzCompression;
use liblzma::write::{XzDecoder, XzEncoder};
use zstd::stream::write::{Decoder as ZstdDecoder, Encoder as ZstdEncoder};

/// The four supported wire codecs.
///
/// These mirror `jumpstarter.streams.encoding.Compression`. There is no `None`
/// variant: a missing/unrecognized codec is represented by `Option<Codec>` at
/// the call sites (passthrough), exactly like Python's `compress_stream(_, None)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Codec {
    /// gzip container (Python `zlib.compressobj(wbits=31)` / `decompressobj(wbits=47)`).
    Gzip,
    /// xz container with CRC64 check (Python `lzma.LZMACompressor()`).
    Xz,
    /// bzip2 container at level 9 (Python `bz2.BZ2Compressor()`).
    Bz2,
    /// zstd single frame at level 3, no checksum (Python `zstd.ZstdCompressor()`).
    Zstd,
}

impl Codec {
    /// Parse a codec from its `x_jmp_content_encoding` wire string.
    ///
    /// Returns `None` for an unrecognized value, which the caller treats as the
    /// passthrough (no-compression) path.
    pub fn from_wire(s: &str) -> Option<Codec> {
        match s {
            "gzip" => Some(Codec::Gzip),
            "xz" => Some(Codec::Xz),
            "bz2" => Some(Codec::Bz2),
            "zstd" => Some(Codec::Zstd),
            _ => None,
        }
    }

    /// The lowercase wire string for this codec.
    pub fn as_wire(self) -> &'static str {
        match self {
            Codec::Gzip => "gzip",
            Codec::Xz => "xz",
            Codec::Bz2 => "bz2",
            Codec::Zstd => "zstd",
        }
    }
}

// zstd level 3 is the Python backports/stdlib `ZstdCompressor()` default
// (`COMPRESSION_LEVEL_DEFAULT`); single frame, no checksum.
const ZSTD_LEVEL: i32 = 3;

/// Internal per-codec encoder state.
///
/// Each variant owns a write-based encoder whose sink is an owned `Vec<u8>`.
/// Writing a chunk into the encoder appends compressed bytes to that sink; we
/// then drain the sink to the caller. This is the exact analogue of Python's
/// `compressor.compress(item)` (per-chunk) followed by `compressor.flush()`
/// (terminal footer) — so frame boundaries and the trailing footer match.
enum Enc {
    Gzip(GzEncoder<Vec<u8>>),
    Xz(XzEncoder<Vec<u8>>),
    Bz2(BzEncoder<Vec<u8>>),
    // zstd's Encoder borrows nothing extra; `'static` lifetime on the sink.
    Zstd(ZstdEncoder<'static, Vec<u8>>),
}

/// A streaming, chunk-driven compressor for a single codec.
///
/// Feed inbound raw bytes with [`compress`](Compressor::compress); each call
/// emits whatever compressed bytes are available (possibly empty). At EOF call
/// [`finish`](Compressor::finish) once to emit the terminal footer
/// (gzip CRC+length, xz index, bz2 EOS, zstd epilogue).
pub struct Compressor(Enc);

impl Compressor {
    /// Create a compressor for `codec`, matching the Python default parameters
    /// (gzip level 6 / wbits=31, xz CRC64, bz2 level 9, zstd level 3).
    pub fn new(codec: Codec) -> Self {
        let enc = match codec {
            // flate2 `Compression::default()` == level 6 == Python wbits=31 default.
            Codec::Gzip => Enc::Gzip(GzEncoder::new(Vec::new(), GzCompression::default())),
            // liblzma `XzEncoder::new(_, preset)` uses FORMAT_XZ + CHECK_CRC64;
            // preset 6 matches `lzma.LZMACompressor()`.
            Codec::Xz => Enc::Xz(XzEncoder::new(Vec::new(), 6)),
            // bzip2 `Compression::best()` == level 9 == Python `bz2.BZ2Compressor()`.
            Codec::Bz2 => Enc::Bz2(BzEncoder::new(Vec::new(), BzCompression::best())),
            Codec::Zstd => {
                // `new` only fails on an invalid level; ZSTD_LEVEL is a constant.
                let enc =
                    ZstdEncoder::new(Vec::new(), ZSTD_LEVEL).expect("zstd level 3 is always valid");
                Enc::Zstd(enc)
            }
        };
        Compressor(enc)
    }

    /// Compress one inbound chunk, returning any compressed bytes now available.
    ///
    /// May return an empty `Vec` when the encoder is still buffering — this is
    /// expected and harmless for the byte-plane pump (an empty frame is a no-op).
    pub fn compress(&mut self, chunk: &[u8]) -> io::Result<Vec<u8>> {
        // We deliberately do NOT force a sync `flush()` per chunk: that would
        // insert empty stored blocks / flush markers and hurt the ratio. Like
        // Python's `compressor.compress(item)`, we just feed the chunk and drain
        // whatever the encoder has pushed to its sink on its own; the rest comes
        // out of `finish()` (= Python's terminal `compressor.flush()`).
        match &mut self.0 {
            Enc::Gzip(e) => {
                e.write_all(chunk)?;
                Ok(drain(e.get_mut()))
            }
            Enc::Xz(e) => {
                e.write_all(chunk)?;
                Ok(drain(e.get_mut()))
            }
            Enc::Bz2(e) => {
                e.write_all(chunk)?;
                Ok(drain(e.get_mut()))
            }
            Enc::Zstd(e) => {
                e.write_all(chunk)?;
                Ok(drain(e.get_mut()))
            }
        }
    }

    /// Finalize the stream, emitting the terminal footer bytes.
    ///
    /// Consumes `self` (no further chunks). This is the analogue of Python's
    /// single `compressor.flush()` at EOF.
    pub fn finish(self) -> io::Result<Vec<u8>> {
        match self.0 {
            Enc::Gzip(e) => e.finish(),
            Enc::Xz(e) => e.finish(),
            Enc::Bz2(e) => e.finish(),
            // zstd `finish()` returns the inner writer on success.
            Enc::Zstd(e) => e.finish(),
        }
    }
}

/// Internal per-codec decoder state. Symmetric to [`Enc`].
enum Dec {
    Gzip(GzDecoder<Vec<u8>>),
    Xz(XzDecoder<Vec<u8>>),
    Bz2(BzDecoder<Vec<u8>>),
    Zstd(ZstdDecoder<'static, Vec<u8>>),
}

/// A streaming, chunk-driven decompressor for a single codec.
///
/// Feed inbound compressed bytes with [`decompress`](Decompressor::decompress);
/// each call emits whatever decompressed bytes are available. At EOF call
/// [`finish`](Decompressor::finish) once. Only gzip carries trailing bytes that
/// surface from `finish`; the others return empty (their footer is consumed
/// inline), matching Python's `ZlibCompressedStream` EOF flush.
pub struct Decompressor(Dec);

impl Decompressor {
    /// Create a decompressor for `codec`.
    pub fn new(codec: Codec) -> Self {
        let dec = match codec {
            // flate2 write `GzDecoder` consumes a gzip stream; Python uses
            // `decompressobj(wbits=47)` (auto gzip/zlib) over the same bytes.
            Codec::Gzip => Dec::Gzip(GzDecoder::new(Vec::new())),
            Codec::Xz => Dec::Xz(XzDecoder::new(Vec::new())),
            Codec::Bz2 => Dec::Bz2(BzDecoder::new(Vec::new())),
            Codec::Zstd => {
                let dec =
                    ZstdDecoder::new(Vec::new()).expect("zstd decoder construction is infallible");
                Dec::Zstd(dec)
            }
        };
        Decompressor(dec)
    }

    /// Decompress one inbound compressed chunk, returning decompressed bytes.
    pub fn decompress(&mut self, chunk: &[u8]) -> io::Result<Vec<u8>> {
        // Write-based decoders emit decompressed output to their sink as they
        // consume input, so a plain `write_all` + drain mirrors Python's
        // `decompressor.decompress(chunk)`. No forced flush needed.
        match &mut self.0 {
            Dec::Gzip(d) => {
                d.write_all(chunk)?;
                Ok(drain(d.get_mut()))
            }
            Dec::Xz(d) => {
                d.write_all(chunk)?;
                Ok(drain(d.get_mut()))
            }
            Dec::Bz2(d) => {
                d.write_all(chunk)?;
                Ok(drain(d.get_mut()))
            }
            Dec::Zstd(d) => {
                d.write_all(chunk)?;
                Ok(drain(d.get_mut()))
            }
        }
    }

    /// Finalize decompression, emitting any trailing decompressed bytes.
    ///
    /// gzip surfaces its final block here (parity with Python's
    /// `ZlibCompressedStream` flush on EOF); the other codecs return empty.
    pub fn finish(self) -> io::Result<Vec<u8>> {
        match self.0 {
            // gzip `finish()` consumes self, flushes the final block and returns
            // the inner buffer (the residual tail after our streaming drains).
            Dec::Gzip(d) => d.finish(),
            // bzip2 / liblzma decoders expose `finish(&mut self) -> io::Result<W>`,
            // which `try_finish`es then takes the (already-drained) inner Vec, so
            // it yields exactly the residual decompressed tail.
            Dec::Xz(mut d) => d.finish(),
            Dec::Bz2(mut d) => d.finish(),
            // zstd's write Decoder has no `finish`; flush pending output then take
            // the residual from the inner buffer.
            Dec::Zstd(mut d) => {
                d.flush()?;
                Ok(std::mem::take(d.get_mut()))
            }
        }
    }
}

/// Take ownership of the accumulated sink contents, leaving it empty.
fn drain(buf: &mut Vec<u8>) -> Vec<u8> {
    std::mem::take(buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Compress `input` in `chunk_size`-byte pieces, returning the full blob.
    fn compress_chunked(codec: Codec, input: &[u8], chunk_size: usize) -> Vec<u8> {
        let mut c = Compressor::new(codec);
        let mut out = Vec::new();
        for piece in input.chunks(chunk_size.max(1)) {
            out.extend(c.compress(piece).unwrap());
        }
        out.extend(c.finish().unwrap());
        out
    }

    /// Decompress `input` in `chunk_size`-byte pieces, returning the full blob.
    fn decompress_chunked(codec: Codec, input: &[u8], chunk_size: usize) -> Vec<u8> {
        let mut d = Decompressor::new(codec);
        let mut out = Vec::new();
        for piece in input.chunks(chunk_size.max(1)) {
            out.extend(d.decompress(piece).unwrap());
        }
        out.extend(d.finish().unwrap());
        out
    }

    const ALL: [Codec; 4] = [Codec::Gzip, Codec::Xz, Codec::Bz2, Codec::Zstd];

    #[test]
    fn from_wire_roundtrips_known_and_rejects_unknown() {
        for c in ALL {
            assert_eq!(Codec::from_wire(c.as_wire()), Some(c));
        }
        assert_eq!(Codec::from_wire("none"), None);
        assert_eq!(Codec::from_wire(""), None);
        assert_eq!(Codec::from_wire("GZIP"), None); // case-sensitive on the wire
    }

    #[test]
    fn roundtrip_single_shot() {
        let input = b"the quick brown fox jumps over the lazy dog".repeat(1000);
        for codec in ALL {
            let blob = {
                let mut c = Compressor::new(codec);
                let mut out = c.compress(&input).unwrap();
                out.extend(c.finish().unwrap());
                out
            };
            let back = {
                let mut d = Decompressor::new(codec);
                let mut out = d.decompress(&blob).unwrap();
                out.extend(d.finish().unwrap());
                out
            };
            assert_eq!(back, input, "single-shot round-trip failed for {codec:?}");
        }
    }

    #[test]
    fn roundtrip_empty_input() {
        for codec in ALL {
            // Empty input still produces a valid (header+footer) frame that
            // decompresses back to empty.
            let blob = {
                let mut c = Compressor::new(codec);
                let mut out = c.compress(b"").unwrap();
                out.extend(c.finish().unwrap());
                out
            };
            assert!(
                !blob.is_empty(),
                "{codec:?} empty frame should have header/footer"
            );
            let back = {
                let mut d = Decompressor::new(codec);
                let mut out = d.decompress(&blob).unwrap();
                out.extend(d.finish().unwrap());
                out
            };
            assert_eq!(back, b"", "empty round-trip failed for {codec:?}");
        }
    }

    #[test]
    fn roundtrip_multi_chunk_streaming() {
        // Mix of compressible (repeated) and a small incompressible tail.
        let mut input = b"ABCDEFGH".repeat(5000);
        input.extend_from_slice(&[0u8, 1, 2, 3, 255, 254, 253, 7, 42, 99]);
        for codec in ALL {
            // Compress in odd-sized chunks, decompress in different-sized chunks.
            let blob = compress_chunked(codec, &input, 333);
            let back = decompress_chunked(codec, &blob, 257);
            assert_eq!(back, input, "multi-chunk round-trip failed for {codec:?}");

            // Single-byte chunks exercise the buffering edges.
            let blob1 = compress_chunked(codec, &input, 1);
            let back1 = decompress_chunked(codec, &blob1, 1);
            assert_eq!(back1, input, "1-byte-chunk round-trip failed for {codec:?}");
        }
    }

    #[test]
    fn corrupt_input_errors_without_aborting() {
        // A malformed/truncated resource stream arrives from an untrusted client at the host
        // decompression seam; it must surface a clean `io::Error`, never crash the host. The fact
        // that this test runs to completion at all proves no codec ABORTS the process on bad input
        // (an abort would kill the whole test binary, uncatchable by `should_panic`/catch_unwind).
        for codec in ALL {
            // Pure garbage: an invalid container header must error, not "succeed" or abort.
            let mut d = Decompressor::new(codec);
            let garbage = vec![0xABu8; 8192];
            let res = d.decompress(&garbage).and_then(|_| d.finish());
            assert!(
                res.is_err(),
                "{codec:?}: garbage input must yield an error, not succeed/abort"
            );
        }
        for codec in ALL {
            // A valid stream truncated before its footer: must not abort. `finish` may error
            // (incomplete) or yield a short read, but the process must survive either way.
            let mut c = Compressor::new(codec);
            let mut blob = c.compress(&b"the quick brown fox".repeat(2000)).unwrap();
            blob.extend(c.finish().unwrap());
            let truncated = blob[..blob.len().saturating_sub(8)].to_vec();
            let mut d = Decompressor::new(codec);
            let _ = d.decompress(&truncated).and_then(|_| d.finish());
        }
    }
}
