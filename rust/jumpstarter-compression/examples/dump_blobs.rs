//! Throwaway helper used during development to cross-check Python<->Rust wire
//! compat: emits Rust-compressed blobs of the golden inputs to an output dir so
//! the Python decompressors can be asked to read them. Run:
//!   cargo run -p jumpstarter-compression --example dump_blobs -- /tmp/rustcomp
//! Not part of the test suite; kept as documentation of the round-trip check.

use std::path::PathBuf;

use jumpstarter_compression::{Codec, Compressor};

fn compress(codec: Codec, input: &[u8]) -> Vec<u8> {
    let mut c = Compressor::new(codec);
    let mut out = c.compress(input).unwrap();
    out.extend(c.finish().unwrap());
    out
}

fn main() {
    let out_dir = PathBuf::from(
        std::env::args()
            .nth(1)
            .expect("usage: dump_blobs <out_dir>"),
    );
    std::fs::create_dir_all(&out_dir).unwrap();
    let fixtures = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    for (stem, file) in [("random", "input_random.bin"), ("zeros", "input_zeros.bin")] {
        let input = std::fs::read(fixtures.join(file)).unwrap();
        for (codec, ext) in [
            (Codec::Gzip, "gzip"),
            (Codec::Xz, "xz"),
            (Codec::Bz2, "bz2"),
            (Codec::Zstd, "zstd"),
        ] {
            let blob = compress(codec, &input);
            std::fs::write(out_dir.join(format!("rust_{stem}.{ext}")), &blob).unwrap();
        }
    }
    println!("wrote rust blobs to {}", out_dir.display());
}
