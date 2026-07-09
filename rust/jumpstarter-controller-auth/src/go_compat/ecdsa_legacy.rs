//! Bit-exact port of `filippo.io/keygen.ECDSALegacy` (P-256 only) plus the
//! controller's seed-to-key composition from
//! `controller/internal/oidc/op.go` `NewSignerFromSeed`:
//!
//! ```text
//! hash   = sha256(CONTROLLER_KEY bytes)
//! seed   = int64(binary.BigEndian.Uint64(hash[:8]))
//! reader = rand.New(rand.NewSource(seed))        // go_compat::gorand
//! key    = keygen.ECDSALegacy(elliptic.P256(), reader)
//! ```
//!
//! `ECDSALegacy` (keygen `ecdsa.go`, v0.0.0-20240718133620-7f162efbbd87,
//! reproducing Go 1.19's `ecdsa.GenerateKey` / FIPS 186-5 A.2.1) reads exactly
//! `N.BitLen()/8 + 8` bytes — **40 bytes for P-256** — in one `io.ReadFull`,
//! interprets them as a big-endian integer, and maps it into `[1, n-1]` via
//! `x mod (n-1) + 1`.
//!
//! Parity is locked by golden vectors generated from the Go code
//! (`controller/hack/goldenvec` → `tests/golden/derivation.json`).

use num_bigint::BigUint;
use p256::elliptic_curve::sec1::ToEncodedPoint;
use sha2::{Digest, Sha256};

use super::gorand::GoRand;

/// Bytes `ECDSALegacy` consumes for P-256: `params.N.BitLen()/8 + 8`.
const P256_LEGACY_READ_LEN: usize = 256 / 8 + 8; // 40

/// The order `n` of the P-256 base point (big-endian hex), i.e.
/// `elliptic.P256().Params().N`. Checked against the `p256` crate's own
/// constant in the tests below.
const P256_ORDER_HEX: &str = "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551";

#[derive(Debug, thiserror::Error)]
pub enum DeriveKeyError {
    /// The reduced scalar was rejected by the `p256` crate. Unreachable in
    /// practice: `x mod (n-1) + 1` is always in `[1, n-1]`.
    #[error("derived scalar is not a valid P-256 secret key: {0}")]
    InvalidScalar(p256::elliptic_curve::Error),
}

/// Port of `keygen.ECDSALegacy(elliptic.P256(), reader)`: reads 40 bytes from
/// the Go-compatible reader and maps them to a P-256 secret scalar via
/// `d = bytes mod (n-1) + 1`.
pub fn ecdsa_legacy_p256(reader: &mut GoRand) -> Result<p256::SecretKey, DeriveKeyError> {
    let mut b = [0u8; P256_LEGACY_READ_LEN];
    reader.read(&mut b);

    let x = BigUint::from_bytes_be(&b);
    let n = BigUint::parse_bytes(P256_ORDER_HEX.as_bytes(), 16)
        .expect("P-256 order constant is valid hex");
    let d = x % (&n - 1u32) + 1u32;

    // Left-pad to the 32-byte fixed-width big-endian encoding SecretKey expects.
    let d_bytes = d.to_bytes_be();
    debug_assert!(d_bytes.len() <= 32);
    let mut padded = [0u8; 32];
    padded[32 - d_bytes.len()..].copy_from_slice(&d_bytes);

    p256::SecretKey::from_slice(&padded).map_err(DeriveKeyError::InvalidScalar)
}

/// Port of the derivation inside `oidc.NewSignerFromSeed`: CONTROLLER_KEY
/// bytes → sha256 → big-endian `int64` of the first 8 hash bytes → seeded Go
/// `math/rand` → `ECDSALegacy(P256)`.
pub fn derive_key_from_seed(controller_key: &[u8]) -> Result<p256::SecretKey, DeriveKeyError> {
    let hash = Sha256::digest(controller_key);
    let seed = i64::from_be_bytes(hash[..8].try_into().expect("sha256 output >= 8 bytes"));
    let mut reader = GoRand::new(seed);
    ecdsa_legacy_p256(&mut reader)
}

/// The (d, x, y) scalars of a derived key as 32-byte left-padded big-endian
/// hex, matching the golden-vector encoding.
pub fn key_scalars_hex(key: &p256::SecretKey) -> (String, String, String) {
    let d = hex_encode(&key.to_bytes());
    let point = key.public_key().to_encoded_point(false);
    let x = hex_encode(point.x().expect("uncompressed point has x"));
    let y = hex_encode(point.y().expect("uncompressed point has y"));
    (d, x, y)
}

/// Lower-case hex, no allocation-per-byte niceties needed at this scale.
pub fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0xf) as usize] as char);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn p256_order_constant_matches_crate() {
        use p256::elliptic_curve::bigint::Encoding;
        use p256::elliptic_curve::Curve;
        let ours = BigUint::parse_bytes(P256_ORDER_HEX.as_bytes(), 16).unwrap();
        let crate_order = BigUint::from_bytes_be(&p256::NistP256::ORDER.to_be_bytes());
        assert_eq!(ours, crate_order);
    }

    #[test]
    fn read_length_is_40_bytes() {
        assert_eq!(P256_LEGACY_READ_LEN, 40);
    }

    /// The "empty" vector from tests/golden/derivation.json, inlined as a
    /// smoke test (the full set is exercised by tests/golden_derivation.rs).
    #[test]
    fn empty_controller_key_smoke() {
        let key = derive_key_from_seed(b"").unwrap();
        let (d, x, y) = key_scalars_hex(&key);
        assert_eq!(
            d,
            "7e774bdbd9fef761436ed3986dda3f9c91fc3c92ea0e91dbbf5615d2111cf4d2"
        );
        assert_eq!(
            x,
            "399b0e6c143008ed92bb25b818ca92f7fc3348470dc0e2ac532aacf827f193a0"
        );
        assert_eq!(
            y,
            "e04a2e30e9465fcb572c8d6158b11a9d17bad0edf039b6151d7a8d49062ed380"
        );
    }

    #[test]
    fn hex_encode_basics() {
        assert_eq!(hex_encode(&[0x00, 0x0f, 0xa5, 0xff]), "000fa5ff");
        assert_eq!(hex_encode(&[]), "");
    }
}
