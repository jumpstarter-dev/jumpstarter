//! Display models for the controller resource commands — the Rust port of the
//! pydantic `Exporter`/`Lease`/`ExporterList`/`LeaseList` models in
//! `python/.../client/grpc.py:86-378`.
//!
//! These own *presentation*: building from the wire protos, the table-column
//! computations (`EXPIRES AT`/`REMAINING`/`get_status`), and the JSON/YAML shapes
//! (duration as float seconds, datetimes as ISO strings, conditions rendered like
//! protobuf `MessageToDict`). The gRPC plumbing lives in `jumpstarter-client`.

use std::collections::BTreeMap;

use chrono::{DateTime, Duration as ChronoDuration, Local, SecondsFormat};
use jumpstarter_protocol::client_v1;
use jumpstarter_protocol::v1::{Condition, Time};
use serde::ser::{Serialize, SerializeMap, SerializeSeq, Serializer};

use crate::output::Printable;

const DT_FMT: &str = "%Y-%m-%d %H:%M:%S";

/// Which optional exporter columns/fields to include (`grpc.py:WithOptions`).
#[derive(Debug, Clone, Copy, Default)]
pub struct WithOptions {
    pub show_online: bool,
    pub show_status: bool,
    pub show_leases: bool,
}

// ---- shared conversions ---------------------------------------------------

/// Parse a resource identifier `namespaces/<ns>/<kind>/<name>` into `(ns, name)`,
/// falling back to `("", last-segment)` for malformed input
/// (`grpc.py:parse_identifier`, but non-raising).
fn parse_identifier(identifier: &str, kind: &str) -> (String, String) {
    let segments: Vec<&str> = identifier.split('/').collect();
    if segments.len() == 4 && segments[0] == "namespaces" && segments[2] == kind {
        (segments[1].to_string(), segments[3].to_string())
    } else {
        let name = identifier
            .rsplit('/')
            .next()
            .unwrap_or(identifier)
            .to_string();
        (String::new(), name)
    }
}

fn ts_to_local(ts: &prost_types::Timestamp) -> Option<DateTime<Local>> {
    DateTime::from_timestamp(ts.seconds, ts.nanos.max(0) as u32).map(|u| u.with_timezone(&Local))
}

fn pb_duration(d: &prost_types::Duration) -> ChronoDuration {
    ChronoDuration::seconds(d.seconds) + ChronoDuration::nanoseconds(d.nanos as i64)
}

/// `timedelta.total_seconds()` as a float (microsecond resolution, matching
/// Python's `ser_json_timedelta="float"`).
fn duration_secs(d: &ChronoDuration) -> f64 {
    d.num_microseconds()
        .unwrap_or_else(|| d.num_seconds() * 1_000_000) as f64
        / 1_000_000.0
}

/// `datetime.isoformat()` (offset-aware, microseconds when non-zero).
fn iso(dt: &DateTime<Local>) -> String {
    let fmt = if dt.timestamp_subsec_nanos() == 0 {
        SecondsFormat::Secs
    } else {
        SecondsFormat::Micros
    };
    dt.to_rfc3339_opts(fmt, false)
}

fn sorted_kv(map: &BTreeMap<String, String>) -> String {
    map.iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join(",")
}

fn exporter_status_name(value: i32) -> &'static str {
    match value {
        1 => "OFFLINE",
        2 => "AVAILABLE",
        3 => "BEFORE_LEASE_HOOK",
        4 => "LEASE_READY",
        5 => "AFTER_LEASE_HOOK",
        6 => "BEFORE_LEASE_HOOK_FAILED",
        7 => "AFTER_LEASE_HOOK_FAILED",
        _ => "UNSPECIFIED",
    }
}

// ---- Lease ----------------------------------------------------------------

/// A lease as rendered by the CLI (`grpc.py:Lease`).
#[derive(Debug, Clone)]
pub struct LeaseResource {
    pub namespace: String,
    pub name: String,
    pub selector: String,
    pub exporter_name: Option<String>,
    pub tags: BTreeMap<String, String>,
    pub duration: ChronoDuration,
    pub effective_duration: Option<ChronoDuration>,
    pub begin_time: Option<DateTime<Local>>,
    pub client: String,
    pub exporter: String,
    pub conditions: Vec<Condition>,
    pub effective_begin_time: Option<DateTime<Local>>,
    pub effective_end_time: Option<DateTime<Local>>,
}

impl LeaseResource {
    pub fn from_proto(l: client_v1::Lease) -> Self {
        let (namespace, name) = parse_identifier(&l.name, "leases");
        let client = l
            .client
            .as_deref()
            .filter(|c| !c.is_empty())
            .map(|c| parse_identifier(c, "clients").1)
            .unwrap_or_default();
        let exporter = l
            .exporter
            .as_deref()
            .filter(|e| !e.is_empty())
            .map(|e| parse_identifier(e, "exporters").1)
            .unwrap_or_default();
        Self {
            namespace,
            name,
            selector: l.selector,
            exporter_name: l.exporter_name.filter(|s| !s.is_empty()),
            tags: l.tags.into_iter().collect(),
            duration: l
                .duration
                .as_ref()
                .map(pb_duration)
                .unwrap_or_else(ChronoDuration::zero),
            effective_duration: l.effective_duration.as_ref().map(pb_duration),
            begin_time: l.begin_time.as_ref().and_then(ts_to_local),
            client,
            exporter,
            conditions: l.conditions,
            effective_begin_time: l.effective_begin_time.as_ref().and_then(ts_to_local),
            effective_end_time: l.effective_end_time.as_ref().and_then(ts_to_local),
        }
    }

    /// `_compute_expires_at` (`grpc.py:214`).
    fn compute_expires_at(&self) -> Option<DateTime<Local>> {
        if let Some(t) = self.effective_end_time {
            return Some(t);
        }
        if !self.duration.is_zero() {
            if let Some(b) = self.effective_begin_time {
                return Some(b + self.duration);
            }
            if let Some(b) = self.begin_time {
                return Some(b + self.duration);
            }
        }
        None
    }

    /// `_format_remaining` (`grpc.py:223`).
    fn format_remaining(expires: Option<DateTime<Local>>) -> String {
        let Some(expires) = expires else {
            return String::new();
        };
        let remaining = expires - Local::now();
        if remaining.num_seconds() <= 0 {
            return "expired".to_string();
        }
        let total = remaining.num_seconds();
        let days = total / 86_400;
        let within_day = total % 86_400;
        let hours = within_day / 3_600;
        let minutes = (within_day % 3_600) / 60;
        let mut parts = Vec::new();
        if days != 0 {
            parts.push(format!("{days}d"));
        }
        if hours != 0 {
            parts.push(format!("{hours}h"));
        }
        if minutes != 0 || parts.is_empty() {
            parts.push(format!("{minutes}m"));
        }
        parts.join(" ")
    }

    /// `get_status` (`grpc.py:263`).
    pub fn get_status(&self) -> String {
        if self.effective_end_time.is_some() {
            return "Ended".to_string();
        }
        let Some(latest) = self.conditions.last() else {
            return "Unknown".to_string();
        };
        let ty = latest.r#type.as_deref().unwrap_or("");
        let status = latest.status.as_deref().unwrap_or("");
        match (ty, status) {
            ("Ready", "True") => "In-Use".to_string(),
            ("Ready", "False") => "Waiting".to_string(),
            ("Expired", _) => "Expired".to_string(),
            _ => latest
                .reason
                .clone()
                .filter(|r| !r.is_empty())
                .unwrap_or_else(|| "Unknown".to_string()),
        }
    }

    /// The expected release-time cell for an exporter's `--with leases` column.
    fn release_time(&self) -> String {
        if let Some(t) = self.effective_end_time {
            t.format(DT_FMT).to_string()
        } else if let Some(b) = self.effective_begin_time {
            (b + self.duration).format(DT_FMT).to_string()
        } else if let Some(b) = self.begin_time {
            (b + self.duration).format(DT_FMT).to_string()
        } else {
            String::new()
        }
    }

    /// One table row (`rich_add_rows`, `grpc.py:243`).
    fn row(&self) -> Vec<String> {
        let expires = self.compute_expires_at();
        let expires_str = expires
            .map(|e| e.format(DT_FMT).to_string())
            .unwrap_or_default();
        vec![
            self.name.clone(),
            self.selector.clone(),
            expires_str,
            Self::format_remaining(expires),
            self.client.clone(),
            self.exporter.clone(),
            sorted_kv(&self.tags),
        ]
    }

    fn lease_columns() -> Vec<String> {
        [
            "NAME",
            "SELECTOR",
            "EXPIRES AT",
            "REMAINING",
            "CLIENT",
            "EXPORTER",
            "TAGS",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }
}

impl Serialize for LeaseResource {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut m = serializer.serialize_map(None)?;
        m.serialize_entry("namespace", &self.namespace)?;
        m.serialize_entry("name", &self.name)?;
        m.serialize_entry("selector", &self.selector)?;
        m.serialize_entry("exporter_name", &self.exporter_name)?;
        m.serialize_entry("tags", &self.tags)?;
        m.serialize_entry("duration", &duration_secs(&self.duration))?;
        m.serialize_entry(
            "effective_duration",
            &self.effective_duration.as_ref().map(duration_secs),
        )?;
        m.serialize_entry("begin_time", &self.begin_time.as_ref().map(iso))?;
        m.serialize_entry("client", &self.client)?;
        m.serialize_entry("exporter", &self.exporter)?;
        m.serialize_entry("conditions", &ConditionsJson(&self.conditions))?;
        m.serialize_entry(
            "effective_begin_time",
            &self.effective_begin_time.as_ref().map(iso),
        )?;
        m.serialize_entry(
            "effective_end_time",
            &self.effective_end_time.as_ref().map(iso),
        )?;
        m.end()
    }
}

/// A single lease is itself printable (used by `create`/`update lease`).
impl Printable for LeaseResource {
    fn headers(&self) -> Vec<String> {
        Self::lease_columns()
    }
    fn rows(&self) -> Vec<Vec<String>> {
        vec![self.row()]
    }
    fn names(&self) -> Vec<String> {
        vec![self.name.clone()]
    }
}

// conditions rendered as protobuf `MessageToDict` (camelCase, int64-as-string,
// unset fields omitted).
struct ConditionsJson<'a>(&'a [Condition]);
impl Serialize for ConditionsJson<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut seq = serializer.serialize_seq(Some(self.0.len()))?;
        for c in self.0 {
            seq.serialize_element(&ConditionJson(c))?;
        }
        seq.end()
    }
}

struct ConditionJson<'a>(&'a Condition);
impl Serialize for ConditionJson<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let c = self.0;
        let mut m = serializer.serialize_map(None)?;
        if let Some(v) = &c.r#type {
            m.serialize_entry("type", v)?;
        }
        if let Some(v) = &c.status {
            m.serialize_entry("status", v)?;
        }
        if let Some(v) = &c.observed_generation {
            m.serialize_entry("observedGeneration", &v.to_string())?;
        }
        if let Some(t) = &c.last_transition_time {
            m.serialize_entry("lastTransitionTime", &TimeJson(t))?;
        }
        if let Some(v) = &c.reason {
            m.serialize_entry("reason", v)?;
        }
        if let Some(v) = &c.message {
            m.serialize_entry("message", v)?;
        }
        m.end()
    }
}

struct TimeJson<'a>(&'a Time);
impl Serialize for TimeJson<'_> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut m = serializer.serialize_map(None)?;
        if let Some(v) = &self.0.seconds {
            m.serialize_entry("seconds", &v.to_string())?;
        }
        if let Some(v) = &self.0.nanos {
            m.serialize_entry("nanos", v)?;
        }
        m.end()
    }
}

// ---- Exporter -------------------------------------------------------------

/// An exporter as rendered by the CLI (`grpc.py:Exporter`).
#[derive(Debug, Clone)]
pub struct ExporterResource {
    pub namespace: String,
    pub name: String,
    pub labels: BTreeMap<String, String>,
    pub online: bool,
    /// Status enum value, or `None` when unspecified (0).
    pub status: Option<i32>,
    /// Joined lease for `--with leases` (populated by the `get exporters` command).
    pub lease: Option<LeaseResource>,
}

impl ExporterResource {
    pub fn from_proto(e: client_v1::Exporter) -> Self {
        let (namespace, name) = parse_identifier(&e.name, "exporters");
        let status = (e.status != 0).then_some(e.status);
        Self {
            namespace,
            name,
            labels: e.labels.into_iter().collect(),
            // `online` is deprecated in favor of `status` but the CLI still surfaces
            // it for `--with online` (matching `grpc.py`).
            #[allow(deprecated)]
            online: e.online,
            status,
            lease: None,
        }
    }

    fn row(&self, opt: &WithOptions) -> Vec<String> {
        let mut row = vec![self.name.clone()];
        if opt.show_online {
            row.push(if self.online { "yes" } else { "no" }.to_string());
        }
        if opt.show_status {
            row.push(match self.status {
                Some(v) => exporter_status_name(v).to_string(),
                None => "UNKNOWN".to_string(),
            });
        }
        row.push(sorted_kv(&self.labels));
        if opt.show_leases {
            let (client, status, release) = match &self.lease {
                Some(l) => (l.client.clone(), l.get_status(), l.release_time()),
                None => (String::new(), "Available".to_string(), String::new()),
            };
            row.push(client);
            row.push(status);
            row.push(release);
        }
        row
    }

    /// Per-exporter JSON object honoring the include flags (`grpc.py:330`).
    fn serialize_with<S: Serializer>(
        &self,
        serializer: S,
        opt: &WithOptions,
    ) -> Result<S::Ok, S::Error> {
        let mut m = serializer.serialize_map(None)?;
        m.serialize_entry("namespace", &self.namespace)?;
        m.serialize_entry("name", &self.name)?;
        m.serialize_entry("labels", &self.labels)?;
        if opt.show_online {
            m.serialize_entry("online", &self.online)?;
        }
        if opt.show_status {
            m.serialize_entry("status", &self.status)?;
        }
        if opt.show_leases {
            m.serialize_entry("lease", &self.lease)?;
        }
        m.end()
    }
}

// ---- list wrappers --------------------------------------------------------

/// `get leases` result (`grpc.py:LeaseList`).
pub struct LeaseList {
    pub leases: Vec<LeaseResource>,
}

impl Serialize for LeaseList {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut m = serializer.serialize_map(Some(1))?;
        m.serialize_entry("leases", &self.leases)?;
        m.end()
    }
}

impl Printable for LeaseList {
    fn headers(&self) -> Vec<String> {
        LeaseResource::lease_columns()
    }
    fn rows(&self) -> Vec<Vec<String>> {
        self.leases.iter().map(LeaseResource::row).collect()
    }
    fn names(&self) -> Vec<String> {
        self.leases.iter().map(|l| l.name.clone()).collect()
    }
}

/// `get exporters` result (`grpc.py:ExporterList`).
pub struct ExporterList {
    pub exporters: Vec<ExporterResource>,
    pub options: WithOptions,
}

impl Serialize for ExporterList {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        struct Items<'a>(&'a [ExporterResource], WithOptions);
        impl Serialize for Items<'_> {
            fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
                let mut seq = serializer.serialize_seq(Some(self.0.len()))?;
                for e in self.0 {
                    seq.serialize_element(&Item(e, self.1))?;
                }
                seq.end()
            }
        }
        struct Item<'a>(&'a ExporterResource, WithOptions);
        impl Serialize for Item<'_> {
            fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
                self.0.serialize_with(serializer, &self.1)
            }
        }
        let mut m = serializer.serialize_map(Some(1))?;
        m.serialize_entry("exporters", &Items(&self.exporters, self.options))?;
        m.end()
    }
}

impl Printable for ExporterList {
    fn headers(&self) -> Vec<String> {
        let mut h = vec!["NAME".to_string()];
        if self.options.show_online {
            h.push("ONLINE".to_string());
        }
        if self.options.show_status {
            h.push("STATUS".to_string());
        }
        h.push("LABELS".to_string());
        if self.options.show_leases {
            h.push("LEASED BY".to_string());
            h.push("LEASE STATUS".to_string());
            h.push("RELEASE TIME".to_string());
        }
        h
    }
    fn rows(&self) -> Vec<Vec<String>> {
        self.exporters
            .iter()
            .map(|e| e.row(&self.options))
            .collect()
    }
    fn names(&self) -> Vec<String> {
        self.exporters.iter().map(|e| e.name.clone()).collect()
    }
}
