//! Golden wire-compatibility tests against Python-produced fixtures.
//!
//! Fixtures under `tests/fixtures/` were produced by the exact compressor calls
//! in `python/packages/jumpstarter/jumpstarter/streams/encoding.py::compress_stream`
//! (single `compress(data)` + terminal `flush()`), on Python 3.12.3 with
//! backports.zstd, zlib 1.2.12. See `MANIFEST.json` for lengths + sha256.
//!
//! The contract we assert (per the golden recipe) is *round-trip*, not byte
//! identity:
//!   (a) Rust DECOMPRESSES Python's golden bytes back to the original input
//!       (sha256-match) for all four formats — the load-bearing direction, since
//!       the host decompresses client-sent compressed bytes.
//!   (b) Rust's OWN compressed output, fed back through the Rust decompressor,
//!       reproduces the original — proving Rust emits a valid frame Python could
//!       also read. (We do NOT assert byte-identity with Python: the gzip OS
//!       header byte differs — Python=0x13 platform zlib vs flate2=0xFF — and
//!       zstd/xz framing can vary across library versions.)

use std::path::{Path, PathBuf};

use jumpstarter_compression::{Codec, Compressor, Decompressor};
use sha2::{Digest, Sha256};

fn fixtures_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn read_fixture(name: &str) -> Vec<u8> {
    let path = fixtures_dir().join(name);
    std::fs::read(&path).unwrap_or_else(|e| panic!("read fixture {}: {e}", path.display()))
}

fn sha256_hex(data: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(data);
    let digest = h.finalize();
    let mut s = String::with_capacity(64);
    for b in digest {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Decompress a full compressed blob via the streaming API in one shot.
fn rust_decompress(codec: Codec, blob: &[u8]) -> Vec<u8> {
    let mut d = Decompressor::new(codec);
    let mut out = d.decompress(blob).expect("decompress");
    out.extend(d.finish().expect("decompress finish"));
    out
}

/// Compress a full input via the streaming API in one shot.
fn rust_compress(codec: Codec, input: &[u8]) -> Vec<u8> {
    let mut c = Compressor::new(codec);
    let mut out = c.compress(input).expect("compress");
    out.extend(c.finish().expect("compress finish"));
    out
}

// (input fixture name, sha256 of the raw input) per MANIFEST.json.
const INPUTS: [(&str, &str); 2] = [
    (
        "input_random.bin",
        "69388d838bfd22412714356c7fb6eb9a3920994c901e910ee5c947f9762011cd",
    ),
    (
        "input_zeros.bin",
        "8a39d2abd3999ab73c34db2476849cddf303ce389b35826850f9a700589b4a90",
    ),
];

// (codec, file extension used by the fixtures).
const CODECS: [(Codec, &str); 4] = [
    (Codec::Gzip, "gzip"),
    (Codec::Xz, "xz"),
    (Codec::Bz2, "bz2"),
    (Codec::Zstd, "zstd"),
];

#[test]
fn input_fixtures_match_manifest_sha256() {
    for (name, sha) in INPUTS {
        let data = read_fixture(name);
        assert_eq!(sha256_hex(&data), sha, "input fixture {name} sha mismatch");
    }
}

/// (a) Rust decompresses Python's golden compressed bytes -> original input.
/// This is the host-side direction that actually runs in production.
#[test]
fn rust_decompresses_python_golden_to_original() {
    for (input_name, input_sha) in INPUTS {
        let original = read_fixture(input_name);
        // stem: "random" or "zeros"
        let stem = input_name
            .strip_prefix("input_")
            .and_then(|s| s.strip_suffix(".bin"))
            .unwrap();
        for (codec, ext) in CODECS {
            let blob = read_fixture(&format!("{stem}.{ext}"));
            let restored = rust_decompress(codec, &blob);
            assert_eq!(
                sha256_hex(&restored),
                input_sha,
                "Rust decompress of Python {stem}.{ext} did not reproduce original"
            );
            assert_eq!(restored, original, "byte mismatch for {stem}.{ext}");
        }
    }
}

/// (a') Same direction but feeding Python's golden bytes in small chunks, to
/// prove streaming decode (not just one-shot) reproduces the original.
#[test]
fn rust_decompresses_python_golden_chunked() {
    for (input_name, _input_sha) in INPUTS {
        let original = read_fixture(input_name);
        let stem = input_name
            .strip_prefix("input_")
            .and_then(|s| s.strip_suffix(".bin"))
            .unwrap();
        for (codec, ext) in CODECS {
            let blob = read_fixture(&format!("{stem}.{ext}"));
            let mut d = Decompressor::new(codec);
            let mut restored = Vec::new();
            for piece in blob.chunks(4096) {
                restored.extend(d.decompress(piece).expect("decompress chunk"));
            }
            restored.extend(d.finish().expect("decompress finish"));
            assert_eq!(
                restored, original,
                "chunked Rust decompress of Python {stem}.{ext} mismatch"
            );
        }
    }
}

/// (b) Rust compresses the original, then Rust decompresses it back -> original.
/// Proves Rust emits valid frames (the same frames Python's decompressors read).
#[test]
fn rust_compress_then_decompress_roundtrips_originals() {
    for (input_name, input_sha) in INPUTS {
        let original = read_fixture(input_name);
        for (codec, _ext) in CODECS {
            let blob = rust_compress(codec, &original);
            // Sanity: magic bytes per COMPRESSION_SIGNATURES.
            assert_magic(codec, &blob);
            let restored = rust_decompress(codec, &blob);
            assert_eq!(
                sha256_hex(&restored),
                input_sha,
                "Rust compress->decompress of {input_name} via {codec:?} mismatch"
            );
        }
    }
}

fn assert_magic(codec: Codec, blob: &[u8]) {
    let ok = match codec {
        Codec::Gzip => blob.starts_with(&[0x1f, 0x8b, 0x08]),
        Codec::Xz => blob.starts_with(&[0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00]),
        Codec::Bz2 => blob.starts_with(&[0x42, 0x5a, 0x68]), // "BZh"
        Codec::Zstd => blob.starts_with(&[0x28, 0xb5, 0x2f, 0xfd]),
    };
    assert!(ok, "Rust {codec:?} output had wrong magic bytes: {:02x?}", &blob[..blob.len().min(8)]);
}

/// bz2 level-9 header parity: Python `BZ2Compressor()` emits "BZh9".
#[test]
fn rust_bz2_uses_level_9_header() {
    let original = read_fixture("input_zeros.bin");
    let blob = rust_compress(Codec::Bz2, &original);
    assert_eq!(&blob[..4], b"BZh9", "bz2 must use level 9 ('BZh9') to match Python");
}
