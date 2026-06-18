//! Value parsers shared by the resource commands: durations, datetimes, tags,
//! and selector joining (`common.py:DurationParamType`/`DateTimeParamType`,
//! `create.py` tag parsing, `_opt_selector_callback`).

use std::time::Duration;

use chrono::{DateTime, Local, NaiveDateTime, TimeZone, Utc};

/// Join repeated `-l/--selector` values into one comma-separated string, or
/// `None` when none were given (`common.py:_opt_selector_callback`).
pub fn join_selector(values: &[String]) -> Option<String> {
    if values.is_empty() {
        None
    } else {
        Some(values.join(","))
    }
}

/// Parse a `--duration` value (`common.py:DurationParamType`). Tries, in order:
/// integer seconds, ISO-8601 duration, `HH:MM:SS`, compact (`1d3h40m`), and word
/// forms (`2 days`). Used as a clap `value_parser`, so an error is an exit-2 usage
/// error.
pub fn parse_duration(value: &str) -> Result<Duration, String> {
    let v = value.trim();

    // 1. plain integer seconds.
    if let Ok(secs) = v.parse::<i64>() {
        return seconds(secs as f64, value);
    }
    // 2/3. the remaining accepted formats.
    if let Some(s) = parse_iso8601(v)
        .or_else(|| parse_colon(v))
        .or_else(|| parse_units(v))
    {
        return seconds(s, value);
    }

    Err(format!(
        "'{value}' is not a valid duration \
         (e.g., '30m', '3h30m', '1d', '1d3h40m', 'PT1H30M', '01:30:00')"
    ))
}

fn seconds(total: f64, value: &str) -> Result<Duration, String> {
    if total < 0.0 || !total.is_finite() {
        return Err(format!("'{value}' is not a valid duration"));
    }
    Ok(Duration::from_secs_f64(total))
}

/// ISO-8601 duration: `P[nW][nD][T[nH][nM][nS]]` (e.g. `PT1H30M`, `P1DT2H30M`).
fn parse_iso8601(v: &str) -> Option<f64> {
    let mut chars = v.chars();
    if !matches!(chars.next(), Some('P') | Some('p')) {
        return None;
    }
    let mut total = 0.0f64;
    let mut in_time = false;
    let mut num = String::new();
    let mut saw_field = false;
    for c in chars {
        match c {
            'T' | 't' => in_time = true,
            '0'..='9' | '.' => num.push(c),
            unit => {
                if num.is_empty() {
                    return None;
                }
                let n: f64 = num.parse().ok()?;
                num.clear();
                let mult = match (unit.to_ascii_uppercase(), in_time) {
                    ('W', false) => 604_800.0,
                    ('D', false) => 86_400.0,
                    ('H', true) => 3_600.0,
                    ('M', true) => 60.0,
                    ('S', true) => 1.0,
                    _ => return None,
                };
                total += n * mult;
                saw_field = true;
            }
        }
    }
    if !num.is_empty() || !saw_field {
        return None;
    }
    Some(total)
}

/// Colon time format: `SS`, `MM:SS`, `HH:MM:SS`, or `DD:HH:MM:SS`.
fn parse_colon(v: &str) -> Option<f64> {
    if !v.contains(':') {
        return None;
    }
    let parts: Vec<&str> = v.split(':').collect();
    if parts.len() < 2 || parts.len() > 4 {
        return None;
    }
    let nums: Option<Vec<f64>> = parts.iter().map(|p| p.trim().parse::<f64>().ok()).collect();
    let nums = nums?;
    let mults: &[f64] = match nums.len() {
        2 => &[60.0, 1.0],
        3 => &[3_600.0, 60.0, 1.0],
        4 => &[86_400.0, 3_600.0, 60.0, 1.0],
        _ => return None,
    };
    Some(nums.iter().zip(mults).map(|(n, m)| n * m).sum())
}

/// Compact / word unit forms: `1d3h40m`, `30m`, `90s`, `2 days`, `1d 3h 40m`.
fn parse_units(v: &str) -> Option<f64> {
    let bytes: Vec<char> = v.chars().collect();
    let mut i = 0;
    let mut total = 0.0f64;
    let mut saw = false;
    while i < bytes.len() {
        while i < bytes.len() && bytes[i].is_whitespace() {
            i += 1;
        }
        if i >= bytes.len() {
            break;
        }
        // number (with optional decimal)
        let start = i;
        while i < bytes.len() && (bytes[i].is_ascii_digit() || bytes[i] == '.') {
            i += 1;
        }
        if i == start {
            return None;
        }
        let num: f64 = bytes[start..i].iter().collect::<String>().parse().ok()?;
        while i < bytes.len() && bytes[i].is_whitespace() {
            i += 1;
        }
        // unit word
        let ustart = i;
        while i < bytes.len() && bytes[i].is_ascii_alphabetic() {
            i += 1;
        }
        if i == ustart {
            return None;
        }
        let unit: String = bytes[ustart..i]
            .iter()
            .collect::<String>()
            .to_ascii_lowercase();
        let mult = match unit.as_str() {
            "w" | "week" | "weeks" => 604_800.0,
            "d" | "day" | "days" => 86_400.0,
            "h" | "hr" | "hrs" | "hour" | "hours" => 3_600.0,
            "m" | "min" | "mins" | "minute" | "minutes" => 60.0,
            "s" | "sec" | "secs" | "second" | "seconds" => 1.0,
            _ => return None,
        };
        total += num * mult;
        saw = true;
    }
    saw.then_some(total)
}

/// Parse a `--begin-time` value (`common.py:DateTimeParamType`): ISO-8601, with
/// naive values interpreted in the local timezone. Returns a protobuf timestamp.
pub fn parse_datetime(value: &str) -> Result<prost_types::Timestamp, String> {
    if let Ok(dt) = DateTime::parse_from_rfc3339(value) {
        return Ok(to_timestamp(dt.with_timezone(&Utc)));
    }
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ] {
        if let Ok(naive) = NaiveDateTime::parse_from_str(value, fmt) {
            let local = Local
                .from_local_datetime(&naive)
                .single()
                .ok_or_else(|| format!("'{value}' is not a valid datetime"))?;
            return Ok(to_timestamp(local.with_timezone(&Utc)));
        }
    }
    Err(format!("'{value}' is not a valid datetime"))
}

fn to_timestamp(dt: DateTime<Utc>) -> prost_types::Timestamp {
    prost_types::Timestamp {
        seconds: dt.timestamp(),
        nanos: dt.timestamp_subsec_nanos() as i32,
    }
}

/// Parse `--tag key=value` pairs (`create.py:85`); a missing `=` is a usage error.
pub fn parse_tags(tags: &[String]) -> Result<std::collections::BTreeMap<String, String>, String> {
    let mut out = std::collections::BTreeMap::new();
    for tag in tags {
        match tag.split_once('=') {
            Some((k, v)) => {
                out.insert(k.to_string(), v.to_string());
            }
            None => {
                return Err(format!("Invalid tag format: '{tag}' (expected key=value)"));
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn secs(v: &str) -> f64 {
        parse_duration(v).unwrap().as_secs_f64()
    }

    #[test]
    fn integer_seconds() {
        assert_eq!(secs("90"), 90.0);
    }

    #[test]
    fn iso8601() {
        assert_eq!(secs("PT1H30M"), 5_400.0);
        assert_eq!(secs("P1DT2H30M"), 86_400.0 + 7_200.0 + 1_800.0);
        assert_eq!(secs("P1W"), 604_800.0);
    }

    #[test]
    fn colon() {
        assert_eq!(secs("01:30:00"), 5_400.0);
        assert_eq!(secs("30:00"), 1_800.0);
    }

    #[test]
    fn compact_and_words() {
        assert_eq!(secs("30m"), 1_800.0);
        assert_eq!(secs("3h30m"), 12_600.0);
        assert_eq!(secs("1d3h40m"), 86_400.0 + 10_800.0 + 2_400.0);
        assert_eq!(secs("2 days"), 172_800.0);
        assert_eq!(secs("1d 3h"), 86_400.0 + 10_800.0);
    }

    #[test]
    fn rejects_garbage() {
        assert!(parse_duration("not-a-duration").is_err());
        assert!(parse_duration("").is_err());
    }

    #[test]
    fn tags_require_equals() {
        assert!(parse_tags(&["k=v".to_string()]).is_ok());
        assert!(parse_tags(&["bad".to_string()]).is_err());
    }
}
