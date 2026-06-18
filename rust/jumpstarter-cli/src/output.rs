//! Output formats for list/get commands (`-o`), reproducing `model_print`
//! (spec 08 §5.1; `jumpstarter_cli_common/print.py:9-76`):
//!
//! - `json` — `serde_json` with indent 4 (Python `json.dumps(..., indent=4)`);
//! - `yaml` — `serde_yaml_ng` (Python `yaml.safe_dump`, indent 2);
//! - `name` — bare names, one per line (machine-parseable; scripts pipe it);
//! - `path` — bare file paths (config commands only);
//! - default — a borderless table, or `No resources found[.| in <ns> namespace.]`.

use std::io::Write;

use clap::ValueEnum;
use serde::Serialize;

/// The `-o` output format. The accepted subset is constrained per command via clap's
/// `value_parser` (e.g. list/get accept json|yaml|name; delete accepts only name).
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum, Default)]
#[value(rename_all = "lower")]
pub enum Format {
    #[default]
    Table,
    Json,
    Yaml,
    Name,
    Path,
}

/// The `opt_output_all` subset (`json|yaml|name`, default table) shared by every
/// `list`/`get` command (`opt.py:130`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
#[value(rename_all = "lower")]
pub enum ListFormat {
    Json,
    Yaml,
    Name,
}

impl ListFormat {
    /// Map an optional `-o` value to a [`Format`] (absent → table).
    pub fn resolve(opt: Option<ListFormat>) -> Format {
        match opt {
            Some(ListFormat::Json) => Format::Json,
            Some(ListFormat::Yaml) => Format::Yaml,
            Some(ListFormat::Name) => Format::Name,
            None => Format::Table,
        }
    }
}

/// The `opt_output_path_only` subset (`path` only) shared by the config
/// create/delete/use commands (`opt.py:150`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
#[value(rename_all = "lower")]
pub enum PathFormat {
    Path,
}

/// A renderable list model: serializable (for json/yaml) and able to project to
/// table rows / bare names / bare paths.
///
/// `headers` takes `&self` because some tables vary their columns at runtime
/// (e.g. `get exporters --with online,status,leases`).
pub trait Printable: Serialize {
    /// Column headers for the table format, verbatim (already cased like Python's
    /// rich column names).
    fn headers(&self) -> Vec<String>;
    /// One row of string cells (header order) per item.
    fn rows(&self) -> Vec<Vec<String>>;
    /// Bare names for `-o name`.
    fn names(&self) -> Vec<String>;
    /// Bare paths for `-o path` (config-file commands).
    fn paths(&self) -> Vec<String> {
        Vec::new()
    }
    /// Message for an empty table (e.g. `No resources found in <ns> namespace.`).
    fn empty_message(&self) -> String {
        "No resources found.".to_string()
    }
}

/// Render `model` in `format` to stdout.
pub fn print<T: Printable>(model: &T, format: Format) -> std::io::Result<()> {
    let mut out = std::io::stdout().lock();
    match format {
        Format::Json => writeln!(out, "{}", to_json(model)?),
        // `to_yaml` ends with a newline (like `safe_dump`); `writeln!` adds the
        // second one that click.echo contributes.
        Format::Yaml => writeln!(out, "{}", to_yaml(model)?),
        Format::Name => {
            for name in model.names() {
                writeln!(out, "{name}")?;
            }
            Ok(())
        }
        Format::Path => {
            for path in model.paths() {
                writeln!(out, "{path}")?;
            }
            Ok(())
        }
        Format::Table => {
            let rows = model.rows();
            if rows.is_empty() {
                return writeln!(out, "{}", model.empty_message());
            }
            write!(out, "{}", render_table(&model.headers(), &rows))
        }
    }
}

/// Serialize as pretty JSON with a 4-space indent (Python `json.dumps(indent=4)`).
pub fn to_json<T: Serialize>(model: &T) -> std::io::Result<String> {
    let mut buf = Vec::new();
    let formatter = serde_json::ser::PrettyFormatter::with_indent(b"    ");
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, formatter);
    model
        .serialize(&mut ser)
        .map_err(|e| std::io::Error::other(e.to_string()))?;
    String::from_utf8(buf).map_err(|e| std::io::Error::other(e.to_string()))
}

/// Serialize as YAML with keys sorted recursively — Python's `model_print` always
/// routes `-o yaml` through `yaml.safe_dump` (default `sort_keys=True`), so every
/// mapping is alphabetized regardless of declaration order.
pub fn to_yaml<T: Serialize>(model: &T) -> std::io::Result<String> {
    let value: serde_yaml_ng::Value =
        serde_yaml_ng::to_value(model).map_err(|e| std::io::Error::other(e.to_string()))?;
    let sorted = sort_yaml(value);
    serde_yaml_ng::to_string(&sorted).map_err(|e| std::io::Error::other(e.to_string()))
}

fn sort_yaml(value: serde_yaml_ng::Value) -> serde_yaml_ng::Value {
    use serde_yaml_ng::Value;
    match value {
        Value::Mapping(map) => {
            let mut entries: Vec<(Value, Value)> =
                map.into_iter().map(|(k, v)| (k, sort_yaml(v))).collect();
            entries.sort_by(|a, b| yaml_key(&a.0).cmp(&yaml_key(&b.0)));
            let mut out = serde_yaml_ng::Mapping::new();
            for (k, v) in entries {
                out.insert(k, v);
            }
            Value::Mapping(out)
        }
        Value::Sequence(seq) => Value::Sequence(seq.into_iter().map(sort_yaml).collect()),
        other => other,
    }
}

fn yaml_key(v: &serde_yaml_ng::Value) -> String {
    match v {
        serde_yaml_ng::Value::String(s) => s.clone(),
        other => format!("{other:?}"),
    }
}

/// Render a borderless table: no leading edge, two spaces between columns,
/// left-aligned, columns padded to their widest cell (the trailing column is not
/// padded). Approximates rich's `box=None, pad_edge=False`; unlike rich, it does
/// not truncate to the terminal width.
fn render_table(headers: &[String], rows: &[Vec<String>]) -> String {
    let ncol = headers.len();
    let mut widths = vec![0usize; ncol];
    for (i, h) in headers.iter().enumerate() {
        widths[i] = h.chars().count();
    }
    for row in rows {
        for (i, cell) in row.iter().enumerate() {
            if i < ncol {
                widths[i] = widths[i].max(cell.chars().count());
            }
        }
    }
    let render_row = |cells: &[String]| -> String {
        let mut line = String::new();
        for (i, cell) in cells.iter().enumerate() {
            if i > 0 {
                line.push_str("  ");
            }
            line.push_str(cell);
            if i + 1 < ncol {
                for _ in 0..widths[i].saturating_sub(cell.chars().count()) {
                    line.push(' ');
                }
            }
        }
        line.push('\n');
        line
    };
    let mut out = render_row(headers);
    for row in rows {
        out.push_str(&render_row(row));
    }
    out
}
