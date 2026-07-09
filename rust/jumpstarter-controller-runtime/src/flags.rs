//! Go-`flag`-compatible argv parser for the manager/router binaries.
//!
//! The operator deploys `/manager` with Go-idiom flags — including a
//! *single-dash long flag* (`-metrics-bind-address=:8080`, see
//! `controller/deploy/operator/internal/controller/jumpstarter/jumpstarter_controller.go`)
//! — so the Rust binaries must reproduce the Go `flag` package's parsing
//! semantics exactly rather than relying on a GNU-style parser:
//!
//! - `-flag` and `--flag` are equivalent (one or two minus signs).
//! - `-flag=value` and `-flag value` both work for non-boolean flags.
//! - Boolean flags take `-flag` (sets true) or `-flag=value`; a detached
//!   `-flag value` form is NOT consumed for booleans (the trailing token
//!   becomes a positional argument, exactly like Go).
//! - A bare `--` terminates flag parsing; a bare `-` (and any token not
//!   starting with `-`) stops parsing and remains in the positional args.
//! - Unknown flags are an error (`flag provided but not defined: -x`),
//!   except `-h`/`-help` which request usage output.
//!
//! The flag set mirrors `controller/cmd/main.go` (`metrics-bind-address`,
//! `health-probe-bind-address`, `leader-elect`, `metrics-secure`,
//! `enable-http2`) plus the `zap.Options.BindFlags` surface from
//! controller-runtime (`zap-devel`, `zap-log-level`, `zap-encoder`,
//! `zap-stacktrace-level`, `zap-time-encoding`). The zap flags are accepted
//! for deployment compatibility: `zap-log-level` is mapped onto the tracing
//! filter (see [`crate::logging`]); the rest are validated with the exact Go
//! value sets and then reported via [`Flags::warn_unsupported_zap_flags`].

use thiserror::Error;

/// Errors mirroring the Go `flag` package's parse-time failure messages
/// (`src/flag/flag.go` `parseOne`), plus the zap flag-value validation
/// errors from `sigs.k8s.io/controller-runtime/pkg/log/zap/flags.go`.
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum FlagError {
    /// Go: `bad flag syntax: %s` (e.g. `---flag` or `-=value`).
    #[error("bad flag syntax: {0}")]
    BadSyntax(String),
    /// Go: `flag provided but not defined: -%s`.
    #[error("flag provided but not defined: -{0}")]
    NotDefined(String),
    /// Go: `flag needs an argument: -%s`.
    #[error("flag needs an argument: -{0}")]
    NeedsArgument(String),
    /// Go: `invalid boolean value %q for -%s: %v`.
    #[error("invalid boolean value {value:?} for -{flag}")]
    InvalidBool { flag: String, value: String },
    /// Go: `invalid value %q for flag -%s: %v`; `reason` carries the zap
    /// flag validator's message (e.g. `invalid log level "warn"`).
    #[error("invalid value {value:?} for flag -{flag}: {reason}")]
    InvalidValue {
        flag: String,
        value: String,
        reason: String,
    },
    /// `-h` / `-help` was passed and no such flag is defined; the Go flag
    /// package prints usage and the binary exits.
    #[error("help requested")]
    Help,
}

/// Parsed `zap-log-level` value, mirroring `levelFlag.Set` in
/// controller-runtime's `pkg/log/zap/flags.go`: one of the named levels
/// (matched case-insensitively) or any integer > 0 which corresponds to
/// custom debug levels of increasing verbosity.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ZapLogLevel {
    Debug,
    Info,
    Error,
    Panic,
    /// Custom verbosity `n > 0` (zap level `-n`); higher is more verbose.
    /// Values beyond `u32::MAX` saturate (Go truncates through an `int8`
    /// cast; nothing sane is affected either way).
    Verbosity(u32),
}

impl ZapLogLevel {
    /// Parse a `zap-log-level` flag value exactly like Go's `levelFlag.Set`.
    fn parse(value: &str) -> Result<Self, String> {
        match value.to_ascii_lowercase().as_str() {
            "debug" => Ok(Self::Debug),
            "info" => Ok(Self::Info),
            "error" => Ok(Self::Error),
            "panic" => Ok(Self::Panic),
            _ => match value.parse::<i64>() {
                Ok(n) if n > 0 => Ok(Self::Verbosity(u32::try_from(n).unwrap_or(u32::MAX))),
                _ => Err(format!("invalid log level \"{value}\"")),
            },
        }
    }
}

/// The `zap-*` flag surface bound by `zap.Options.BindFlags`
/// (controller-runtime `pkg/log/zap/zap.go`). Values are validated at parse
/// time with the exact Go-accepted sets; `None` means the flag was not
/// provided on the command line.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ZapFlags {
    /// `zap-devel`: Development Mode defaults
    /// (encoder=consoleEncoder,logLevel=Debug,stackTraceLevel=Warn).
    /// Production Mode defaults
    /// (encoder=jsonEncoder,logLevel=Info,stackTraceLevel=Error).
    pub devel: Option<bool>,
    /// `zap-log-level`: Zap Level to configure the verbosity of logging.
    /// Can be one of 'debug', 'info', 'error', 'panic' or any integer value
    /// > 0 which corresponds to custom debug levels of increasing verbosity.
    pub log_level: Option<ZapLogLevel>,
    /// `zap-encoder`: Zap log encoding (one of 'json' or 'console').
    /// Stored as provided (Go matches case-insensitively).
    pub encoder: Option<String>,
    /// `zap-stacktrace-level`: Zap Level at and above which stacktraces are
    /// captured (one of 'info', 'error', 'panic').
    pub stacktrace_level: Option<String>,
    /// `zap-time-encoding`: Zap time encoding (one of 'epoch', 'millis',
    /// 'nanos', 'iso8601', 'rfc3339' or 'rfc3339nano'). Defaults to 'epoch'.
    pub time_encoding: Option<String>,
}

/// The parsed bootstrap flags of `controller/cmd/main.go`, with the same
/// defaults the Go binary declares.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Flags {
    /// The address the metric endpoint binds to. Use the port :8080. If not
    /// set, it will be 0 in order to disable the metrics server.
    pub metrics_bind_address: String,
    /// The address the probe endpoint binds to.
    pub health_probe_bind_address: String,
    /// Enable leader election for controller manager. Enabling this will
    /// ensure there is only one active controller manager.
    pub leader_elect: bool,
    /// If set the metrics endpoint is served securely.
    pub metrics_secure: bool,
    /// If set, HTTP/2 will be enabled for the metrics and webhook servers.
    pub enable_http2: bool,
    /// The `zap-*` logging flag surface.
    pub zap: ZapFlags,
    /// Positional arguments left after flag parsing (Go's `flag.Args()`).
    /// The Go binaries ignore these; retained for parse-fidelity tests.
    pub args: Vec<String>,
}

impl Default for Flags {
    fn default() -> Self {
        Self {
            metrics_bind_address: "0".to_string(),
            health_probe_bind_address: ":8081".to_string(),
            leader_elect: false,
            metrics_secure: false,
            enable_http2: false,
            zap: ZapFlags::default(),
            args: Vec::new(),
        }
    }
}

/// Boolean flag names of the **manager** binary (`controller/cmd/main.go`
/// declarations plus the zap surface); these do not consume a detached value
/// token (Go `flag` boolFlag special case).
const BOOL_FLAGS: &[&str] = &[
    "leader-elect",
    "metrics-secure",
    "enable-http2",
    "zap-devel",
];

/// String-valued flag names of the **manager** binary; these accept
/// `-flag=value` and `-flag value`.
const VALUE_FLAGS: &[&str] = &[
    "metrics-bind-address",
    "health-probe-bind-address",
    "zap-log-level",
    "zap-encoder",
    "zap-stacktrace-level",
    "zap-time-encoding",
];

/// The **router** binary defines ONLY the zap flags: `cmd/router/main.go`
/// calls `zap.Options.BindFlags(flag.CommandLine)` and nothing else, so e.g.
/// `-leader-elect` is `flag provided but not defined` there (exit 2).
const ROUTER_BOOL_FLAGS: &[&str] = &["zap-devel"];

/// String-valued zap flags — the router binary's value-flag surface.
const ROUTER_VALUE_FLAGS: &[&str] = &[
    "zap-log-level",
    "zap-encoder",
    "zap-stacktrace-level",
    "zap-time-encoding",
];

/// Parse a boolean flag value with Go's `strconv.ParseBool` accepted set:
/// 1, t, T, TRUE, true, True, 0, f, F, FALSE, false, False.
fn parse_go_bool(value: &str) -> Option<bool> {
    match value {
        "1" | "t" | "T" | "TRUE" | "true" | "True" => Some(true),
        "0" | "f" | "F" | "FALSE" | "false" | "False" => Some(false),
        _ => None,
    }
}

impl Flags {
    /// Parse the process arguments (equivalent to Go's
    /// `flag.Parse()` over `os.Args[1:]`) with the **manager** flag surface.
    pub fn parse_env() -> Result<Self, FlagError> {
        Self::parse(std::env::args().skip(1))
    }

    /// Parse the process arguments with the **router** flag surface (zap
    /// flags only — `cmd/router/main.go` binds nothing else).
    pub fn parse_router_env() -> Result<Self, FlagError> {
        Self::parse_router(std::env::args().skip(1))
    }

    /// Parse an argument vector (excluding the binary name) with Go `flag`
    /// package semantics and the **manager** flag surface. See the module
    /// docs for the exact grammar.
    pub fn parse<I, S>(args: I) -> Result<Self, FlagError>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Self::parse_with_surface(args, BOOL_FLAGS, VALUE_FLAGS)
    }

    /// Parse an argument vector with the **router** flag surface: only the
    /// zap flags are defined; the manager flags are
    /// `flag provided but not defined` errors, matching the Go router
    /// binary's `flag.CommandLine` contents.
    pub fn parse_router<I, S>(args: I) -> Result<Self, FlagError>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        Self::parse_with_surface(args, ROUTER_BOOL_FLAGS, ROUTER_VALUE_FLAGS)
    }

    fn parse_with_surface<I, S>(
        args: I,
        bool_flags: &[&str],
        value_flags: &[&str],
    ) -> Result<Self, FlagError>
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let mut flags = Self::default();
        let mut rest: std::collections::VecDeque<String> =
            args.into_iter().map(Into::into).collect();

        while let Some(token) = rest.front().cloned() {
            // Go parseOne: stop at any token shorter than 2 bytes (a bare
            // "-" included) or not starting with '-'; the token stays in
            // the positional args.
            if token.len() < 2 || !token.starts_with('-') {
                break;
            }
            let mut num_minuses = 1;
            if token.as_bytes()[1] == b'-' {
                num_minuses = 2;
                if token.len() == 2 {
                    // A bare "--" terminates the flags and is consumed.
                    rest.pop_front();
                    break;
                }
            }
            let name_and_value = &token[num_minuses..];
            if name_and_value.starts_with('-') || name_and_value.starts_with('=') {
                return Err(FlagError::BadSyntax(token));
            }
            rest.pop_front();

            let (name, mut value, mut has_value) = match name_and_value.find('=') {
                Some(idx) => (
                    name_and_value[..idx].to_string(),
                    name_and_value[idx + 1..].to_string(),
                    true,
                ),
                None => (name_and_value.to_string(), String::new(), false),
            };

            if bool_flags.contains(&name.as_str()) {
                // Boolean flags never consume the next token.
                let parsed = if has_value {
                    parse_go_bool(&value).ok_or_else(|| FlagError::InvalidBool {
                        flag: name.clone(),
                        value: value.clone(),
                    })?
                } else {
                    true
                };
                match name.as_str() {
                    "leader-elect" => flags.leader_elect = parsed,
                    "metrics-secure" => flags.metrics_secure = parsed,
                    "enable-http2" => flags.enable_http2 = parsed,
                    "zap-devel" => flags.zap.devel = Some(parsed),
                    _ => unreachable!("BOOL_FLAGS covers exactly these names"),
                }
                continue;
            }

            if !value_flags.contains(&name.as_str()) {
                // Go special-cases -h/-help when no such flag is defined.
                if name == "help" || name == "h" {
                    return Err(FlagError::Help);
                }
                return Err(FlagError::NotDefined(name));
            }

            // Non-boolean flag: the value may be the next argument.
            if !has_value {
                if let Some(next) = rest.pop_front() {
                    value = next;
                    has_value = true;
                }
            }
            if !has_value {
                return Err(FlagError::NeedsArgument(name));
            }

            let invalid = |reason: String| FlagError::InvalidValue {
                flag: name.clone(),
                value: value.clone(),
                reason,
            };
            match name.as_str() {
                "metrics-bind-address" => flags.metrics_bind_address = value,
                "health-probe-bind-address" => flags.health_probe_bind_address = value,
                "zap-log-level" => {
                    flags.zap.log_level = Some(ZapLogLevel::parse(&value).map_err(invalid)?);
                }
                "zap-encoder" => {
                    // Go encoderFlag.Set: 'json' or 'console', lowercased.
                    match value.to_ascii_lowercase().as_str() {
                        "json" | "console" => {}
                        _ => return Err(invalid(format!("invalid encoder value \"{value}\""))),
                    }
                    flags.zap.encoder = Some(value);
                }
                "zap-stacktrace-level" => {
                    // Go stackTraceFlag.Set: 'info', 'error' or 'panic'.
                    match value.to_ascii_lowercase().as_str() {
                        "info" | "error" | "panic" => {}
                        _ => return Err(invalid(format!("invalid stacktrace level \"{value}\""))),
                    }
                    flags.zap.stacktrace_level = Some(value);
                }
                "zap-time-encoding" => {
                    // Go timeEncodingFlag.Set (note: the code accepts
                    // 'nanos', although the doc string says 'nano').
                    match value.to_ascii_lowercase().as_str() {
                        "rfc3339nano" | "rfc3339" | "iso8601" | "millis" | "nanos" | "epoch" => {}
                        _ => {
                            return Err(invalid(format!("invalid time-encoding value \"{value}\"")))
                        }
                    }
                    flags.zap.time_encoding = Some(value);
                }
                _ => unreachable!("VALUE_FLAGS covers exactly these names"),
            }
        }

        flags.args = rest.into_iter().collect();
        Ok(flags)
    }

    /// Emit structured warnings for any provided `zap-*` flags. The Rust
    /// binaries log through `tracing`, not zap: `zap-log-level` is honored
    /// by mapping it onto the tracing filter
    /// ([`crate::logging::init_tracing`]); the remaining zap flags are
    /// accepted for operator/deployment compatibility but have no effect.
    ///
    /// Call this after [`crate::logging::init_tracing`] so the warnings are
    /// actually visible.
    pub fn warn_unsupported_zap_flags(&self) {
        if let Some(level) = &self.zap.log_level {
            tracing::debug!(
                zap_log_level = ?level,
                "zap-log-level mapped onto the tracing filter"
            );
        }
        if let Some(devel) = self.zap.devel {
            tracing::warn!(
                value = devel,
                "zap-devel is accepted for compatibility but ignored: logging is tracing-based, not zap"
            );
        }
        if let Some(encoder) = &self.zap.encoder {
            tracing::warn!(
                value = %encoder,
                "zap-encoder is accepted for compatibility but ignored: logging is tracing-based, not zap"
            );
        }
        if let Some(level) = &self.zap.stacktrace_level {
            tracing::warn!(
                value = %level,
                "zap-stacktrace-level is accepted for compatibility but ignored: logging is tracing-based, not zap"
            );
        }
        if let Some(encoding) = &self.zap.time_encoding {
            tracing::warn!(
                value = %encoding,
                "zap-time-encoding is accepted for compatibility but ignored: logging is tracing-based, not zap"
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The exact argument vector the operator passes to `/manager`
    /// (`controller/deploy/operator/internal/controller/jumpstarter/`
    /// `jumpstarter_controller.go`), including the single-dash long flag.
    #[test]
    fn operator_manager_arg_vector() {
        let flags = Flags::parse([
            "--leader-elect",
            "--health-probe-bind-address=:8081",
            "-metrics-bind-address=:8080",
        ])
        .expect("operator arg vector must parse");
        assert!(flags.leader_elect);
        assert_eq!(flags.health_probe_bind_address, ":8081");
        assert_eq!(flags.metrics_bind_address, ":8080");
        assert!(!flags.metrics_secure);
        assert!(!flags.enable_http2);
        assert_eq!(flags.zap, ZapFlags::default());
        assert!(flags.args.is_empty());
    }

    #[test]
    fn defaults_match_go_flag_declarations() {
        let flags = Flags::parse(Vec::<String>::new()).unwrap();
        assert_eq!(flags.metrics_bind_address, "0");
        assert_eq!(flags.health_probe_bind_address, ":8081");
        assert!(!flags.leader_elect);
        assert!(!flags.metrics_secure);
        assert!(!flags.enable_http2);
    }

    #[test]
    fn single_and_double_dash_are_equivalent() {
        let single = Flags::parse(["-leader-elect", "-metrics-bind-address=:9"]).unwrap();
        let double = Flags::parse(["--leader-elect", "--metrics-bind-address=:9"]).unwrap();
        assert_eq!(single, double);
        assert!(single.leader_elect);
        assert_eq!(single.metrics_bind_address, ":9");
    }

    #[test]
    fn detached_value_form_for_string_flags() {
        let flags = Flags::parse(["-metrics-bind-address", ":8080"]).unwrap();
        assert_eq!(flags.metrics_bind_address, ":8080");
        let flags = Flags::parse(["--health-probe-bind-address", "localhost:9440"]).unwrap();
        assert_eq!(flags.health_probe_bind_address, "localhost:9440");
    }

    #[test]
    fn bool_flag_with_explicit_value() {
        for (value, expected) in [
            ("true", true),
            ("True", true),
            ("TRUE", true),
            ("t", true),
            ("T", true),
            ("1", true),
            ("false", false),
            ("False", false),
            ("FALSE", false),
            ("f", false),
            ("F", false),
            ("0", false),
        ] {
            let flags = Flags::parse([format!("-leader-elect={value}")]).unwrap();
            assert_eq!(flags.leader_elect, expected, "value {value:?}");
        }
    }

    #[test]
    fn bool_flag_does_not_consume_next_token() {
        // Go: a boolean flag never takes a detached value; "false" becomes
        // a positional argument and terminates flag parsing.
        let flags = Flags::parse(["-leader-elect", "false"]).unwrap();
        assert!(flags.leader_elect);
        assert_eq!(flags.args, vec!["false"]);
    }

    #[test]
    fn invalid_bool_value_is_an_error() {
        let err = Flags::parse(["-leader-elect=yes"]).unwrap_err();
        assert_eq!(
            err,
            FlagError::InvalidBool {
                flag: "leader-elect".into(),
                value: "yes".into()
            }
        );
    }

    #[test]
    fn double_dash_terminates_parsing() {
        let flags = Flags::parse(["--leader-elect", "--", "-metrics-bind-address=:9"]).unwrap();
        assert!(flags.leader_elect);
        // The "--" itself is consumed; everything after is positional.
        assert_eq!(flags.args, vec!["-metrics-bind-address=:9"]);
        assert_eq!(flags.metrics_bind_address, "0");
    }

    #[test]
    fn bare_dash_stops_parsing_and_is_kept() {
        let flags = Flags::parse(["-", "-leader-elect"]).unwrap();
        assert!(!flags.leader_elect);
        assert_eq!(flags.args, vec!["-", "-leader-elect"]);
    }

    #[test]
    fn non_flag_token_stops_parsing() {
        let flags = Flags::parse(["positional", "-leader-elect"]).unwrap();
        assert!(!flags.leader_elect);
        assert_eq!(flags.args, vec!["positional", "-leader-elect"]);
    }

    #[test]
    fn unknown_flag_is_an_error() {
        let err = Flags::parse(["-bogus"]).unwrap_err();
        assert_eq!(err, FlagError::NotDefined("bogus".into()));
        assert_eq!(
            err.to_string(),
            "flag provided but not defined: -bogus",
            "error text is the Go flag package's message"
        );
    }

    #[test]
    fn missing_value_is_an_error() {
        let err = Flags::parse(["-metrics-bind-address"]).unwrap_err();
        assert_eq!(err, FlagError::NeedsArgument("metrics-bind-address".into()));
        assert_eq!(
            err.to_string(),
            "flag needs an argument: -metrics-bind-address"
        );
    }

    #[test]
    fn bad_flag_syntax() {
        assert_eq!(
            Flags::parse(["---leader-elect"]).unwrap_err(),
            FlagError::BadSyntax("---leader-elect".into())
        );
        assert_eq!(
            Flags::parse(["-=value"]).unwrap_err(),
            FlagError::BadSyntax("-=value".into())
        );
    }

    #[test]
    fn help_flags() {
        assert_eq!(Flags::parse(["-h"]).unwrap_err(), FlagError::Help);
        assert_eq!(Flags::parse(["--help"]).unwrap_err(), FlagError::Help);
    }

    #[test]
    fn zap_flags_are_accepted() {
        let flags = Flags::parse([
            "-zap-devel",
            "-zap-log-level=debug",
            "-zap-encoder",
            "console",
            "--zap-stacktrace-level=panic",
            "--zap-time-encoding",
            "iso8601",
        ])
        .unwrap();
        assert_eq!(flags.zap.devel, Some(true));
        assert_eq!(flags.zap.log_level, Some(ZapLogLevel::Debug));
        assert_eq!(flags.zap.encoder.as_deref(), Some("console"));
        assert_eq!(flags.zap.stacktrace_level.as_deref(), Some("panic"));
        assert_eq!(flags.zap.time_encoding.as_deref(), Some("iso8601"));
    }

    #[test]
    fn zap_devel_explicit_false() {
        let flags = Flags::parse(["--zap-devel=false"]).unwrap();
        assert_eq!(flags.zap.devel, Some(false));
    }

    #[test]
    fn zap_log_level_named_values_are_case_insensitive() {
        for (value, expected) in [
            ("debug", ZapLogLevel::Debug),
            ("DEBUG", ZapLogLevel::Debug),
            ("Info", ZapLogLevel::Info),
            ("error", ZapLogLevel::Error),
            ("panic", ZapLogLevel::Panic),
        ] {
            let flags = Flags::parse([format!("-zap-log-level={value}")]).unwrap();
            assert_eq!(flags.zap.log_level, Some(expected), "value {value:?}");
        }
    }

    #[test]
    fn zap_log_level_numeric_verbosity() {
        let flags = Flags::parse(["-zap-log-level=5"]).unwrap();
        assert_eq!(flags.zap.log_level, Some(ZapLogLevel::Verbosity(5)));
        // Go's strconv.Atoi accepts a leading '+'.
        let flags = Flags::parse(["-zap-log-level=+3"]).unwrap();
        assert_eq!(flags.zap.log_level, Some(ZapLogLevel::Verbosity(3)));
    }

    #[test]
    fn zap_log_level_invalid_values() {
        // 'warn' is not in Go's levelStrings map; 0 and negatives fail the
        // `logLevel > 0` check.
        for value in ["warn", "0", "-1"] {
            let err = Flags::parse([format!("-zap-log-level={value}")]).unwrap_err();
            assert_eq!(
                err,
                FlagError::InvalidValue {
                    flag: "zap-log-level".into(),
                    value: value.into(),
                    reason: format!("invalid log level \"{value}\""),
                },
                "value {value:?}"
            );
        }
    }

    #[test]
    fn zap_value_flag_validation() {
        let err = Flags::parse(["-zap-encoder=xml"]).unwrap_err();
        assert_eq!(
            err.to_string(),
            "invalid value \"xml\" for flag -zap-encoder: invalid encoder value \"xml\""
        );
        let err = Flags::parse(["-zap-stacktrace-level=debug"]).unwrap_err();
        assert_eq!(
            err.to_string(),
            "invalid value \"debug\" for flag -zap-stacktrace-level: invalid stacktrace level \"debug\""
        );
        let err = Flags::parse(["-zap-time-encoding=nano"]).unwrap_err();
        // Go's switch accepts "nanos", not "nano" (the doc string is wrong).
        assert_eq!(
            err.to_string(),
            "invalid value \"nano\" for flag -zap-time-encoding: invalid time-encoding value \"nano\""
        );
    }

    /// The router binary defines only the zap flags (`cmd/router/main.go`
    /// binds `zap.Options.BindFlags` and nothing else): the manager flags
    /// must be "not defined" errors on the router surface.
    #[test]
    fn router_surface_rejects_manager_flags() {
        for flag in [
            "-leader-elect",
            "--leader-elect",
            "-metrics-bind-address=:8080",
            "--health-probe-bind-address=:8081",
            "-metrics-secure",
            "-enable-http2",
        ] {
            let err = Flags::parse_router([flag]).unwrap_err();
            let name = flag
                .trim_start_matches('-')
                .split('=')
                .next()
                .unwrap()
                .to_string();
            assert_eq!(err, FlagError::NotDefined(name), "flag {flag}");
        }
    }

    #[test]
    fn router_surface_accepts_zap_flags_and_help() {
        let flags = Flags::parse_router([
            "-zap-devel",
            "-zap-log-level=debug",
            "--zap-encoder=json",
            "--zap-stacktrace-level",
            "error",
            "-zap-time-encoding=epoch",
        ])
        .unwrap();
        assert_eq!(flags.zap.devel, Some(true));
        assert_eq!(flags.zap.log_level, Some(ZapLogLevel::Debug));
        assert_eq!(flags.zap.encoder.as_deref(), Some("json"));
        assert_eq!(flags.zap.stacktrace_level.as_deref(), Some("error"));
        assert_eq!(flags.zap.time_encoding.as_deref(), Some("epoch"));

        assert_eq!(Flags::parse_router(["-h"]).unwrap_err(), FlagError::Help);
        assert_eq!(
            Flags::parse_router(["--help"]).unwrap_err(),
            FlagError::Help
        );
        // Unknown flags keep the Go flag error text.
        assert_eq!(
            Flags::parse_router(["-bogus"]).unwrap_err().to_string(),
            "flag provided but not defined: -bogus"
        );
    }

    #[test]
    fn later_flags_override_earlier_ones() {
        let flags = Flags::parse(["-metrics-bind-address=:1", "-metrics-bind-address=:2"]).unwrap();
        assert_eq!(flags.metrics_bind_address, ":2");
    }

    #[test]
    fn empty_value_via_equals_is_kept() {
        let flags = Flags::parse(["-metrics-bind-address="]).unwrap();
        assert_eq!(flags.metrics_bind_address, "");
    }
}
