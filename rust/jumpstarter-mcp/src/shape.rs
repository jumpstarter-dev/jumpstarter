//! Reshape the controller's raw lease/exporter JSON into the MCP tool output
//! (ports the Python `jumpstarter_mcp/tools/leases.py` helpers).

use std::collections::HashMap;

use serde_json::{json, Map, Value};

/// Derive a human-readable status from a lease's `conditions` (list of {type,status}).
pub fn lease_status(lease: &Value) -> &'static str {
    if let Some(conds) = lease.get("conditions").and_then(Value::as_array) {
        for c in conds {
            let ty = c.get("type").and_then(Value::as_str).unwrap_or("");
            let st = c.get("status").and_then(Value::as_str).unwrap_or("");
            if ty == "Ready" && st == "True" {
                return "ready";
            }
            if ty == "Pending" && st == "True" {
                return "pending";
            }
            if ty == "Unsatisfiable" && st == "True" {
                return "unsatisfiable";
            }
        }
    }
    "unknown"
}

fn f64_field(v: &Value, k: &str) -> Option<f64> {
    v.get(k).and_then(Value::as_f64)
}

/// Format a Unix epoch (seconds) as an ISO-8601 / RFC3339 UTC timestamp.
fn iso(epoch: Option<f64>) -> Option<String> {
    let e = epoch?;
    let dt = time::OffsetDateTime::from_unix_timestamp(e as i64).ok()?;
    dt.format(&time::format_description::well_known::Rfc3339).ok()
}

/// Format a duration in seconds as `H:MM:SS` (matching Python `str(timedelta)`).
fn duration_str(seconds: Option<f64>) -> Option<String> {
    let s = seconds? as i64;
    Some(format!("{}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60))
}

/// The per-exporter lease summary embedded in `list_exporters` output.
fn lease_summary(lease: &Value) -> Value {
    json!({
        "name": lease.get("name"),
        "client": lease.get("client"),
        "status": lease_status(lease),
        "duration": duration_str(f64_field(lease, "duration_seconds")),
        "begin_time": iso(f64_field(lease, "begin_time_epoch")),
        "end_time": iso(f64_field(lease, "end_time_epoch")),
    })
}

/// Index ready leases by their exporter name.
pub fn active_leases_by_exporter(leases: &[Value]) -> HashMap<String, Value> {
    let mut map = HashMap::new();
    for l in leases {
        if lease_status(l) == "ready" {
            if let Some(exp) = l.get("exporter").and_then(Value::as_str) {
                map.insert(exp.to_string(), l.clone());
            }
        }
    }
    map
}

/// Shape the exporter list, optionally attaching online status + each one's active lease.
pub fn shape_exporters(
    exporters: &[Value],
    active: &HashMap<String, Value>,
    include_leases: bool,
    include_online: bool,
) -> Vec<Value> {
    exporters
        .iter()
        .map(|e| {
            let name = e.get("name").and_then(Value::as_str).unwrap_or("");
            let mut entry = Map::new();
            entry.insert("name".into(), json!(name));
            entry.insert("labels".into(), e.get("labels").cloned().unwrap_or_else(|| json!({})));
            if include_online {
                entry.insert("online".into(), e.get("online").cloned().unwrap_or(json!(false)));
            }
            if let Some(status) = e.get("status") {
                if !status.is_null() {
                    entry.insert("status".into(), status.clone());
                }
            }
            if include_leases {
                let lease = active.get(name).map(lease_summary).unwrap_or(Value::Null);
                entry.insert("lease".into(), lease);
            }
            Value::Object(entry)
        })
        .collect()
}

/// Shape the lease list for `jmp_list_leases`.
pub fn shape_leases(leases: &[Value]) -> Vec<Value> {
    leases
        .iter()
        .map(|l| {
            json!({
                "name": l.get("name"),
                "client": l.get("client"),
                "exporter": l.get("exporter"),
                "selector": l.get("selector"),
                "status": lease_status(l),
                "begin_time": iso(f64_field(l, "begin_time_epoch")),
                "end_time": iso(f64_field(l, "end_time_epoch")),
                "duration": duration_str(f64_field(l, "duration_seconds")),
            })
        })
        .collect()
}
