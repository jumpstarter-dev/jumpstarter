//! Go `time.ParseDuration`-compatible duration parsing for config strings
//! ("1s", "180s", "43800h", ...).
//!
//! Transliterated from Go `src/time/format.go` (`ParseDuration`,
//! `leadingInt`, `leadingFraction`) so that every accept/reject decision and
//! overflow boundary matches Go bit-for-bit. Durations are `i64` nanoseconds,
//! exactly like Go's `time.Duration`.
//!
//! This module is deliberately independent of `jumpstarter-controller-api`'s
//! `GoDuration` (a small duplication keeping this crate dependency-free).

/// One nanosecond, in nanoseconds (Go `time.Nanosecond`).
pub const NANOSECOND: i64 = 1;
/// One microsecond, in nanoseconds (Go `time.Microsecond`).
pub const MICROSECOND: i64 = 1000 * NANOSECOND;
/// One millisecond, in nanoseconds (Go `time.Millisecond`).
pub const MILLISECOND: i64 = 1000 * MICROSECOND;
/// One second, in nanoseconds (Go `time.Second`).
pub const SECOND: i64 = 1000 * MILLISECOND;
/// One minute, in nanoseconds (Go `time.Minute`).
pub const MINUTE: i64 = 60 * SECOND;
/// One hour, in nanoseconds (Go `time.Hour`).
pub const HOUR: i64 = 60 * MINUTE;

/// Parse failure. `Display` reproduces Go's `parseDurationError` strings
/// (`time: invalid duration "x"`, `time: missing unit in duration "x"`,
/// `time: unknown unit "u" in duration "x"`). Quoting uses Rust's `{:?}`,
/// which matches Go's `quote()` for ASCII input (non-ASCII escape sequences
/// may differ — units are ASCII in practice).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ParseDurationError {
    /// Go: `time: invalid duration %q` (bad syntax or overflow).
    #[error("time: invalid duration {0:?}")]
    InvalidDuration(String),
    /// Go: `time: missing unit in duration %q`.
    #[error("time: missing unit in duration {0:?}")]
    MissingUnit(String),
    /// Go: `time: unknown unit %q in duration %q`.
    #[error("time: unknown unit {unit:?} in duration {duration:?}")]
    UnknownUnit { unit: String, duration: String },
}

/// Go `unitMap`: maps a unit suffix (as raw bytes) to its length in
/// nanoseconds. Returns `None` for unknown units.
fn unit_map(u: &[u8]) -> Option<u64> {
    match u {
        b"ns" => Some(NANOSECOND as u64),
        b"us" => Some(MICROSECOND as u64),
        // U+00B5 = micro symbol (UTF-8 0xC2 0xB5)
        [0xC2, 0xB5, b's'] => Some(MICROSECOND as u64),
        // U+03BC = Greek letter mu (UTF-8 0xCE 0xBC)
        [0xCE, 0xBC, b's'] => Some(MICROSECOND as u64),
        b"ms" => Some(MILLISECOND as u64),
        b"s" => Some(SECOND as u64),
        b"m" => Some(MINUTE as u64),
        b"h" => Some(HOUR as u64),
        _ => None,
    }
}

/// Go `leadingInt`: consumes leading `[0-9]*` from s, returning the value and
/// the remainder. Errors on overflow past 1<<63.
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

/// Go `leadingFraction`: consumes leading `[0-9]*` from s, treating it as a
/// fraction `x / scale`. Once the value would overflow, further digits are
/// consumed but ignored (they cannot affect the result).
fn leading_fraction(s: &[u8]) -> (u64, f64, &[u8]) {
    let mut x: u64 = 0;
    let mut scale: f64 = 1.0;
    let mut overflow = false;
    let mut i = 0;
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

/// Parses a duration string with Go `time.ParseDuration` semantics, returning
/// nanoseconds (the exact value a Go `time.Duration` would hold).
///
/// A duration string is a possibly signed sequence of decimal numbers, each
/// with optional fraction and a unit suffix, such as "300ms", "-1.5h" or
/// "2h45m". Valid time units are "ns", "us" (or "µs"), "ms", "s", "m", "h".
pub fn parse_go_duration(input: &str) -> Result<i64, ParseDurationError> {
    // [-+]?([0-9]*(\.[0-9]*)?[a-z]+)+
    let orig = input;
    let mut s = input.as_bytes();
    let mut d: u64 = 0;
    let mut neg = false;

    let invalid = || ParseDurationError::InvalidDuration(orig.to_string());

    // Consume [-+]?
    if let Some(&c) = s.first() {
        if c == b'-' || c == b'+' {
            neg = c == b'-';
            s = &s[1..];
        }
    }
    // Special case: if all that is left is "0", this is zero.
    if s == b"0" {
        return Ok(0);
    }
    if s.is_empty() {
        return Err(invalid());
    }
    while !s.is_empty() {
        // The next character must be [0-9.]
        if !(s[0] == b'.' || s[0].is_ascii_digit()) {
            return Err(invalid());
        }
        // Consume [0-9]*
        let pl = s.len();
        let (mut v, rest) = leading_int(s).map_err(|()| invalid())?;
        s = rest;
        let pre = pl != s.len(); // whether we consumed anything before a period

        // Consume (\.[0-9]*)?
        let mut f: u64 = 0;
        let mut scale: f64 = 1.0;
        let mut post = false;
        if !s.is_empty() && s[0] == b'.' {
            s = &s[1..];
            let pl = s.len();
            let (xf, xscale, rest) = leading_fraction(s);
            f = xf;
            scale = xscale;
            s = rest;
            post = pl != s.len();
        }
        if !pre && !post {
            // no digits (e.g. ".s" or "-.s")
            return Err(invalid());
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
            return Err(ParseDurationError::MissingUnit(orig.to_string()));
        }
        let u = &s[..i];
        s = &s[i..];
        let unit = unit_map(u).ok_or_else(|| ParseDurationError::UnknownUnit {
            unit: String::from_utf8_lossy(u).into_owned(),
            duration: orig.to_string(),
        })?;
        if v > (1u64 << 63) / unit {
            // overflow
            return Err(invalid());
        }
        v *= unit;
        if f > 0 {
            // float64 is needed to be nanosecond accurate for fractions of hours.
            // v >= 0 && (f*unit/scale) <= 3.6e+12 (ns/h, h is the largest unit)
            v = v.wrapping_add((f as f64 * (unit as f64 / scale)) as u64);
            if v > 1u64 << 63 {
                // overflow
                return Err(invalid());
            }
        }
        d = d.wrapping_add(v);
        if d > 1u64 << 63 {
            return Err(invalid());
        }
    }
    if neg {
        // d <= 1<<63 here; -(1<<63) is exactly i64::MIN.
        return Ok((d as i128).wrapping_neg() as i64);
    }
    if d > (1u64 << 63) - 1 {
        return Err(invalid());
    }
    Ok(d as i64)
}

/// ParseDuration is a helper to parse duration strings with better error
/// messages. Ported from `controller/internal/config/types.go`: unlike
/// [`parse_go_duration`] (and Go `time.ParseDuration`), an empty string is
/// accepted and yields zero — "unset" duration fields in the ConfigMap decode
/// to `""`.
pub fn parse_config_duration(s: &str) -> Result<i64, ParseDurationError> {
    if s.is_empty() {
        return Ok(0);
    }
    parse_go_duration(s)
}

/// Converts a parsed duration (nanoseconds) into a `std::time::Duration`.
/// Returns `None` for negative values (`std::time::Duration` is unsigned).
pub fn to_std_duration(nanos: i64) -> Option<std::time::Duration> {
    u64::try_from(nanos)
        .ok()
        .map(std::time::Duration::from_nanos)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Ported from Go `parseDurationTests` (src/time/time_test.go), plus the
    /// values that actually appear in jumpstarter ConfigMaps.
    #[test]
    fn parse_duration_table() {
        #[rustfmt::skip]
        let cases: &[(&str, i64)] = &[
            // simple
            ("0", 0),
            ("5s", 5 * SECOND),
            ("30s", 30 * SECOND),
            ("1478s", 1478 * SECOND),
            // sign
            ("-5s", -5 * SECOND),
            ("+5s", 5 * SECOND),
            ("-0", 0),
            ("+0", 0),
            // decimal
            ("5.0s", 5 * SECOND),
            ("5.6s", 5 * SECOND + 600 * MILLISECOND),
            ("5.s", 5 * SECOND),
            (".5s", 500 * MILLISECOND),
            ("1.0s", SECOND),
            ("1.00s", SECOND),
            ("1.004s", SECOND + 4 * MILLISECOND),
            ("1.0040s", SECOND + 4 * MILLISECOND),
            ("100.00100s", 100 * SECOND + MILLISECOND),
            // different units
            ("10ns", 10 * NANOSECOND),
            ("11us", 11 * MICROSECOND),
            ("12µs", 12 * MICROSECOND), // U+00B5
            ("12μs", 12 * MICROSECOND), // U+03BC
            ("13ms", 13 * MILLISECOND),
            ("14s", 14 * SECOND),
            ("15m", 15 * MINUTE),
            ("16h", 16 * HOUR),
            // composite durations
            ("3h30m", 3 * HOUR + 30 * MINUTE),
            ("10.5s4m", 4 * MINUTE + 10 * SECOND + 500 * MILLISECOND),
            ("-2m3.4s", -(2 * MINUTE + 3 * SECOND + 400 * MILLISECOND)),
            ("1h2m3s4ms5us6ns", HOUR + 2 * MINUTE + 3 * SECOND + 4 * MILLISECOND + 5 * MICROSECOND + 6 * NANOSECOND),
            ("39h9m14.425s", 39 * HOUR + 9 * MINUTE + 14 * SECOND + 425 * MILLISECOND),
            // large value
            ("52763797000ns", 52_763_797_000 * NANOSECOND),
            // more than 9 digits after decimal point, see golang.org/issue/6617
            ("0.3333333333333333333h", 20 * MINUTE),
            // 9007199254740993 = 1<<53+1 cannot be stored precisely in a float64
            ("9007199254740993ns", (1 << 53) + 1),
            // largest duration that can be represented by int64 in nanoseconds
            ("9223372036854775807ns", i64::MAX),
            ("9223372036854775.807us", i64::MAX),
            ("9223372036854ms775us807ns", i64::MAX),
            ("-9223372036854775808ns", i64::MIN),
            // largest negative value
            ("-9223372036854775.808us", i64::MIN),
            // largest negative round trip value, see golang.org/issue/48629
            ("-2562047h47m16.854775808s", i64::MIN),
            // huge string; issue golang.org/issue/15011.
            ("0.100000000000000000000h", 6 * MINUTE),
            // This value tests the first overflow check in leadingFraction.
            ("0.830103483285477580700h", 49 * MINUTE + 48 * SECOND + 372_539_827 * NANOSECOND),
            // jumpstarter ConfigMap values
            ("1s", SECOND),
            ("10s", 10 * SECOND),
            ("180s", 180 * SECOND),
            ("43800h", 43800 * HOUR),
            ("43800h0m0s", 43800 * HOUR), // metav1.Duration.String() form
            ("720h", 720 * HOUR),
            ("5m", 5 * MINUTE),
        ];
        for (input, want) in cases {
            match parse_go_duration(input) {
                Ok(got) => assert_eq!(got, *want, "parse_go_duration({input:?})"),
                Err(err) => panic!("parse_go_duration({input:?}) unexpected error: {err}"),
            }
        }
    }

    /// Ported from Go `parseDurationErrorTests` (src/time/time_test.go);
    /// every input Go rejects must be rejected here with the same message.
    #[test]
    fn parse_duration_errors() {
        let invalid = |s: &str| ParseDurationError::InvalidDuration(s.to_string());
        let missing_unit = |s: &str| ParseDurationError::MissingUnit(s.to_string());
        let cases: &[(&str, ParseDurationError)] = &[
            // invalid
            ("", invalid("")),
            ("3", missing_unit("3")),
            ("-", invalid("-")),
            ("s", invalid("s")),
            (".", invalid(".")),
            ("-.", invalid("-.")),
            (".s", invalid(".s")),
            ("+.s", invalid("+.s")),
            (
                "1d",
                ParseDurationError::UnknownUnit {
                    unit: "d".into(),
                    duration: "1d".into(),
                },
            ),
            (
                "30d",
                ParseDurationError::UnknownUnit {
                    unit: "d".into(),
                    duration: "30d".into(),
                },
            ),
            ("invalid", invalid("invalid")),
            // overflow
            ("9223372036854775810ns", invalid("9223372036854775810ns")),
            ("9223372036854775808ns", invalid("9223372036854775808ns")),
            ("9223372036854775.808us", invalid("9223372036854775.808us")),
            (
                "9223372036854ms775us808ns",
                invalid("9223372036854ms775us808ns"),
            ),
            ("3000000h", invalid("3000000h")),
        ];
        for (input, want) in cases {
            match parse_go_duration(input) {
                Ok(got) => panic!("parse_go_duration({input:?}) = {got}, want error {want}"),
                Err(err) => assert_eq!(&err, want, "parse_go_duration({input:?})"),
            }
        }
    }

    /// Error strings must match Go's `parseDurationError.Error()` exactly for
    /// ASCII input.
    #[test]
    fn error_strings_match_go() {
        assert_eq!(
            parse_go_duration("invalid").unwrap_err().to_string(),
            r#"time: invalid duration "invalid""#
        );
        assert_eq!(
            parse_go_duration("3").unwrap_err().to_string(),
            r#"time: missing unit in duration "3""#
        );
        assert_eq!(
            parse_go_duration("30d").unwrap_err().to_string(),
            r#"time: unknown unit "d" in duration "30d""#
        );
    }

    /// Ported from Go `TestParseDuration` (controller/internal/config/types_test.go):
    /// the config helper accepts "" as zero.
    #[test]
    fn parse_config_duration_table() {
        assert_eq!(parse_config_duration("1s"), Ok(SECOND));
        assert_eq!(parse_config_duration("10s"), Ok(10 * SECOND));
        assert_eq!(parse_config_duration("1m"), Ok(MINUTE));
        assert_eq!(parse_config_duration("1h"), Ok(HOUR));
        assert_eq!(parse_config_duration(""), Ok(0)); // empty string returns 0
        assert!(parse_config_duration("invalid").is_err());
    }

    #[test]
    fn to_std_duration_bounds() {
        assert_eq!(
            to_std_duration(SECOND),
            Some(std::time::Duration::from_secs(1))
        );
        assert_eq!(to_std_duration(0), Some(std::time::Duration::ZERO));
        assert_eq!(to_std_duration(-1), None);
    }
}
