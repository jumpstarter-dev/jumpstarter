//! Username normalization, ported from
//! `controller/internal/authorization/metadata.go` (behavioral reference).
//!
//! Three functions turn an authenticated identity (an OIDC `sub`/username, or
//! an internal/service-account subject) into a Kubernetes resource name:
//!
//! - [`strip_oidc_prefix`] drops the provider prefix (`internal/oidc/metadata.go`
//!   `stripOIDCPrefix`);
//! - [`normalize_oidc_username`] sanitizes the *stripped* name into a DNS-1123
//!   label (`normalizeOIDCUsername`), used as the auto-provisioned Client name;
//! - [`normalize_name`] is the `oidc-<=37>-<sha256[..3]>` variant over the
//!   *raw* (un-stripped) username (`normalizeName`).
//!
//! ## Go-exact Unicode behavior (load-bearing)
//!
//! Go's `strings.ToLower` uses `unicode.ToLower`, the **simple** 1:1 rune case
//! mapping. Rust's `char::to_lowercase` is the **full** mapping and diverges
//! for U+0130 (`İ` → `"i\u{307}"`). We reproduce Go by taking the first scalar
//! of the full mapping (see [`go_to_lower`]): for every scalar that first
//! scalar equals the simple mapping (U+0130 is the only lowercase full mapping
//! with more than one scalar, and its first scalar `'i'` is exactly Go's
//! simple mapping). Go's `regexp` `[^-a-zA-Z0-9]` matches per **rune**, so a
//! multibyte char collapses to a single `-`; we iterate over `char`s to match.
//! After that replacement the string is pure ASCII, so Go's byte-slice
//! truncation (`s[:37]`/`s[:63]`) equals a char/byte truncation here.
//!
//! All three functions plus [`go_quote`] are locked to Go output by
//! `tests/golden/normalize_quote.json` (regenerated from Go; see
//! `tests/golden.rs`), including multibyte, combining-mark, private-use, and
//! astral-plane inputs.

use sha2::{Digest, Sha256};

// ---------------------------------------------------------------------------
// Normalization
// ---------------------------------------------------------------------------

/// Reproduces Go's `unicode.ToLower` (simple case mapping) for one scalar.
///
/// See the module docs: the first scalar of Rust's full lowercase mapping
/// equals Go's simple mapping for every scalar.
fn go_to_lower(c: char) -> char {
    c.to_lowercase().next().unwrap()
}

/// `strings.ToLower` — simple per-rune lowercasing.
fn go_lower(s: &str) -> String {
    s.chars().map(go_to_lower).collect()
}

/// `invalidChar.ReplaceAllString(s, "-")` where `invalidChar = [^-a-zA-Z0-9]`.
/// Each non-matching rune (of any byte width) becomes a single `-`.
fn replace_invalid_chars(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c == '-' || c.is_ascii_alphanumeric() {
                c
            } else {
                '-'
            }
        })
        .collect()
}

/// `multipleHyphen.ReplaceAllString(s, "-")` where `multipleHyphen = -+`:
/// collapse each run of hyphens to a single hyphen.
fn collapse_hyphens(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_hyphen = false;
    for c in s.chars() {
        if c == '-' {
            if !prev_hyphen {
                out.push('-');
            }
            prev_hyphen = true;
        } else {
            out.push(c);
            prev_hyphen = false;
        }
    }
    out
}

/// `surroundingHyphen.ReplaceAllString(s, "")` where `surroundingHyphen =
/// ^-|-$`: remove at most one leading and one trailing hyphen. (Callers always
/// [`collapse_hyphens`] first, so there is never a run to strip.)
fn trim_surrounding_hyphen(s: &str) -> &str {
    let s = s.strip_prefix('-').unwrap_or(s);
    s.strip_suffix('-').unwrap_or(s)
}

/// Lowercase-hex encode (no separators), matching `hex.EncodeToString`.
fn hex_encode(bytes: &[u8]) -> String {
    const LOWERHEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(LOWERHEX[(b >> 4) as usize] as char);
        out.push(LOWERHEX[(b & 0x0f) as usize] as char);
    }
    out
}

/// True when `username` matches the Kubernetes service-account shape
/// `provider:system:serviceaccount:namespace:name` (>= 5 colon-separated
/// parts with `parts[1] == "system"` and `parts[2] == "serviceaccount"`).
///
/// Ported from `isKubernetesServiceAccount` (`metadata.go:43-46`). Operates on
/// the **full** username, not the stripped one.
pub fn is_kubernetes_service_account(username: &str) -> bool {
    let parts: Vec<&str> = username.split(':').collect();
    parts.len() >= 5 && parts[1] == "system" && parts[2] == "serviceaccount"
}

/// Removes the OIDC provider prefix and extracts the meaningful resource name.
///
/// Ported from `stripOIDCPrefix` (`metadata.go:54-70`):
///   - no colon → returned as-is;
///   - service account (`provider:system:serviceaccount:ns:name`) → `ns:name`
///     (avoids cross-namespace collisions);
///   - otherwise → everything after the first colon.
pub fn strip_oidc_prefix(username: &str) -> String {
    let parts: Vec<&str> = username.split(':').collect();

    // No colons, return as-is (Go: len(parts) == 1).
    if parts.len() == 1 {
        return username.to_string();
    }

    // Service account: return "namespace:name".
    if parts.len() >= 5 && parts[1] == "system" && parts[2] == "serviceaccount" {
        return format!("{}:{}", parts[3], parts[4]);
    }

    // Default: strip only the provider prefix (the first part).
    parts[1..].join(":")
}

/// Normalizes an OIDC username into a Kubernetes-compliant resource name.
///
/// Ported from `normalizeOIDCUsername` (`metadata.go:95-113`): strip the
/// provider prefix, lowercase, replace every non-`[-a-zA-Z0-9]` rune with `-`,
/// collapse hyphen runs, trim a surrounding hyphen, then (only if longer than
/// the 63-char DNS-label limit) truncate to 63 and re-trim a surrounding
/// hyphen.
pub fn normalize_oidc_username(username: &str) -> String {
    let base_name = strip_oidc_prefix(username);

    let sanitized = go_lower(&base_name);
    let sanitized = replace_invalid_chars(&sanitized);
    let sanitized = collapse_hyphens(&sanitized);
    let mut sanitized = trim_surrounding_hyphen(&sanitized).to_string();

    // DNS label max length is 63 characters. `sanitized` is pure ASCII here,
    // so byte length == char count and `truncate` is on a char boundary.
    if sanitized.len() > 63 {
        sanitized.truncate(63);
        // Ensure we don't end with a hyphen after truncation.
        sanitized = trim_surrounding_hyphen(&sanitized).to_string();
    }

    sanitized
}

/// The `oidc-<sanitized(<=37)>-<sha256(name)[..3] hex>` name variant.
///
/// Ported from `normalizeName` (`metadata.go:72-89`). Note two differences
/// from [`normalize_oidc_username`]: the hash and the sanitization both run on
/// the **raw** username (no [`strip_oidc_prefix`]), and the 37-char truncation
/// is **not** followed by a re-trim (a trailing hyphen can survive, producing
/// e.g. `oidc-...-my--<hex>`).
pub fn normalize_name(name: &str) -> String {
    let hash = Sha256::digest(name.as_bytes());

    let sanitized = go_lower(name);
    let sanitized = replace_invalid_chars(&sanitized);
    let sanitized = collapse_hyphens(&sanitized);
    let mut sanitized = trim_surrounding_hyphen(&sanitized).to_string();

    if sanitized.len() > 37 {
        // No re-trim after truncation, matching Go exactly.
        sanitized.truncate(37);
    }

    // strings.Join(["oidc", sanitized, hex], "-").
    format!("oidc-{sanitized}-{}", hex_encode(&hash[..3]))
}

// ---------------------------------------------------------------------------
// Go `%q` / strconv.Quote
// ---------------------------------------------------------------------------

/// Reproduces Go's `strconv.Quote` / `fmt`'s `%q` verb for a string.
///
/// Needed byte-for-byte by the `resource name mismatch` error string
/// (`metadata.go:158-164`), whose three `%q` operands can carry arbitrary
/// Unicode (the OIDC username in particular). Rust's `{:?}` is **not**
/// equivalent — it emits `\u{a0}`/`\u{7f}`-style escapes where Go emits
/// ` `/`\x7f` — so this is a faithful port of `strconv`'s
/// `appendEscapedRune` (Go 1.26 `src/strconv/quote.go`), including the
/// printability tables ported verbatim in [`go_print`].
///
/// A Rust `&str` is always valid UTF-8 and a `char` is always a valid scalar,
/// so Go's invalid-byte (`\xNN`) and invalid-rune (`0xFFFD`) fallbacks are
/// unreachable and omitted.
pub fn go_quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        append_escaped_rune(&mut out, c);
    }
    out.push('"');
    out
}

fn append_escaped_rune(out: &mut String, c: char) {
    // Always backslashed (quote char and backslash).
    if c == '"' || c == '\\' {
        out.push('\\');
        out.push(c);
        return;
    }

    // Printable runes pass through as UTF-8.
    if go_print::is_print(c as u32) {
        out.push(c);
        return;
    }

    match c {
        '\u{07}' => out.push_str("\\a"),
        '\u{08}' => out.push_str("\\b"),
        '\u{0c}' => out.push_str("\\f"),
        '\n' => out.push_str("\\n"),
        '\r' => out.push_str("\\r"),
        '\t' => out.push_str("\\t"),
        '\u{0b}' => out.push_str("\\v"),
        _ => {
            let r = c as u32;
            if r < 0x20 || r == 0x7f {
                out.push_str("\\x");
                push_hex(out, r, 2);
            } else if r < 0x10000 {
                out.push_str("\\u");
                push_hex(out, r, 4);
            } else {
                out.push_str("\\U");
                push_hex(out, r, 8);
            }
        }
    }
}

/// Append `digits` lowercase big-endian hex digits of `r` (`strconv`'s
/// `lowerhex[r>>s & 0xF]` loop).
fn push_hex(out: &mut String, r: u32, digits: usize) {
    const LOWERHEX: &[u8; 16] = b"0123456789abcdef";
    for i in (0..digits).rev() {
        out.push(LOWERHEX[((r >> (4 * i)) & 0xf) as usize] as char);
    }
}

/// Verbatim port of Go 1.26 `strconv.IsPrint` and its generated tables
/// (`src/strconv/isprint.go`). The tables are BSD-3 licensed (`The Go
/// Authors`); they encode the same printability classification `%q` uses. Only
/// the `IsPrint` path is needed here (not `IsGraphic`), so `isGraphic` is
/// omitted.
mod go_print {
    /// Generic lower-bound search: first index `i` with `s[i] >= v` (Go's
    /// generic `bsearch`).
    fn bsearch<T: Ord + Copy>(s: &[T], v: T) -> usize {
        let (mut i, mut j) = (0usize, s.len());
        while i < j {
            let h = i + (j - i) / 2;
            if s[h] < v {
                i = h + 1;
            } else {
                j = h;
            }
        }
        i
    }

    /// `strconv.IsPrint(r)` for a Unicode scalar `r`.
    pub(super) fn is_print(r: u32) -> bool {
        // Fast check for Latin-1.
        if r <= 0xff {
            if (0x20..=0x7e).contains(&r) {
                return true;
            }
            if (0xa1..=0xff).contains(&r) {
                return r != 0xad; // soft hyphen
            }
            return false;
        }

        if r < 0x1_0000 {
            let rr = r as u16;
            let i = bsearch(IS_PRINT16, rr);
            // len(IS_PRINT16) is even, so `i|1` is in-bounds whenever `i < len`.
            if i >= IS_PRINT16.len() || rr < IS_PRINT16[i & !1] || IS_PRINT16[i | 1] < rr {
                return false;
            }
            let j = bsearch(IS_NOT_PRINT16, rr);
            return !(j < IS_NOT_PRINT16.len() && IS_NOT_PRINT16[j] == rr);
        }

        let rr = r;
        let i = bsearch(IS_PRINT32, rr);
        if i >= IS_PRINT32.len() || rr < IS_PRINT32[i & !1] || IS_PRINT32[i | 1] < rr {
            return false;
        }
        if r >= 0x2_0000 {
            return true;
        }
        // isNotPrint32 stores (codepoint - 0x10000) as u16.
        let r16 = (r - 0x1_0000) as u16;
        let j = bsearch(IS_NOT_PRINT32, r16);
        !(j < IS_NOT_PRINT32.len() && IS_NOT_PRINT32[j] == r16)
    }

    // Tables generated by `go run makeisprint.go` (Go 1.26 src/strconv/isprint.go).
    #[rustfmt::skip]
    pub(super) const IS_PRINT16: &[u16] = &[
        0x0020, 0x007e, 0x00a1, 0x0377, 0x037a, 0x037f, 0x0384, 0x0556,
        0x0559, 0x058a, 0x058d, 0x05c7, 0x05d0, 0x05ea, 0x05ef, 0x05f4,
        0x0606, 0x070d, 0x0710, 0x074a, 0x074d, 0x07b1, 0x07c0, 0x07fa,
        0x07fd, 0x082d, 0x0830, 0x085b, 0x085e, 0x086a, 0x0870, 0x088e,
        0x0898, 0x098c, 0x098f, 0x0990, 0x0993, 0x09b2, 0x09b6, 0x09b9,
        0x09bc, 0x09c4, 0x09c7, 0x09c8, 0x09cb, 0x09ce, 0x09d7, 0x09d7,
        0x09dc, 0x09e3, 0x09e6, 0x09fe, 0x0a01, 0x0a0a, 0x0a0f, 0x0a10,
        0x0a13, 0x0a39, 0x0a3c, 0x0a42, 0x0a47, 0x0a48, 0x0a4b, 0x0a4d,
        0x0a51, 0x0a51, 0x0a59, 0x0a5e, 0x0a66, 0x0a76, 0x0a81, 0x0ab9,
        0x0abc, 0x0acd, 0x0ad0, 0x0ad0, 0x0ae0, 0x0ae3, 0x0ae6, 0x0af1,
        0x0af9, 0x0b0c, 0x0b0f, 0x0b10, 0x0b13, 0x0b39, 0x0b3c, 0x0b44,
        0x0b47, 0x0b48, 0x0b4b, 0x0b4d, 0x0b55, 0x0b57, 0x0b5c, 0x0b63,
        0x0b66, 0x0b77, 0x0b82, 0x0b8a, 0x0b8e, 0x0b95, 0x0b99, 0x0b9f,
        0x0ba3, 0x0ba4, 0x0ba8, 0x0baa, 0x0bae, 0x0bb9, 0x0bbe, 0x0bc2,
        0x0bc6, 0x0bcd, 0x0bd0, 0x0bd0, 0x0bd7, 0x0bd7, 0x0be6, 0x0bfa,
        0x0c00, 0x0c39, 0x0c3c, 0x0c4d, 0x0c55, 0x0c5a, 0x0c5d, 0x0c5d,
        0x0c60, 0x0c63, 0x0c66, 0x0c6f, 0x0c77, 0x0cb9, 0x0cbc, 0x0ccd,
        0x0cd5, 0x0cd6, 0x0cdd, 0x0ce3, 0x0ce6, 0x0cf3, 0x0d00, 0x0d4f,
        0x0d54, 0x0d63, 0x0d66, 0x0d96, 0x0d9a, 0x0dbd, 0x0dc0, 0x0dc6,
        0x0dca, 0x0dca, 0x0dcf, 0x0ddf, 0x0de6, 0x0def, 0x0df2, 0x0df4,
        0x0e01, 0x0e3a, 0x0e3f, 0x0e5b, 0x0e81, 0x0ebd, 0x0ec0, 0x0ed9,
        0x0edc, 0x0edf, 0x0f00, 0x0f6c, 0x0f71, 0x0fda, 0x1000, 0x10c7,
        0x10cd, 0x10cd, 0x10d0, 0x124d, 0x1250, 0x125d, 0x1260, 0x128d,
        0x1290, 0x12b5, 0x12b8, 0x12c5, 0x12c8, 0x1315, 0x1318, 0x135a,
        0x135d, 0x137c, 0x1380, 0x1399, 0x13a0, 0x13f5, 0x13f8, 0x13fd,
        0x1400, 0x169c, 0x16a0, 0x16f8, 0x1700, 0x1715, 0x171f, 0x1736,
        0x1740, 0x1753, 0x1760, 0x1773, 0x1780, 0x17dd, 0x17e0, 0x17e9,
        0x17f0, 0x17f9, 0x1800, 0x1819, 0x1820, 0x1878, 0x1880, 0x18aa,
        0x18b0, 0x18f5, 0x1900, 0x192b, 0x1930, 0x193b, 0x1940, 0x1940,
        0x1944, 0x196d, 0x1970, 0x1974, 0x1980, 0x19ab, 0x19b0, 0x19c9,
        0x19d0, 0x19da, 0x19de, 0x1a1b, 0x1a1e, 0x1a7c, 0x1a7f, 0x1a89,
        0x1a90, 0x1a99, 0x1aa0, 0x1aad, 0x1ab0, 0x1ace, 0x1b00, 0x1b4c,
        0x1b50, 0x1bf3, 0x1bfc, 0x1c37, 0x1c3b, 0x1c49, 0x1c4d, 0x1c88,
        0x1c90, 0x1cba, 0x1cbd, 0x1cc7, 0x1cd0, 0x1cfa, 0x1d00, 0x1f15,
        0x1f18, 0x1f1d, 0x1f20, 0x1f45, 0x1f48, 0x1f4d, 0x1f50, 0x1f7d,
        0x1f80, 0x1fd3, 0x1fd6, 0x1fef, 0x1ff2, 0x1ffe, 0x2010, 0x2027,
        0x2030, 0x205e, 0x2070, 0x2071, 0x2074, 0x209c, 0x20a0, 0x20c0,
        0x20d0, 0x20f0, 0x2100, 0x218b, 0x2190, 0x2426, 0x2440, 0x244a,
        0x2460, 0x2b73, 0x2b76, 0x2cf3, 0x2cf9, 0x2d27, 0x2d2d, 0x2d2d,
        0x2d30, 0x2d67, 0x2d6f, 0x2d70, 0x2d7f, 0x2d96, 0x2da0, 0x2e5d,
        0x2e80, 0x2ef3, 0x2f00, 0x2fd5, 0x2ff0, 0x2ffb, 0x3001, 0x3096,
        0x3099, 0x30ff, 0x3105, 0x31e3, 0x31f0, 0xa48c, 0xa490, 0xa4c6,
        0xa4d0, 0xa62b, 0xa640, 0xa6f7, 0xa700, 0xa7ca, 0xa7d0, 0xa7d9,
        0xa7f2, 0xa82c, 0xa830, 0xa839, 0xa840, 0xa877, 0xa880, 0xa8c5,
        0xa8ce, 0xa8d9, 0xa8e0, 0xa953, 0xa95f, 0xa97c, 0xa980, 0xa9d9,
        0xa9de, 0xaa36, 0xaa40, 0xaa4d, 0xaa50, 0xaa59, 0xaa5c, 0xaac2,
        0xaadb, 0xaaf6, 0xab01, 0xab06, 0xab09, 0xab0e, 0xab11, 0xab16,
        0xab20, 0xab6b, 0xab70, 0xabed, 0xabf0, 0xabf9, 0xac00, 0xd7a3,
        0xd7b0, 0xd7c6, 0xd7cb, 0xd7fb, 0xf900, 0xfa6d, 0xfa70, 0xfad9,
        0xfb00, 0xfb06, 0xfb13, 0xfb17, 0xfb1d, 0xfbc2, 0xfbd3, 0xfd8f,
        0xfd92, 0xfdc7, 0xfdcf, 0xfdcf, 0xfdf0, 0xfe19, 0xfe20, 0xfe6b,
        0xfe70, 0xfefc, 0xff01, 0xffbe, 0xffc2, 0xffc7, 0xffca, 0xffcf,
        0xffd2, 0xffd7, 0xffda, 0xffdc, 0xffe0, 0xffee, 0xfffc, 0xfffd,
    ];

    #[rustfmt::skip]
    pub(super) const IS_NOT_PRINT16: &[u16] = &[
        0x00ad, 0x038b, 0x038d, 0x03a2, 0x0530, 0x0590, 0x061c, 0x06dd,
        0x083f, 0x085f, 0x08e2, 0x0984, 0x09a9, 0x09b1, 0x09de, 0x0a04,
        0x0a29, 0x0a31, 0x0a34, 0x0a37, 0x0a3d, 0x0a5d, 0x0a84, 0x0a8e,
        0x0a92, 0x0aa9, 0x0ab1, 0x0ab4, 0x0ac6, 0x0aca, 0x0b00, 0x0b04,
        0x0b29, 0x0b31, 0x0b34, 0x0b5e, 0x0b84, 0x0b91, 0x0b9b, 0x0b9d,
        0x0bc9, 0x0c0d, 0x0c11, 0x0c29, 0x0c45, 0x0c49, 0x0c57, 0x0c8d,
        0x0c91, 0x0ca9, 0x0cb4, 0x0cc5, 0x0cc9, 0x0cdf, 0x0cf0, 0x0d0d,
        0x0d11, 0x0d45, 0x0d49, 0x0d80, 0x0d84, 0x0db2, 0x0dbc, 0x0dd5,
        0x0dd7, 0x0e83, 0x0e85, 0x0e8b, 0x0ea4, 0x0ea6, 0x0ec5, 0x0ec7,
        0x0ecf, 0x0f48, 0x0f98, 0x0fbd, 0x0fcd, 0x10c6, 0x1249, 0x1257,
        0x1259, 0x1289, 0x12b1, 0x12bf, 0x12c1, 0x12d7, 0x1311, 0x1680,
        0x176d, 0x1771, 0x180e, 0x191f, 0x1a5f, 0x1b7f, 0x1f58, 0x1f5a,
        0x1f5c, 0x1f5e, 0x1fb5, 0x1fc5, 0x1fdc, 0x1ff5, 0x208f, 0x2b96,
        0x2d26, 0x2da7, 0x2daf, 0x2db7, 0x2dbf, 0x2dc7, 0x2dcf, 0x2dd7,
        0x2ddf, 0x2e9a, 0x3040, 0x3130, 0x318f, 0x321f, 0xa7d2, 0xa7d4,
        0xa9ce, 0xa9ff, 0xab27, 0xab2f, 0xfb37, 0xfb3d, 0xfb3f, 0xfb42,
        0xfb45, 0xfe53, 0xfe67, 0xfe75, 0xffe7,
    ];

    #[rustfmt::skip]
    pub(super) const IS_PRINT32: &[u32] = &[
        0x010000, 0x01004d, 0x010050, 0x01005d, 0x010080, 0x0100fa, 0x010100, 0x010102,
        0x010107, 0x010133, 0x010137, 0x01019c, 0x0101a0, 0x0101a0, 0x0101d0, 0x0101fd,
        0x010280, 0x01029c, 0x0102a0, 0x0102d0, 0x0102e0, 0x0102fb, 0x010300, 0x010323,
        0x01032d, 0x01034a, 0x010350, 0x01037a, 0x010380, 0x0103c3, 0x0103c8, 0x0103d5,
        0x010400, 0x01049d, 0x0104a0, 0x0104a9, 0x0104b0, 0x0104d3, 0x0104d8, 0x0104fb,
        0x010500, 0x010527, 0x010530, 0x010563, 0x01056f, 0x0105bc, 0x010600, 0x010736,
        0x010740, 0x010755, 0x010760, 0x010767, 0x010780, 0x0107ba, 0x010800, 0x010805,
        0x010808, 0x010838, 0x01083c, 0x01083c, 0x01083f, 0x01089e, 0x0108a7, 0x0108af,
        0x0108e0, 0x0108f5, 0x0108fb, 0x01091b, 0x01091f, 0x010939, 0x01093f, 0x01093f,
        0x010980, 0x0109b7, 0x0109bc, 0x0109cf, 0x0109d2, 0x010a06, 0x010a0c, 0x010a35,
        0x010a38, 0x010a3a, 0x010a3f, 0x010a48, 0x010a50, 0x010a58, 0x010a60, 0x010a9f,
        0x010ac0, 0x010ae6, 0x010aeb, 0x010af6, 0x010b00, 0x010b35, 0x010b39, 0x010b55,
        0x010b58, 0x010b72, 0x010b78, 0x010b91, 0x010b99, 0x010b9c, 0x010ba9, 0x010baf,
        0x010c00, 0x010c48, 0x010c80, 0x010cb2, 0x010cc0, 0x010cf2, 0x010cfa, 0x010d27,
        0x010d30, 0x010d39, 0x010e60, 0x010ead, 0x010eb0, 0x010eb1, 0x010efd, 0x010f27,
        0x010f30, 0x010f59, 0x010f70, 0x010f89, 0x010fb0, 0x010fcb, 0x010fe0, 0x010ff6,
        0x011000, 0x01104d, 0x011052, 0x011075, 0x01107f, 0x0110c2, 0x0110d0, 0x0110e8,
        0x0110f0, 0x0110f9, 0x011100, 0x011147, 0x011150, 0x011176, 0x011180, 0x0111f4,
        0x011200, 0x011241, 0x011280, 0x0112a9, 0x0112b0, 0x0112ea, 0x0112f0, 0x0112f9,
        0x011300, 0x01130c, 0x01130f, 0x011310, 0x011313, 0x011344, 0x011347, 0x011348,
        0x01134b, 0x01134d, 0x011350, 0x011350, 0x011357, 0x011357, 0x01135d, 0x011363,
        0x011366, 0x01136c, 0x011370, 0x011374, 0x011400, 0x011461, 0x011480, 0x0114c7,
        0x0114d0, 0x0114d9, 0x011580, 0x0115b5, 0x0115b8, 0x0115dd, 0x011600, 0x011644,
        0x011650, 0x011659, 0x011660, 0x01166c, 0x011680, 0x0116b9, 0x0116c0, 0x0116c9,
        0x011700, 0x01171a, 0x01171d, 0x01172b, 0x011730, 0x011746, 0x011800, 0x01183b,
        0x0118a0, 0x0118f2, 0x0118ff, 0x011906, 0x011909, 0x011909, 0x01190c, 0x011938,
        0x01193b, 0x011946, 0x011950, 0x011959, 0x0119a0, 0x0119a7, 0x0119aa, 0x0119d7,
        0x0119da, 0x0119e4, 0x011a00, 0x011a47, 0x011a50, 0x011aa2, 0x011ab0, 0x011af8,
        0x011b00, 0x011b09, 0x011c00, 0x011c45, 0x011c50, 0x011c6c, 0x011c70, 0x011c8f,
        0x011c92, 0x011cb6, 0x011d00, 0x011d36, 0x011d3a, 0x011d47, 0x011d50, 0x011d59,
        0x011d60, 0x011d98, 0x011da0, 0x011da9, 0x011ee0, 0x011ef8, 0x011f00, 0x011f3a,
        0x011f3e, 0x011f59, 0x011fb0, 0x011fb0, 0x011fc0, 0x011ff1, 0x011fff, 0x012399,
        0x012400, 0x012474, 0x012480, 0x012543, 0x012f90, 0x012ff2, 0x013000, 0x01342f,
        0x013440, 0x013455, 0x014400, 0x014646, 0x016800, 0x016a38, 0x016a40, 0x016a69,
        0x016a6e, 0x016ac9, 0x016ad0, 0x016aed, 0x016af0, 0x016af5, 0x016b00, 0x016b45,
        0x016b50, 0x016b77, 0x016b7d, 0x016b8f, 0x016e40, 0x016e9a, 0x016f00, 0x016f4a,
        0x016f4f, 0x016f87, 0x016f8f, 0x016f9f, 0x016fe0, 0x016fe4, 0x016ff0, 0x016ff1,
        0x017000, 0x0187f7, 0x018800, 0x018cd5, 0x018d00, 0x018d08, 0x01aff0, 0x01b122,
        0x01b132, 0x01b132, 0x01b150, 0x01b152, 0x01b155, 0x01b155, 0x01b164, 0x01b167,
        0x01b170, 0x01b2fb, 0x01bc00, 0x01bc6a, 0x01bc70, 0x01bc7c, 0x01bc80, 0x01bc88,
        0x01bc90, 0x01bc99, 0x01bc9c, 0x01bc9f, 0x01cf00, 0x01cf2d, 0x01cf30, 0x01cf46,
        0x01cf50, 0x01cfc3, 0x01d000, 0x01d0f5, 0x01d100, 0x01d126, 0x01d129, 0x01d172,
        0x01d17b, 0x01d1ea, 0x01d200, 0x01d245, 0x01d2c0, 0x01d2d3, 0x01d2e0, 0x01d2f3,
        0x01d300, 0x01d356, 0x01d360, 0x01d378, 0x01d400, 0x01d49f, 0x01d4a2, 0x01d4a2,
        0x01d4a5, 0x01d4a6, 0x01d4a9, 0x01d50a, 0x01d50d, 0x01d546, 0x01d54a, 0x01d6a5,
        0x01d6a8, 0x01d7cb, 0x01d7ce, 0x01da8b, 0x01da9b, 0x01daaf, 0x01df00, 0x01df1e,
        0x01df25, 0x01df2a, 0x01e000, 0x01e018, 0x01e01b, 0x01e02a, 0x01e030, 0x01e06d,
        0x01e08f, 0x01e08f, 0x01e100, 0x01e12c, 0x01e130, 0x01e13d, 0x01e140, 0x01e149,
        0x01e14e, 0x01e14f, 0x01e290, 0x01e2ae, 0x01e2c0, 0x01e2f9, 0x01e2ff, 0x01e2ff,
        0x01e4d0, 0x01e4f9, 0x01e7e0, 0x01e8c4, 0x01e8c7, 0x01e8d6, 0x01e900, 0x01e94b,
        0x01e950, 0x01e959, 0x01e95e, 0x01e95f, 0x01ec71, 0x01ecb4, 0x01ed01, 0x01ed3d,
        0x01ee00, 0x01ee24, 0x01ee27, 0x01ee3b, 0x01ee42, 0x01ee42, 0x01ee47, 0x01ee54,
        0x01ee57, 0x01ee64, 0x01ee67, 0x01ee9b, 0x01eea1, 0x01eebb, 0x01eef0, 0x01eef1,
        0x01f000, 0x01f02b, 0x01f030, 0x01f093, 0x01f0a0, 0x01f0ae, 0x01f0b1, 0x01f0f5,
        0x01f100, 0x01f1ad, 0x01f1e6, 0x01f202, 0x01f210, 0x01f23b, 0x01f240, 0x01f248,
        0x01f250, 0x01f251, 0x01f260, 0x01f265, 0x01f300, 0x01f6d7, 0x01f6dc, 0x01f6ec,
        0x01f6f0, 0x01f6fc, 0x01f700, 0x01f776, 0x01f77b, 0x01f7d9, 0x01f7e0, 0x01f7eb,
        0x01f7f0, 0x01f7f0, 0x01f800, 0x01f80b, 0x01f810, 0x01f847, 0x01f850, 0x01f859,
        0x01f860, 0x01f887, 0x01f890, 0x01f8ad, 0x01f8b0, 0x01f8b1, 0x01f900, 0x01fa53,
        0x01fa60, 0x01fa6d, 0x01fa70, 0x01fa7c, 0x01fa80, 0x01fa88, 0x01fa90, 0x01fac5,
        0x01face, 0x01fadb, 0x01fae0, 0x01fae8, 0x01faf0, 0x01faf8, 0x01fb00, 0x01fbca,
        0x01fbf0, 0x01fbf9, 0x020000, 0x02a6df, 0x02a700, 0x02b739, 0x02b740, 0x02b81d,
        0x02b820, 0x02cea1, 0x02ceb0, 0x02ebe0, 0x02f800, 0x02fa1d, 0x030000, 0x03134a,
        0x031350, 0x0323af, 0x0e0100, 0x0e01ef,
    ];

    #[rustfmt::skip]
    pub(super) const IS_NOT_PRINT32: &[u16] = &[
        0x000c, 0x0027, 0x003b, 0x003e, 0x018f, 0x039e, 0x057b, 0x058b,
        0x0593, 0x0596, 0x05a2, 0x05b2, 0x05ba, 0x0786, 0x07b1, 0x0809,
        0x0836, 0x0856, 0x08f3, 0x0a04, 0x0a14, 0x0a18, 0x0e7f, 0x0eaa,
        0x10bd, 0x1135, 0x11e0, 0x1212, 0x1287, 0x1289, 0x128e, 0x129e,
        0x1304, 0x1329, 0x1331, 0x1334, 0x133a, 0x145c, 0x1914, 0x1917,
        0x1936, 0x1c09, 0x1c37, 0x1ca8, 0x1d07, 0x1d0a, 0x1d3b, 0x1d3e,
        0x1d66, 0x1d69, 0x1d8f, 0x1d92, 0x1f11, 0x246f, 0x6a5f, 0x6abf,
        0x6b5a, 0x6b62, 0xaff4, 0xaffc, 0xafff, 0xd455, 0xd49d, 0xd4ad,
        0xd4ba, 0xd4bc, 0xd4c4, 0xd506, 0xd515, 0xd51d, 0xd53a, 0xd53f,
        0xd545, 0xd551, 0xdaa0, 0xe007, 0xe022, 0xe025, 0xe7e7, 0xe7ec,
        0xe7ef, 0xe7ff, 0xee04, 0xee20, 0xee23, 0xee28, 0xee33, 0xee38,
        0xee3a, 0xee48, 0xee4a, 0xee4c, 0xee50, 0xee53, 0xee58, 0xee5a,
        0xee5c, 0xee5e, 0xee60, 0xee63, 0xee6b, 0xee73, 0xee78, 0xee7d,
        0xee7f, 0xee8a, 0xeea4, 0xeeaa, 0xf0c0, 0xf0d0, 0xfabe, 0xfb93,
    ];
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // Transliterated from `TestStripOIDCPrefix`
    // (controller/internal/authorization/metadata_test.go:15-56).
    #[test]
    fn strip_oidc_prefix_cases() {
        let cases = [
            // go: metadata_test.go:20-23
            ("dex:test-user", "test-user"),
            // go: metadata_test.go:24-27
            ("internal:admin", "admin"),
            // go: metadata_test.go:28-31
            ("test-user", "test-user"),
            // go: metadata_test.go:32-35
            ("prefix:with:multiple:colons", "with:multiple:colons"),
            // go: metadata_test.go:36-39
            ("", ""),
            // go: metadata_test.go:40-43
            (
                "dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa",
                "jumpstarter-lab:test-exporter-sa",
            ),
            // go: metadata_test.go:44-47
            ("dex:system:serviceaccount:default:my-sa", "default:my-sa"),
        ];
        for (input, want) in cases {
            assert_eq!(
                strip_oidc_prefix(input),
                want,
                "strip_oidc_prefix({input:?})"
            );
        }
    }

    // Transliterated from `TestNormalizeName`
    // (controller/internal/authorization/metadata_test.go:58-95).
    #[test]
    fn normalize_name_cases() {
        let cases = [
            // go: metadata_test.go:63-66
            ("foo".to_string(), "oidc-foo-2c26b4"),
            // go: metadata_test.go:67-70
            ("foo@example.com".to_string(), "oidc-foo-example-com-321ba1"),
            // go: metadata_test.go:71-74
            (
                "foo@@@@@example.com".to_string(),
                "oidc-foo-example-com-5ac340",
            ),
            // go: metadata_test.go:75-78
            (
                "@foo@example.com@".to_string(),
                "oidc-foo-example-com-5be6ea",
            ),
            // go: metadata_test.go:79-82 (strings.Repeat("foo", 30))
            (
                "foo".repeat(30),
                "oidc-foofoofoofoofoofoofoofoofoofoofoofoof-4ac4a7",
            ),
        ];
        for (input, want) in cases {
            assert_eq!(normalize_name(&input), want, "normalize_name({input:?})");
        }
    }

    // Transliterated from `TestNormalizeOIDCUsername`
    // (controller/internal/authorization/metadata_test.go:97-142).
    #[test]
    fn normalize_oidc_username_cases() {
        let cases = [
            // go: metadata_test.go:102-105
            ("dex:test-exporter-hooks".to_string(), "test-exporter-hooks"),
            // go: metadata_test.go:106-109
            ("internal:admin".to_string(), "admin"),
            // go: metadata_test.go:110-113
            ("dex:foo@example.com".to_string(), "foo-example-com"),
            // go: metadata_test.go:114-117
            ("foo".to_string(), "foo"),
            // go: metadata_test.go:118-121
            ("foo@example.com".to_string(), "foo-example-com"),
            // go: metadata_test.go:122-125 (strings.Repeat("a", 70) -> 63 a's)
            ("a".repeat(70), &"a".repeat(63)),
            // go: metadata_test.go:126-129
            (
                "dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa".to_string(),
                "jumpstarter-lab-test-exporter-sa",
            ),
        ];
        for (input, want) in cases {
            assert_eq!(
                normalize_oidc_username(&input),
                want,
                "normalize_oidc_username({input:?})"
            );
        }
    }

    /// The load-bearing Unicode divergence: Go's simple `unicode.ToLower` maps
    /// `İ` (U+0130) to a single `i`, whereas Rust's full `char::to_lowercase`
    /// yields `"i\u{307}"` (the combining dot would then become a stray `-`).
    /// [`go_to_lower`] must reproduce Go.
    #[test]
    fn dotted_capital_i_lowercases_like_go() {
        assert_eq!(go_to_lower('\u{0130}'), 'i');
        // dex:İstanbul-User -> istanbul-user (not i-stanbul-user).
        assert_eq!(
            normalize_oidc_username("dex:İstanbul-User"),
            "istanbul-user"
        );
    }

    /// A multibyte rune collapses to exactly one `-` (Go regexp is per-rune),
    /// not one `-` per UTF-8 byte.
    #[test]
    fn multibyte_rune_becomes_single_hyphen() {
        // é (2 bytes) between letters -> single hyphen.
        assert_eq!(normalize_oidc_username("a\u{00e9}b"), "a-b");
        // 😀 (4 bytes) -> single hyphen, then trimmed at the edge.
        assert_eq!(normalize_oidc_username("\u{1f600}smile\u{1f600}"), "smile");
    }

    /// `go_quote` must escape the way Go's `%q` does (not Rust's `{:?}`):
    /// `\x7f`, ` `, `\a`, and printable multibyte passthrough.
    #[test]
    fn go_quote_spot_checks() {
        assert_eq!(go_quote("hello"), "\"hello\"");
        assert_eq!(go_quote("a\\b\"c"), "\"a\\\\b\\\"c\"");
        assert_eq!(go_quote("tab\tbell\u{07}"), "\"tab\\tbell\\a\"");
        assert_eq!(go_quote("del\u{7f}"), "\"del\\x7f\"");
        assert_eq!(go_quote("nbsp\u{a0}end"), "\"nbsp\\u00a0end\"");
        assert_eq!(go_quote("tag\u{e0067}"), "\"tag\\U000e0067\"");
        // Printable multibyte passes through verbatim.
        assert_eq!(go_quote("é日本😀"), "\"é日本😀\"");
    }
}
