//! Port of `config.LoadGrpcConfiguration`
//! (`controller/internal/config/grpc.go:10-67`) — the gRPC server keepalive
//! options the router binary loads from the `jumpstarter-controller`
//! ConfigMap's `config` key (`controller/cmd/router/main.go:68-79`) and
//! appends to its server options (`router_service.go:158`).
//!
//! Parsing reproduces Go exactly, including tolerance: every duration field
//! goes through `config.ParseDuration` (`types.go:106-111`), where the empty
//! string — i.e. an absent YAML key — parses to `0` rather than erroring;
//! only a present-but-malformed value is fatal, with the Go error text
//! (`failed to parse keepalive <field>: time: invalid duration "..."`).
//! `minTime == 0` defaults to 1s (`grpc.go:18-20`), and
//! `keepalive.ServerParameters` is produced only when at least one of its
//! five fields is non-zero (`grpc.go:63-65`).
//!
//! ## Application to tonic (documented divergences, all wire-tolerant)
//!
//! - grpc-go's `KeepaliveEnforcementPolicy` (`MinTime`,
//!   `PermitWithoutStream`) polices *client* ping cadence; tonic/h2 has no
//!   equivalent and never GOAWAYs pinging clients. The Rust router is
//!   therefore strictly **more permissive** than any configured policy —
//!   the Python clients' 20s cadence (spec 06 §2.4) is always tolerated.
//!   The values are still parsed (validation parity) and logged.
//! - `Timeout`/`Time` map to tonic's `http2_keepalive_timeout`/`interval`.
//!   grpc-go applies server keepalive defaults even when no
//!   `ServerParameters` option is given (Time 2h, Timeout 20s), so those
//!   defaults are applied here whenever a field is zero/absent.
//! - `MaxConnectionAge` maps to tonic's `max_connection_age` (grpc-go adds
//!   ±10% jitter; tonic does not — benign).
//! - `MaxConnectionIdle` and `MaxConnectionAgeGrace` have no tonic
//!   equivalent; non-zero values are parsed and logged as unsupported.

use jumpstarter_controller_config::duration::{
    parse_config_duration, ParseDurationError, HOUR, SECOND,
};
use jumpstarter_controller_config::types::Grpc;
use thiserror::Error;

/// grpc-go `defaultServerKeepaliveTime` (2 hours).
const DEFAULT_SERVER_KEEPALIVE_TIME: i64 = 2 * HOUR;
/// grpc-go `defaultServerKeepaliveTimeout` (20 seconds).
const DEFAULT_SERVER_KEEPALIVE_TIMEOUT: i64 = 20 * SECOND;

/// Fatal configuration error; the router binary exits with
/// "failed to load router configuration" (`cmd/router/main.go:71-75`).
#[derive(Debug, Clone, PartialEq, Eq, Error)]
pub enum GrpcConfigError {
    /// Go: `fmt.Errorf("failed to parse keepalive %s: %w", field, err)`.
    #[error("failed to parse keepalive {field}: {source}")]
    ParseDuration {
        field: &'static str,
        #[source]
        source: ParseDurationError,
    },
}

/// `keepalive.EnforcementPolicy` (`grpc.go:21-24`). Durations are Go
/// `time.Duration` nanoseconds.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnforcementPolicy {
    /// Minimum time between client keepalive pings; defaulted to 1s when the
    /// config value is absent/zero (`grpc.go:18-20`).
    pub min_time: i64,
    pub permit_without_stream: bool,
}

/// `keepalive.ServerParameters` (`grpc.go:54-61`). Zero means "not set"
/// (grpc-go then applies its own defaults / infinity).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ServerParameters {
    pub timeout: i64,
    /// grpc-go `Time` — the config key is `intervalTime`.
    pub time: i64,
    pub max_connection_idle: i64,
    pub max_connection_age: i64,
    pub max_connection_age_grace: i64,
}

impl ServerParameters {
    fn is_zero(&self) -> bool {
        *self == Self::default()
    }
}

/// The parsed equivalent of the `[]grpc.ServerOption` slice Go returns: the
/// enforcement policy is always present; server parameters only when any
/// field was set (`grpc.go:63-65`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GrpcServerOptions {
    pub enforcement_policy: EnforcementPolicy,
    pub server_parameters: Option<ServerParameters>,
}

/// Port of `LoadGrpcConfiguration` (`grpc.go:10-67`): parse order, defaults,
/// zero-struct check and error strings all preserved.
pub fn load_grpc_configuration(config: &Grpc) -> Result<GrpcServerOptions, GrpcConfigError> {
    let ka = &config.keepalive;

    let parse = |field: &'static str, value: &str| {
        parse_config_duration(value)
            .map_err(|source| GrpcConfigError::ParseDuration { field, source })
    };

    let mut min_time = parse("minTime", &ka.min_time)?;
    if min_time == 0 {
        // Go: minTime = 1e9 (1 second).
        min_time = SECOND;
    }

    let enforcement_policy = EnforcementPolicy {
        min_time,
        permit_without_stream: ka.permit_without_stream,
    };

    let server_parameters = ServerParameters {
        timeout: parse("timeout", &ka.timeout)?,
        time: parse("intervalTime", &ka.interval_time)?,
        max_connection_idle: parse("maxConnectionIdle", &ka.max_connection_idle)?,
        max_connection_age: parse("maxConnectionAge", &ka.max_connection_age)?,
        max_connection_age_grace: parse("maxConnectionAgeGrace", &ka.max_connection_age_grace)?,
    };

    Ok(GrpcServerOptions {
        enforcement_policy,
        // Go: `if params != (keepalive.ServerParameters{})`.
        server_parameters: (!server_parameters.is_zero()).then_some(server_parameters),
    })
}

/// Converts non-negative Go nanoseconds to a `std::time::Duration`; negative
/// values (expressible in config as e.g. `"-5s"`) are treated as unset with
/// a warning — grpc-go would arm immediately-firing timers, which no sane
/// deployment configures.
fn positive_duration(field: &'static str, nanos: i64) -> Option<std::time::Duration> {
    match nanos {
        0 => None,
        n if n < 0 => {
            tracing::warn!(field, nanos, "negative keepalive duration ignored");
            None
        }
        n => Some(std::time::Duration::from_nanos(n as u64)),
    }
}

/// Applies the loaded options to a tonic server builder, the counterpart of
/// Go appending the option slice at `router_service.go:158`. See the module
/// docs for the exact mapping and divergences.
pub fn apply<L>(
    server: tonic::transport::Server<L>,
    options: &GrpcServerOptions,
) -> tonic::transport::Server<L> {
    // Enforcement policy: no tonic equivalent (strictly more permissive).
    tracing::debug!(
        min_time_ns = options.enforcement_policy.min_time,
        permit_without_stream = options.enforcement_policy.permit_without_stream,
        "keepalive enforcement policy has no tonic equivalent; all client ping cadences tolerated"
    );

    let params = options.server_parameters.clone().unwrap_or_default();

    // grpc-go applies these defaults whether or not KeepaliveParams was set.
    let interval = positive_duration("intervalTime", params.time).unwrap_or(
        std::time::Duration::from_nanos(DEFAULT_SERVER_KEEPALIVE_TIME as u64),
    );
    let timeout = positive_duration("timeout", params.timeout).unwrap_or(
        std::time::Duration::from_nanos(DEFAULT_SERVER_KEEPALIVE_TIMEOUT as u64),
    );

    let mut server = server
        .http2_keepalive_interval(Some(interval))
        .http2_keepalive_timeout(Some(timeout));

    if let Some(age) = positive_duration("maxConnectionAge", params.max_connection_age) {
        server = server.max_connection_age(age);
    }
    if params.max_connection_idle != 0 {
        tracing::warn!(
            max_connection_idle_ns = params.max_connection_idle,
            "keepalive maxConnectionIdle is not supported by tonic and is ignored"
        );
    }
    if params.max_connection_age_grace != 0 {
        tracing::warn!(
            max_connection_age_grace_ns = params.max_connection_age_grace,
            "keepalive maxConnectionAgeGrace is not supported by tonic and is ignored"
        );
    }

    server
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_controller_config::types::Keepalive;

    fn grpc(keepalive: Keepalive) -> Grpc {
        Grpc { keepalive }
    }

    /// Go `TestLoadGrpcConfigurationMinimalConfigReturnsSlice`
    /// (grpc_test.go): policy always produced.
    #[test]
    fn minimal_config_returns_policy_only() {
        let options = load_grpc_configuration(&grpc(Keepalive {
            min_time: "5s".into(),
            permit_without_stream: true,
            ..Default::default()
        }))
        .unwrap();

        assert_eq!(
            options.enforcement_policy,
            EnforcementPolicy {
                min_time: 5 * SECOND,
                permit_without_stream: true,
            }
        );
        // No server-parameter field set => no params (Go returns 1 option).
        assert_eq!(options.server_parameters, None);
    }

    /// Go `TestLoadGrpcConfigurationWithTimeoutAndIntervalTime`: any set
    /// parameter field yields ServerParameters (Go returns 2 options).
    #[test]
    fn timeout_and_interval_produce_server_parameters() {
        let options = load_grpc_configuration(&grpc(Keepalive {
            min_time: "1s".into(),
            permit_without_stream: true,
            timeout: "30s".into(),
            interval_time: "5s".into(),
            ..Default::default()
        }))
        .unwrap();

        assert_eq!(
            options.server_parameters,
            Some(ServerParameters {
                timeout: 30 * SECOND,
                time: 5 * SECOND,
                ..Default::default()
            })
        );
    }

    /// Absent fields tolerate: `config.ParseDuration("") == 0` and minTime
    /// defaults to 1s (grpc.go:18-20) — an empty `grpc:`/`keepalive:`
    /// section loads successfully.
    #[test]
    fn empty_config_defaults_min_time_to_one_second() {
        let options = load_grpc_configuration(&Grpc::default()).unwrap();
        assert_eq!(options.enforcement_policy.min_time, SECOND);
        assert!(!options.enforcement_policy.permit_without_stream);
        assert_eq!(options.server_parameters, None);
    }

    /// Go `TestLoadGrpcConfigurationInvalidTimeoutReturnsError`, with the
    /// exact wrapped error text.
    #[test]
    fn invalid_timeout_is_an_error_mentioning_timeout() {
        let err = load_grpc_configuration(&grpc(Keepalive {
            min_time: "1s".into(),
            timeout: "abc".into(),
            ..Default::default()
        }))
        .unwrap_err();
        assert_eq!(
            err.to_string(),
            "failed to parse keepalive timeout: time: invalid duration \"abc\""
        );
    }

    /// Go `TestLoadGrpcConfigurationInvalidIntervalTimeReturnsError`.
    #[test]
    fn invalid_interval_time_is_an_error_mentioning_interval_time() {
        let err = load_grpc_configuration(&grpc(Keepalive {
            min_time: "1s".into(),
            interval_time: "xyz".into(),
            ..Default::default()
        }))
        .unwrap_err();
        assert!(
            err.to_string().contains("intervalTime"),
            "expected error mentioning 'intervalTime', got: {err}"
        );
    }

    #[test]
    fn invalid_min_time_and_max_connection_fields_error_with_go_field_names() {
        for (field, keepalive) in [
            (
                "minTime",
                Keepalive {
                    min_time: "nope".into(),
                    ..Default::default()
                },
            ),
            (
                "maxConnectionIdle",
                Keepalive {
                    max_connection_idle: "nope".into(),
                    ..Default::default()
                },
            ),
            (
                "maxConnectionAge",
                Keepalive {
                    max_connection_age: "nope".into(),
                    ..Default::default()
                },
            ),
            (
                "maxConnectionAgeGrace",
                Keepalive {
                    max_connection_age_grace: "nope".into(),
                    ..Default::default()
                },
            ),
        ] {
            let err = load_grpc_configuration(&grpc(keepalive)).unwrap_err();
            assert_eq!(
                err.to_string(),
                format!("failed to parse keepalive {field}: time: invalid duration \"nope\"")
            );
        }
    }

    /// `apply` must accept both the always-present-policy and the
    /// with-parameters shapes without panicking (builder smoke test).
    #[test]
    fn apply_smoke() {
        let options = load_grpc_configuration(&grpc(Keepalive {
            min_time: "1s".into(),
            permit_without_stream: true,
            timeout: "180s".into(),
            interval_time: "10s".into(),
            max_connection_idle: "1h".into(),
            max_connection_age: "2h".into(),
            max_connection_age_grace: "5m".into(),
        }))
        .unwrap();
        let _ = apply(tonic::transport::Server::builder(), &options);

        let defaults = load_grpc_configuration(&Grpc::default()).unwrap();
        let _ = apply(tonic::transport::Server::builder(), &defaults);
    }
}
