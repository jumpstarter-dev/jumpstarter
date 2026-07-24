//! Lightweight structured logger for jumpstarter-exec.
//!
//! Field names follow JEP-0013 / PR #865 conventions used by the Python
//! exporter (`structlog`) and Go controllers (`zap`):
//!   - `level`      — "debug" | "info" | "warn" | "error"
//!   - `ts`         — ISO-8601 UTC timestamp
//!   - `msg`        — human-readable event message
//!   - `component`  — default "jumpstarter-exec"; override via log context
//!
//! Persistent context fields (exporter, namespace, component, …) are set at
//! process start — the Rust equivalent of `set_persistent_log_context` —
//! and appear on every log line. Event-specific fields may override them.
//!
//! No external logging crates: we already depend on `serde_json`.

use std::collections::BTreeMap;
use std::io::{self, Write};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Map, Value};

/// Default component when none is supplied via log context.
pub const DEFAULT_COMPONENT: &str = "jumpstarter-exec";

/// Maximum number of bytes included in an I/O preview when debug is on.
pub const IO_PREVIEW_MAX: usize = 128;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LogFormat {
    Json,
    Text,
}

impl LogFormat {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "json" => Some(Self::Json),
            "text" => Some(Self::Text),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct Logger {
    format: LogFormat,
    debug: bool,
    /// Persistent correlation fields (JEP-0013), e.g. exporter, namespace.
    context: BTreeMap<String, String>,
}

impl Logger {
    pub fn new(format: LogFormat, debug: bool, context: BTreeMap<String, String>) -> Self {
        let mut context = context;
        context
            .entry("component".to_string())
            .or_insert_with(|| DEFAULT_COMPONENT.to_string());
        Self {
            format,
            debug,
            context,
        }
    }

    pub fn debug_enabled(&self) -> bool {
        self.debug
    }

    pub fn info(&self, msg: &str, fields: &[(&str, Value)]) {
        self.emit("info", msg, fields);
    }

    pub fn warn(&self, msg: &str, fields: &[(&str, Value)]) {
        self.emit("warn", msg, fields);
    }

    pub fn error(&self, msg: &str, fields: &[(&str, Value)]) {
        self.emit("error", msg, fields);
    }

    /// Debug-level event; no-op unless debug mode is enabled.
    pub fn debug(&self, msg: &str, fields: &[(&str, Value)]) {
        if self.debug {
            self.emit("debug", msg, fields);
        }
    }

    fn emit(&self, level: &str, msg: &str, fields: &[(&str, Value)]) {
        let ts = now_ts();
        match self.format {
            LogFormat::Json => write_json(level, &ts, msg, &self.context, fields),
            LogFormat::Text => write_text(level, &ts, msg, &self.context, fields),
        }
    }
}

fn write_json(
    level: &str,
    ts: &str,
    msg: &str,
    context: &BTreeMap<String, String>,
    fields: &[(&str, Value)],
) {
    let mut map = Map::new();
    map.insert("level".into(), json!(level));
    map.insert("ts".into(), json!(ts));
    map.insert("msg".into(), json!(msg));
    for (k, v) in context {
        map.insert(k.clone(), json!(v));
    }
    for (k, v) in fields {
        map.insert((*k).into(), v.clone());
    }
    let mut line = Value::Object(map).to_string();
    line.push('\n');
    let _ = io::stderr().write_all(line.as_bytes());
}

fn write_text(
    level: &str,
    ts: &str,
    msg: &str,
    context: &BTreeMap<String, String>,
    fields: &[(&str, Value)],
) {
    let component = context
        .get("component")
        .map(|s| s.as_str())
        .unwrap_or(DEFAULT_COMPONENT);
    let mut out = format!("{ts} {level:>5} [{component}] {msg}");
    for (k, v) in context {
        if k == "component" {
            continue;
        }
        out.push_str(&format!(" {k}={v}"));
    }
    for (k, v) in fields {
        match v {
            Value::String(s) => out.push_str(&format!(" {k}={s}")),
            other => out.push_str(&format!(" {k}={other}")),
        }
    }
    out.push('\n');
    let _ = io::stderr().write_all(out.as_bytes());
}

/// Parse `key=value` pairs from a comma-separated string
/// (`exporter=foo,namespace=bar`). Empty entries are skipped.
/// Values may not contain commas.
pub fn parse_log_fields(s: &str) -> Result<BTreeMap<String, String>, String> {
    let mut out = BTreeMap::new();
    for part in s.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let (k, v) = part
            .split_once('=')
            .ok_or_else(|| format!("invalid log field {part:?}: expected key=value"))?;
        let k = k.trim();
        let v = v.trim();
        if k.is_empty() {
            return Err(format!("invalid log field {part:?}: empty key"));
        }
        out.insert(k.to_string(), v.to_string());
    }
    Ok(out)
}

/// Build a printable, truncated preview of binary I/O for debug logs.
pub fn io_preview(data: &[u8]) -> String {
    let truncated = data.len() > IO_PREVIEW_MAX;
    let slice = &data[..data.len().min(IO_PREVIEW_MAX)];
    let mut s: String = slice
        .iter()
        .map(|&b| {
            if (0x20..0x7f).contains(&b) {
                b as char
            } else if b == b'\n' {
                '⏎'
            } else if b == b'\t' {
                '⇥'
            } else {
                '.'
            }
        })
        .collect();
    if truncated {
        s.push('…');
    }
    s
}

/// UTC RFC3339 timestamp with millisecond precision (matches structlog `ts`).
fn now_ts() -> String {
    let dur = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = dur.as_secs() as i64;
    let millis = dur.subsec_millis();
    let (year, month, day, hour, min, sec) = civil_from_unix(secs);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{min:02}:{sec:02}.{millis:03}Z")
}

/// Convert Unix seconds to (year, month, day, hour, min, sec) in UTC.
/// Algorithm adapted from Howard Hinnant's `civil_from_days`.
fn civil_from_unix(secs: i64) -> (i32, u32, u32, u32, u32, u32) {
    let days = secs.div_euclid(86_400);
    let tod = secs.rem_euclid(86_400) as u32;
    let hour = tod / 3600;
    let min = (tod % 3600) / 60;
    let sec = tod % 60;

    // Shift epoch from 1970-01-01 to 0000-03-01 for the algorithm.
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = (yoe as i64) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as i32, m, d, hour, min, sec)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn io_preview_printable_and_truncates() {
        assert_eq!(io_preview(b"hello"), "hello");
        assert_eq!(io_preview(b"a\nb\tc"), "a⏎b⇥c");
        let big = vec![b'x'; IO_PREVIEW_MAX + 10];
        let p = io_preview(&big);
        assert!(p.ends_with('…'));
        assert_eq!(p.chars().count(), IO_PREVIEW_MAX + 1);
    }

    #[test]
    fn civil_from_unix_epoch() {
        let (y, m, d, h, mi, s) = civil_from_unix(0);
        assert_eq!((y, m, d, h, mi, s), (1970, 1, 1, 0, 0, 0));
    }

    #[test]
    fn civil_from_unix_known_date() {
        // 2026-07-24T11:12:35Z
        let (y, m, d, h, mi, s) = civil_from_unix(1_784_891_555);
        assert_eq!((y, m, d, h, mi, s), (2026, 7, 24, 11, 12, 35));
    }

    #[test]
    fn log_format_parse() {
        assert_eq!(LogFormat::parse("json"), Some(LogFormat::Json));
        assert_eq!(LogFormat::parse("text"), Some(LogFormat::Text));
        assert_eq!(LogFormat::parse("xml"), None);
    }

    #[test]
    fn parse_log_fields_ok() {
        let m = parse_log_fields("exporter=demo-abc,namespace=jumpstarter-lab,component=exporter")
            .unwrap();
        assert_eq!(m.get("exporter").map(String::as_str), Some("demo-abc"));
        assert_eq!(
            m.get("namespace").map(String::as_str),
            Some("jumpstarter-lab")
        );
        assert_eq!(m.get("component").map(String::as_str), Some("exporter"));
    }

    #[test]
    fn parse_log_fields_rejects_bad_pair() {
        assert!(parse_log_fields("noequals").is_err());
        assert!(parse_log_fields("=novalue").is_err());
    }

    #[test]
    fn logger_defaults_component() {
        let log = Logger::new(LogFormat::Json, false, BTreeMap::new());
        assert_eq!(
            log.context.get("component").map(String::as_str),
            Some(DEFAULT_COMPONENT)
        );
    }

    #[test]
    fn logger_preserves_override_component() {
        let mut ctx = BTreeMap::new();
        ctx.insert("component".into(), "exporter".into());
        let log = Logger::new(LogFormat::Json, false, ctx);
        assert_eq!(
            log.context.get("component").map(String::as_str),
            Some("exporter")
        );
    }
}
