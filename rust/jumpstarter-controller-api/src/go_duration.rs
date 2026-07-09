//! Go `time.Duration` wire compatibility.
//!
//! The Go controller stores `LeaseSpec.Duration` as a `metav1.Duration`, which
//! marshals to JSON as the string produced by Go's `time.Duration.String()`
//! (e.g. `"1h30m0s"`) and unmarshals via Go's `time.ParseDuration`. Duration
//! strings are wire-visible (CRD payloads, gRPC-adjacent error messages such as
//! `"duration must be positive, got %v"`), so both directions are ported here
//! verbatim from the Go standard library rather than approximated.
//!
//! Ported from Go's `src/time/time.go` (`Duration.String`, `format`, `fmtFrac`,
//! `fmtInt`, `ParseDuration`, `leadingInt`, `leadingFraction`) and
//! `src/time/format.go` (`quote`), and cross-checked against `go1.26.0` output
//! for the exact strings (including error strings and formatting quirks like
//! `90s` -> `"1m30s"` and fraction-overflow truncation during parse).

use std::fmt;
use std::str::FromStr;

use schemars::{JsonSchema, Schema, SchemaGenerator};
use serde::{Deserialize, Deserializer, Serialize, Serializer};

/// Nanoseconds in a nanosecond (Go `time.Nanosecond`).
pub const NANOSECOND: i64 = 1;
/// Nanoseconds in a microsecond (Go `time.Microsecond`).
pub const MICROSECOND: i64 = 1_000 * NANOSECOND;
/// Nanoseconds in a millisecond (Go `time.Millisecond`).
pub const MILLISECOND: i64 = 1_000 * MICROSECOND;
/// Nanoseconds in a second (Go `time.Second`).
pub const SECOND: i64 = 1_000 * MILLISECOND;
/// Nanoseconds in a minute (Go `time.Minute`).
pub const MINUTE: i64 = 60 * SECOND;
/// Nanoseconds in an hour (Go `time.Hour`).
pub const HOUR: i64 = 60 * MINUTE;

/// A signed duration counted in nanoseconds, exactly like Go's `time.Duration`
/// (`int64` nanoseconds), wrapped so that serde and schemars speak the
/// `metav1.Duration` wire format:
///
/// - serializes to the Go `Duration.String()` representation (`"1h30m0s"`,
///   `"500ms"`, `"-1m30.5s"`, `"0s"`, ...),
/// - deserializes from anything Go's `time.ParseDuration` accepts (optional
///   sign, decimal fractions, unit suffixes `ns`/`us`/`µs`/`μs`/`ms`/`s`/`m`/`h`,
///   concatenated terms like `"2h45m"`),
/// - its JSON schema is a plain `{ "type": "string" }`, matching the
///   controller-gen output for `metav1.Duration` in the golden CRD.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct GoDuration(pub i64);

impl GoDuration {
    /// Construct from a nanosecond count (the Go `time.Duration` value itself).
    pub const fn from_nanos(nanos: i64) -> Self {
        Self(nanos)
    }

    /// Construct from whole seconds.
    pub const fn from_secs(secs: i64) -> Self {
        Self(secs * SECOND)
    }

    /// The underlying nanosecond count (Go `time.Duration` integer value).
    pub const fn nanos(self) -> i64 {
        self.0
    }

    /// Port of Go's `Duration.format`: writes the textual form into the tail
    /// of `buf` and returns the offset of the first byte.
    ///
    /// go: src/time/time.go `func (d Duration) format(buf *[32]byte) int`
    fn format_buf(self, buf: &mut [u8; 32]) -> usize {
        // Largest time is 2540400h10m10.000000000s
        let mut w = buf.len();

        let mut u = self.0.unsigned_abs();
        let neg = self.0 < 0;

        if u < SECOND as u64 {
            // Special case: if duration is smaller than a second,
            // use smaller units, like 1.2ms
            let prec;
            w -= 1;
            buf[w] = b's';
            w -= 1;
            if u == 0 {
                buf[w] = b'0';
                return w;
            } else if u < MICROSECOND as u64 {
                // print nanoseconds
                prec = 0;
                buf[w] = b'n';
            } else if u < MILLISECOND as u64 {
                // print microseconds
                prec = 3;
                // U+00B5 'µ' micro sign == 0xC2 0xB5
                w -= 1; // Need room for two bytes.
                buf[w..w + 2].copy_from_slice("µ".as_bytes());
            } else {
                // print milliseconds
                prec = 6;
                buf[w] = b'm';
            }
            (w, u) = fmt_frac(buf, w, u, prec);
            w = fmt_int(buf, w, u);
        } else {
            w -= 1;
            buf[w] = b's';

            (w, u) = fmt_frac(buf, w, u, 9);

            // u is now integer seconds
            w = fmt_int(buf, w, u % 60);
            u /= 60;

            // u is now integer minutes
            if u > 0 {
                w -= 1;
                buf[w] = b'm';
                w = fmt_int(buf, w, u % 60);
                u /= 60;

                // u is now integer hours
                // Stop at hours because days can be different lengths.
                if u > 0 {
                    w -= 1;
                    buf[w] = b'h';
                    w = fmt_int(buf, w, u);
                }
            }
        }

        if neg {
            w -= 1;
            buf[w] = b'-';
        }

        w
    }
}

/// Formats the fraction of `v / 10**prec` (e.g. `".12345"`) into the tail of
/// `buf[..w]`, omitting trailing zeros. It omits the decimal point too when
/// the fraction is 0. Returns the new write offset and `v / 10**prec`.
///
/// go: src/time/time.go `func fmtFrac(buf []byte, v uint64, prec int) (nw int, nv uint64)`
fn fmt_frac(buf: &mut [u8; 32], mut w: usize, mut v: u64, prec: usize) -> (usize, u64) {
    // Omit trailing zeros up to and including decimal point.
    let mut print = false;
    for _ in 0..prec {
        let digit = v % 10;
        print = print || digit != 0;
        if print {
            w -= 1;
            buf[w] = digit as u8 + b'0';
        }
        v /= 10;
    }
    if print {
        w -= 1;
        buf[w] = b'.';
    }
    (w, v)
}

/// Formats `v` into the tail of `buf[..w]`, returning the new write offset.
///
/// go: src/time/time.go `func fmtInt(buf []byte, v uint64) int`
fn fmt_int(buf: &mut [u8; 32], mut w: usize, mut v: u64) -> usize {
    if v == 0 {
        w -= 1;
        buf[w] = b'0';
    } else {
        while v > 0 {
            w -= 1;
            buf[w] = (v % 10) as u8 + b'0';
            v /= 10;
        }
    }
    w
}

impl fmt::Display for GoDuration {
    /// Formats exactly like Go's `Duration.String()` (e.g. `90 * SECOND`
    /// prints as `"1m30s"`, zero as `"0s"`, sub-second values with ns/µs/ms
    /// units).
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut buf = [0u8; 32];
        let w = self.format_buf(&mut buf);
        f.write_str(std::str::from_utf8(&buf[w..]).expect("Go duration formatting is valid UTF-8"))
    }
}

/// Errors from [`parse_go_duration`], with messages byte-identical to Go's
/// `time.ParseDuration` errors (`go1.26.0` behavior, hex-escaping `quote`).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ParseGoDurationError {
    /// `time: invalid duration "..."`
    #[error("time: invalid duration {}", quote(.0))]
    InvalidDuration(String),
    /// `time: missing unit in duration "..."`
    #[error("time: missing unit in duration {}", quote(.0))]
    MissingUnit(String),
    /// `time: unknown unit "..." in duration "..."`
    #[error("time: unknown unit {} in duration {}", quote(.unit), quote(.input))]
    UnknownUnit {
        /// The unrecognized unit token.
        unit: String,
        /// The full original input.
        input: String,
    },
}

/// Port of the `time` package's private `quote` helper used in its error
/// strings: wraps in double quotes, backslash-escapes `"` and `\`, and
/// hex-escapes (`\xHH` per byte) control and non-ASCII characters.
///
/// go: src/time/format.go `func quote(s string) string`
fn quote(s: &str) -> String {
    const LOWERHEX: &[u8; 16] = b"0123456789abcdef";
    let mut buf = String::with_capacity(s.len() + 2);
    buf.push('"');
    for c in s.chars() {
        if (c as u32) >= 0x80 || c < ' ' {
            // Unprintable or non-ASCII characters: escape each UTF-8 byte.
            let mut utf8 = [0u8; 4];
            for &byte in c.encode_utf8(&mut utf8).as_bytes() {
                buf.push_str("\\x");
                buf.push(LOWERHEX[(byte >> 4) as usize] as char);
                buf.push(LOWERHEX[(byte & 0xf) as usize] as char);
            }
        } else {
            if c == '"' || c == '\\' {
                buf.push('\\');
            }
            buf.push(c);
        }
    }
    buf.push('"');
    buf
}

/// Consumes leading `[0-9]*` from `s`, erroring on overflow past `1<<63`.
///
/// go: src/time/time.go `func leadingInt(s []byte|string) (x uint64, rem, err)`
fn leading_int(s: &[u8]) -> Result<(u64, &[u8]), ()> {
    let mut x: u64 = 0;
    let mut i = 0;
    while i < s.len() {
        let c = s[i];
        if !c.is_ascii_digit() {
            break;
        }
        if x > (1u64 << 63) / 10 {
            // overflow
            return Err(());
        }
        x = x * 10 + u64::from(c - b'0');
        if x > 1u64 << 63 {
            // overflow
            return Err(());
        }
        i += 1;
    }
    Ok((x, &s[i..]))
}

/// Consumes leading `[0-9]*` from `s` as the fraction `x / scale`. Once the
/// accumulator would overflow, remaining digits are consumed but ignored
/// (Go quirk: precision silently truncates, it never errors).
///
/// go: src/time/time.go `func leadingFraction(s string) (x uint64, scale float64, rem string)`
fn leading_fraction(s: &[u8]) -> (u64, f64, &[u8]) {
    let mut i = 0;
    let mut scale = 1f64;
    let mut x: u64 = 0;
    let mut overflow = false;
    while i < s.len() {
        let c = s[i];
        if !c.is_ascii_digit() {
            break;
        }
        i += 1;
        if overflow {
            continue;
        }
        if x > (u64::MAX >> 1) / 10 {
            // It's possible for overflow to give a positive number, so take care.
            overflow = true;
            continue;
        }
        let y = x * 10 + u64::from(c - b'0');
        if y > 1u64 << 63 {
            overflow = true;
            continue;
        }
        x = y;
        scale *= 10.0;
    }
    (x, scale, &s[i..])
}

/// Parses a duration string exactly like Go's `time.ParseDuration`.
///
/// A duration string is a possibly signed sequence of decimal numbers, each
/// with optional fraction and a unit suffix, such as `"300ms"`, `"-1.5h"` or
/// `"2h45m"`. Valid time units are `"ns"`, `"us"` (or `"µs"`/`"μs"`), `"ms"`,
/// `"s"`, `"m"`, `"h"`.
///
/// go: src/time/time.go `func ParseDuration(s string) (Duration, error)`
pub fn parse_go_duration(input: &str) -> Result<GoDuration, ParseGoDurationError> {
    // [-+]?([0-9]*(\.[0-9]*)?[a-z]+)+
    let orig = input;
    let mut s = input.as_bytes();
    let mut d: u64 = 0;
    let mut neg = false;

    // Consume [-+]?
    if !s.is_empty() {
        let c = s[0];
        if c == b'-' || c == b'+' {
            neg = c == b'-';
            s = &s[1..];
        }
    }
    // Special case: if all that is left is "0", this is zero.
    if s == b"0" {
        return Ok(GoDuration(0));
    }
    if s.is_empty() {
        return Err(ParseGoDurationError::InvalidDuration(orig.to_owned()));
    }
    while !s.is_empty() {
        // integers before, after decimal point; value = v + f/scale
        let mut v: u64;
        let mut f: u64 = 0;
        let mut scale: f64 = 1.0;

        // The next character must be [0-9.]
        if !(s[0] == b'.' || s[0].is_ascii_digit()) {
            return Err(ParseGoDurationError::InvalidDuration(orig.to_owned()));
        }
        // Consume [0-9]*
        let pl = s.len();
        (v, s) =
            leading_int(s).map_err(|()| ParseGoDurationError::InvalidDuration(orig.to_owned()))?;
        let pre = pl != s.len(); // whether we consumed anything before a period

        // Consume (\.[0-9]*)?
        let mut post = false;
        if !s.is_empty() && s[0] == b'.' {
            s = &s[1..];
            let pl = s.len();
            (f, scale, s) = leading_fraction(s);
            post = pl != s.len();
        }
        if !pre && !post {
            // no digits (e.g. ".s" or "-.s")
            return Err(ParseGoDurationError::InvalidDuration(orig.to_owned()));
        }

        // Consume unit.
        let mut i = 0;
        while i < s.len() {
            let c = s[i];
            if c == b'.' || c.is_ascii_digit() {
                break;
            }
            i += 1;
        }
        if i == 0 {
            return Err(ParseGoDurationError::MissingUnit(orig.to_owned()));
        }
        let u = &s[..i];
        s = &s[i..];
        let unit: u64 = match u {
            b"ns" => NANOSECOND as u64,
            // U+00B5 = micro sign, U+03BC = Greek letter mu; Go accepts both.
            b"us" | b"\xC2\xB5s" | b"\xCE\xBCs" => MICROSECOND as u64,
            b"ms" => MILLISECOND as u64,
            b"s" => SECOND as u64,
            b"m" => MINUTE as u64,
            b"h" => HOUR as u64,
            _ => {
                return Err(ParseGoDurationError::UnknownUnit {
                    unit: std::str::from_utf8(u)
                        .expect("unit is a substring of valid UTF-8 input")
                        .to_owned(),
                    input: orig.to_owned(),
                });
            }
        };
        if v > (1u64 << 63) / unit {
            // overflow
            return Err(ParseGoDurationError::InvalidDuration(orig.to_owned()));
        }
        v *= unit;
        if f > 0 {
            // f64 is needed to be nanosecond accurate for fractions of hours.
            // v >= 0 && (f*unit/scale) <= 3.6e+12 (ns/h, h is the largest unit)
            v = v.wrapping_add((f as f64 * (unit as f64 / scale)) as u64);
            if v > 1u64 << 63 {
                // overflow
                return Err(ParseGoDurationError::InvalidDuration(orig.to_owned()));
            }
        }
        // Bug-compatible with Go: uint64 addition wraps silently; the
        // out-of-range check below only catches values that stay above 1<<63.
        d = d.wrapping_add(v);
        if d > 1u64 << 63 {
            return Err(ParseGoDurationError::InvalidDuration(orig.to_owned()));
        }
    }
    if neg {
        // d <= 1<<63 here, which maps exactly onto i64::MIN..=0 when negated.
        return Ok(GoDuration((d as i64).wrapping_neg()));
    }
    if d > (1u64 << 63) - 1 {
        return Err(ParseGoDurationError::InvalidDuration(orig.to_owned()));
    }
    Ok(GoDuration(d as i64))
}

impl FromStr for GoDuration {
    type Err = ParseGoDurationError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        parse_go_duration(s)
    }
}

impl Serialize for GoDuration {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.collect_str(self)
    }
}

impl<'de> Deserialize<'de> for GoDuration {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct GoDurationVisitor;

        impl serde::de::Visitor<'_> for GoDurationVisitor {
            type Value = GoDuration;

            fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("a Go time.Duration string such as \"30m\" or \"1h30m0s\"")
            }

            fn visit_str<E: serde::de::Error>(self, v: &str) -> Result<GoDuration, E> {
                parse_go_duration(v).map_err(E::custom)
            }
        }

        deserializer.deserialize_str(GoDurationVisitor)
    }
}

impl JsonSchema for GoDuration {
    fn schema_name() -> std::borrow::Cow<'static, str> {
        "GoDuration".into()
    }

    fn inline_schema() -> bool {
        // Inline as a bare string schema so the CRD matches controller-gen's
        // output for metav1.Duration: `type: string` with no $ref.
        true
    }

    fn json_schema(_generator: &mut SchemaGenerator) -> Schema {
        schemars::json_schema!({ "type": "string" })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Format table. Expected strings verified against `go1.26.0` output;
    /// most cases transliterated from Go's `TestDurationString`
    /// (go: src/time/time_test.go `var durationTests`).
    const FORMAT_CASES: &[(i64, &str)] = &[
        (0, "0s"),
        (1, "1ns"),
        (1_100, "1.1µs"),
        (2_200_000, "2.2ms"),
        (3_300_000_000, "3.3s"),
        (4 * MINUTE + 5 * SECOND, "4m5s"),
        (4 * MINUTE + 5_001 * MILLISECOND, "4m5.001s"),
        (5 * HOUR + 6 * MINUTE + 7_001 * MILLISECOND, "5h6m7.001s"),
        (8 * MINUTE + 1, "8m0.000000001s"),
        (i64::MAX, "2562047h47m16.854775807s"),
        (i64::MIN, "-2562047h47m16.854775808s"),
        // Canonical formatting quirks:
        (90 * SECOND, "1m30s"),
        (-90 * SECOND - 500 * MILLISECOND, "-1m30.5s"),
        (2 * HOUR + 45 * MINUTE, "2h45m0s"),
        (43_800 * HOUR, "43800h0m0s"),
        (999_999_999, "999.999999ms"),
        (1_500 * MICROSECOND, "1.5ms"),
        (100, "100ns"),
        (30 * MINUTE, "30m0s"),
        (-MINUTE - 30 * SECOND, "-1m30s"),
    ];

    #[test]
    fn format_matches_go_duration_string() {
        for &(nanos, expected) in FORMAT_CASES {
            assert_eq!(
                GoDuration(nanos).to_string(),
                expected,
                "formatting {nanos}ns"
            );
        }
    }

    #[test]
    fn format_parse_round_trip() {
        for &(nanos, formatted) in FORMAT_CASES {
            assert_eq!(
                parse_go_duration(formatted),
                Ok(GoDuration(nanos)),
                "round-tripping {formatted:?}"
            );
        }
    }

    /// Parse table verified against `go1.26.0` `time.ParseDuration`.
    const PARSE_CASES: &[(&str, i64)] = &[
        ("30m", 30 * MINUTE),
        ("2h45m0s", 2 * HOUR + 45 * MINUTE),
        ("-1m30s", -MINUTE - 30 * SECOND),
        ("0s", 0),
        ("43800h", 43_800 * HOUR),
        ("1.5h", HOUR + 30 * MINUTE),
        ("-1.5h", -HOUR - 30 * MINUTE),
        ("0", 0),
        ("-0", 0),
        ("+0", 0),
        ("+5s", 5 * SECOND),
        (".5s", 500 * MILLISECOND),
        ("1.s", SECOND),
        ("1h30m", HOUR + 30 * MINUTE),
        ("300ms", 300 * MILLISECOND),
        ("2h45m", 2 * HOUR + 45 * MINUTE),
        // All three microsecond spellings.
        ("3us", 3 * MICROSECOND),
        ("3µs", 3 * MICROSECOND), // U+00B5 micro sign
        ("3μs", 3 * MICROSECOND), // U+03BC Greek small letter mu
        ("100ns", 100),
        // Fraction truncation (integer nanoseconds, always rounded toward zero).
        ("0.5ns", 0),
        ("1.9ns", 1),
        // Extremes.
        ("9223372036854775807ns", i64::MAX),
        ("-9223372036854775808ns", i64::MIN),
        ("2540400h10m10.000000001s", 9_145_440_610_000_000_001),
        // leadingFraction overflow quirks: excess fraction digits are consumed
        // but silently ignored once the accumulator would overflow.
        ("0.100000000000000000000h", 6 * MINUTE),
        ("0.830103483285477580700h", 2_988_372_539_827),
        // Multiple terms accumulate.
        (
            "1h1m1s1ms1us1ns",
            HOUR + MINUTE + SECOND + MILLISECOND + MICROSECOND + 1,
        ),
    ];

    #[test]
    fn parse_matches_go_parse_duration() {
        for &(input, expected) in PARSE_CASES {
            assert_eq!(
                parse_go_duration(input),
                Ok(GoDuration(expected)),
                "parsing {input:?}"
            );
        }
    }

    /// Error strings verified byte-for-byte against `go1.26.0`.
    const PARSE_ERROR_CASES: &[(&str, &str)] = &[
        ("", r#"time: invalid duration """#),
        ("5", r#"time: missing unit in duration "5""#),
        ("1d", r#"time: unknown unit "d" in duration "1d""#),
        ("-.s", r#"time: invalid duration "-.s""#),
        (".s", r#"time: invalid duration ".s""#),
        ("x.s", r#"time: invalid duration "x.s""#),
        ("..2s", r#"time: invalid duration "..2s""#),
        ("1.5.s", r#"time: missing unit in duration "1.5.s""#),
        (" 5s", r#"time: invalid duration " 5s""#),
        ("1h30m ", r#"time: unknown unit "m " in duration "1h30m ""#),
        ("-", r#"time: invalid duration "-""#),
        (
            "9223372036854775808ns",
            r#"time: invalid duration "9223372036854775808ns""#,
        ),
        ("3000000h", r#"time: invalid duration "3000000h""#),
        // Non-ASCII bytes are hex-escaped by Go's time-package quote helper.
        (
            "3µx",
            r#"time: unknown unit "\xc2\xb5x" in duration "3\xc2\xb5x""#,
        ),
    ];

    #[test]
    fn parse_error_strings_match_go() {
        for &(input, expected) in PARSE_ERROR_CASES {
            let err = parse_go_duration(input).expect_err(input);
            assert_eq!(err.to_string(), expected, "error for {input:?}");
        }
    }

    #[test]
    fn serde_round_trip_as_string() {
        let d = GoDuration(HOUR + 30 * MINUTE);
        let json = serde_json::to_string(&d).unwrap();
        assert_eq!(json, r#""1h30m0s""#);
        let back: GoDuration = serde_json::from_str(&json).unwrap();
        assert_eq!(back, d);

        // metav1.Duration accepts any ParseDuration input, not just the
        // canonical form.
        let parsed: GoDuration = serde_json::from_str(r#""1.5h""#).unwrap();
        assert_eq!(parsed, d);

        // Negative and zero round-trips.
        for nanos in [-90 * SECOND, 0, 43_800 * HOUR] {
            let d = GoDuration(nanos);
            let json = serde_json::to_string(&d).unwrap();
            assert_eq!(serde_json::from_str::<GoDuration>(&json).unwrap(), d);
        }
    }

    #[test]
    fn serde_rejects_non_strings_and_bad_durations() {
        assert!(serde_json::from_str::<GoDuration>("3600").is_err());
        let err = serde_json::from_str::<GoDuration>(r#""1d""#).unwrap_err();
        assert!(
            err.to_string()
                .contains(r#"time: unknown unit "d" in duration "1d""#),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn json_schema_is_plain_string() {
        let mut generator = SchemaGenerator::default();
        let schema = GoDuration::json_schema(&mut generator);
        assert_eq!(schema.to_value(), serde_json::json!({ "type": "string" }));
    }
}
