//! `Bytes` — an efficient byte-buffer type for the FFI byte plane.
//!
//! UniFFI marshals `Vec<u8>` through its *generic* `Vec<T>` converter, which
//! serializes/lifts **one byte at a time** with a bounds-check per byte
//! (`uniffi_core::ffi_converter_impls` has no `Vec<u8>` specialization — the
//! upstream source even flags passing it "directly as a `RustBuffer`" as future
//! work). The generated Python binding *already* uses the efficient bulk `bytes`
//! converter, so the Rust side is the sole bottleneck: a 512 MiB resource/flash
//! transfer pegs one core in `<Vec<u8>>::try_read`/`write` at ~9 MiB/s (≈5.6×
//! slower than the pure-Python exporter, which delivers the bytes in-process).
//!
//! `Bytes` is a `Vec<u8>` newtype whose `FfiConverter` does the bulk thing:
//! zero-copy `lower`/`try_lift` (hand the `RustBuffer`'s `Vec` straight over,
//! exactly like `String`) and a single bulk copy for the nested `write`/`try_read`
//! paths (e.g. inside `Option<Bytes>`). Its `TYPE_ID_META` is byte-identical to
//! `Vec<u8>`'s (`SEQUENCE<u8>`, which the bindgen reader maps to `Type::Bytes`),
//! so the foreign bindings and per-method checksums are unchanged — Python still
//! sees `bytes`; only the slow Rust loop is replaced.

use uniffi::deps::anyhow::bail;
use uniffi::deps::bytes::{Buf, BufMut};
use uniffi::{check_remaining, metadata, FfiConverter, MetadataBuffer, RustBuffer};

/// Bulk-marshalled byte buffer — a drop-in for `Vec<u8>` at the UniFFI seam.
pub struct Bytes(pub Vec<u8>);

impl From<Vec<u8>> for Bytes {
    fn from(v: Vec<u8>) -> Self {
        Bytes(v)
    }
}

impl From<Bytes> for Vec<u8> {
    fn from(b: Bytes) -> Self {
        b.0
    }
}

// SAFETY: mirrors uniffi_core's own `FfiConverter for String` — `lower`/`try_lift`
// move the `RustBuffer`'s uniquely-owned `Vec` across the boundary without copying;
// `write`/`try_read` use the same length-prefixed layout as the generic `Vec<u8>`
// (and the Python `_UniffiConverterBytes`), just in bulk instead of per element.
unsafe impl<UT> FfiConverter<UT> for Bytes {
    type FfiType = RustBuffer;

    // Cross via the length-prefixed RustBuffer layout (a `i32` length + raw bytes), which is
    // what the Python `_UniffiConverterBytes` writes and the generic `Vec<u8>` uses — NOT
    // `String`'s special raw/no-prefix form (that mismatch corrupts/stalls the stream). These
    // inline the `Lower`/`Lift` convenience helpers (not reachable from inside `FfiConverter`)
    // and route through our bulk `write`/`try_read`, so it's a single copy, never per byte.
    fn lower(obj: Self) -> RustBuffer {
        let mut buf = Vec::with_capacity(4 + obj.0.len());
        <Self as FfiConverter<UT>>::write(obj, &mut buf);
        RustBuffer::from_vec(buf)
    }

    fn try_lift(v: RustBuffer) -> uniffi::Result<Self> {
        let vec = v.destroy_into_vec();
        let mut buf = vec.as_slice();
        let value = <Self as FfiConverter<UT>>::try_read(&mut buf)?;
        match Buf::remaining(&buf) {
            0 => Ok(value),
            n => bail!("junk data left in buffer after lifting Bytes (count: {n})"),
        }
    }

    // The length-prefixed bulk codec used by both the top-level (above) and nested
    // (`Option<Bytes>`) paths — one `i32` length then a bulk copy, never per byte.
    fn write(obj: Self, buf: &mut Vec<u8>) {
        let len = i32::try_from(obj.0.len()).expect("byte buffer larger than i32::MAX");
        buf.put_i32(len);
        buf.put_slice(&obj.0);
    }

    fn try_read(buf: &mut &[u8]) -> uniffi::Result<Self> {
        check_remaining(buf, 4)?;
        let len = usize::try_from(buf.get_i32())?;
        check_remaining(buf, len)?;
        let out = buf.chunk()[..len].to_vec();
        buf.advance(len);
        Ok(Bytes(out))
    }

    // Identical to `Vec<u8>`'s metadata (`SEQUENCE<u8>`) so the bindgen still emits
    // `bytes` and the method checksums don't move.
    const TYPE_ID_META: MetadataBuffer = MetadataBuffer::from_code(metadata::codes::TYPE_VEC)
        .concat(<u8 as FfiConverter<UT>>::TYPE_ID_META);
}

// Derive Lower/Lift/LowerReturn/LiftReturn/LiftRef/TypeId from the FfiConverter impl.
uniffi::derive_ffi_traits!(blanket Bytes);
