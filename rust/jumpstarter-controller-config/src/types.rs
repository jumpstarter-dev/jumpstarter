//! The controller configuration types, ported from
//! `controller/internal/config/types.go`.
//!
//! Serde attributes mirror the Go json tags exactly (sigs.k8s.io/yaml routes
//! YAML through encoding/json, so the json tags define the wire shape):
//!
//! - `omitempty` on a Go *string/bool/int* field ⇒ the zero value is omitted
//!   on serialize and tolerated as absent on deserialize
//!   (`#[serde(default, skip_serializing_if = ...)]`).
//! - `omitempty` on a Go *struct* field is ineffective (encoding/json never
//!   considers structs empty) ⇒ the field always serializes, as `{}` when
//!   zero. Such fields deliberately carry no `skip_serializing_if`.
//! - Fields without `omitempty` always serialize, including zero values
//!   (e.g. `prefix: ""`, `enabled: false`, `jwt: []`).
//!
//! No `deny_unknown_fields` on the models (workspace rule — K8s pruning
//! semantics), but that is NOT the Go reader contract for this document: Go
//! parses the ConfigMap `config` key with `yaml.UnmarshalStrict`
//! (`controller/internal/config/config.go:34` and `:99`), so an unknown key
//! anywhere in it is fatal at controller/router startup. That strictness is
//! reproduced per call site by the ConfigMap loader in
//! `jumpstarter-controller-runtime::configmap` (`from_str_strict`, via
//! `serde_ignored`); parsed standalone, these types tolerate unknown fields.

use serde::{Deserialize, Serialize};

use crate::jwt_authenticator::JwtAuthenticator;
use crate::serde_util::{is_false, is_zero_i32, null_default};

/// Config represents the main controller configuration structure.
/// This matches the YAML structure in the ConfigMap's "config" key.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Config {
    #[serde(default)]
    pub authentication: Authentication,
    #[serde(default)]
    pub provisioning: Provisioning,
    #[serde(default)]
    pub grpc: Grpc,
    // Go: `json:"leasePolicy,omitempty"` — omitempty is ineffective on struct
    // values, so this always serializes (`leasePolicy: {}` when zero).
    #[serde(default)]
    pub lease_policy: LeasePolicy,
}

/// LeasePolicy defines policy constraints for leases.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LeasePolicy {
    #[serde(default, skip_serializing_if = "is_zero_i32")]
    pub max_tags: i32,
}

/// Authentication defines the authentication configuration for the controller.
/// Supports multiple authentication methods: internal tokens, Kubernetes tokens, and JWT.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Authentication {
    #[serde(default)]
    pub internal: Internal,
    // Go: `json:"k8s,omitempty"` — omitempty is ineffective on struct values,
    // so this always serializes (`k8s: {}` when zero).
    #[serde(default)]
    pub k8s: K8s,
    // Go: `json:"jwt"` (no omitempty) — always serialized; the operator
    // ensures an explicit empty array rather than null. Deserialization
    // tolerates both a missing key and an explicit `jwt: null` (Go decodes
    // both to a nil slice).
    #[serde(default, deserialize_with = "null_default")]
    pub jwt: Vec<JwtAuthenticator>,
}

/// Internal defines the internal token authentication configuration.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Internal {
    /// Prefix to add to the subject claim of issued tokens (e.g., "internal:")
    #[serde(default)]
    pub prefix: String,

    /// TokenLifetime defines how long issued tokens are valid.
    /// Parsed as a Go duration string (e.g., "43800h", "30d").
    // NOTE (divergence in the Go doc comment, ported verbatim above): Go
    // time.ParseDuration does NOT accept a "d" unit; "30d" fails to parse in
    // both implementations.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub token_lifetime: String,
}

/// K8s defines the Kubernetes service account token authentication configuration.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct K8s {
    /// Enabled indicates whether Kubernetes authentication is enabled.
    #[serde(default, skip_serializing_if = "is_false")]
    pub enabled: bool,
}

/// Provisioning defines the provisioning configuration.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Provisioning {
    #[serde(default)]
    pub enabled: bool,
}

/// Grpc defines the gRPC server configuration.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Grpc {
    #[serde(default)]
    pub keepalive: Keepalive,
}

/// Keepalive defines the gRPC keepalive configuration.
/// All duration fields are parsed as Go duration strings (e.g., "1s", "10s", "180s").
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Keepalive {
    /// MinTime is the minimum time between keepalives that the server will accept.
    /// Default: "1s"
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub min_time: String,

    /// PermitWithoutStream allows keepalive pings even when there are no active streams.
    /// Default: true
    #[serde(default, skip_serializing_if = "is_false")]
    pub permit_without_stream: bool,

    /// Timeout is the duration to wait for a keepalive ping acknowledgment.
    /// Default: "180s"
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub timeout: String,

    /// IntervalTime is the duration between keepalive pings.
    /// Default: "10s"
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub interval_time: String,

    /// MaxConnectionIdle is the maximum duration a connection can be idle before being closed.
    /// Default: infinity (not set)
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub max_connection_idle: String,

    /// MaxConnectionAge is the maximum age of a connection before it is closed.
    /// Default: infinity (not set)
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub max_connection_age: String,

    /// MaxConnectionAgeGrace is the grace period for closing connections that exceed MaxConnectionAge.
    /// Default: infinity (not set)
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub max_connection_age_grace: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jwt_authenticator::{ClaimMappings, Issuer, PrefixedClaimOrExpression};

    /// Ported from Go `TestConfigRoundTrip` (types_test.go).
    #[test]
    fn config_round_trip() {
        let original = Config {
            authentication: Authentication {
                internal: Internal {
                    prefix: "internal:".into(),
                    token_lifetime: "43800h".into(),
                },
                k8s: K8s { enabled: true },
                jwt: vec![], // Empty array
            },
            provisioning: Provisioning { enabled: false },
            grpc: Grpc {
                keepalive: Keepalive {
                    min_time: "1s".into(),
                    permit_without_stream: true,
                    timeout: "180s".into(),
                    interval_time: "10s".into(),
                    ..Default::default()
                },
            },
            lease_policy: LeasePolicy::default(),
        };

        let yaml = serde_yaml_ng::to_string(&original).expect("marshal config");
        let parsed: Config = serde_yaml_ng::from_str(&yaml).expect("unmarshal config");

        assert_eq!(
            parsed.authentication.internal.prefix,
            original.authentication.internal.prefix
        );
        assert_eq!(
            parsed.grpc.keepalive.min_time,
            original.grpc.keepalive.min_time
        );
        assert_eq!(
            parsed.grpc.keepalive.permit_without_stream,
            original.grpc.keepalive.permit_without_stream
        );
        assert_eq!(parsed, original);
    }

    /// Go: zero-value struct fields with `omitempty` still serialize (as `{}`),
    /// and fields without `omitempty` serialize their zero values.
    #[test]
    fn zero_config_serializes_go_shape() {
        let yaml = serde_yaml_ng::to_string(&Config::default()).expect("marshal");
        // `internal.prefix` has no omitempty => always present.
        assert!(yaml.contains("prefix: ''"), "yaml was:\n{yaml}");
        // `provisioning.enabled` has no omitempty => always present.
        assert!(yaml.contains("enabled: false"), "yaml was:\n{yaml}");
        // struct-valued omitempty fields still serialize as empty mappings.
        assert!(yaml.contains("k8s: {}"), "yaml was:\n{yaml}");
        assert!(yaml.contains("leasePolicy: {}"), "yaml was:\n{yaml}");
        assert!(yaml.contains("keepalive: {}"), "yaml was:\n{yaml}");
        // `jwt` has no omitempty => present even when empty.
        assert!(yaml.contains("jwt: []"), "yaml was:\n{yaml}");
        // omitempty scalars are dropped when zero.
        assert!(!yaml.contains("maxTags"), "yaml was:\n{yaml}");
        assert!(!yaml.contains("tokenLifetime"), "yaml was:\n{yaml}");
    }

    /// Missing keys and explicit nulls both decode to zero values, like Go.
    #[test]
    fn tolerates_missing_and_null_sections() {
        let parsed: Config = serde_yaml_ng::from_str("provisioning:\n  enabled: true\n")
            .expect("partial config must parse");
        assert!(parsed.provisioning.enabled);
        assert_eq!(parsed.authentication, Authentication::default());
        assert_eq!(parsed.grpc, Grpc::default());

        let parsed: Config =
            serde_yaml_ng::from_str("authentication:\n  jwt: null\n").expect("null jwt");
        assert!(parsed.authentication.jwt.is_empty());
    }

    /// The models themselves stay lenient (no deny_unknown_fields — workspace
    /// rule). Go-parity strictness for the ConfigMap `config` document
    /// (yaml.UnmarshalStrict) is enforced by the loader in
    /// jumpstarter-controller-runtime::configmap, not here — see its
    /// `config_unknown_field_is_fatal_like_go` tests.
    #[test]
    fn tolerates_unknown_fields() {
        let yaml = "authentication:\n  internal:\n    prefix: 'internal:'\n  someFutureKnob: true\nfutureSection:\n  a: 1\n";
        let parsed: Config = serde_yaml_ng::from_str(yaml).expect("unknown fields tolerated");
        assert_eq!(parsed.authentication.internal.prefix, "internal:");
    }

    #[test]
    fn jwt_entry_round_trips() {
        let cfg = Config {
            authentication: Authentication {
                internal: Internal {
                    prefix: "internal:".into(),
                    ..Default::default()
                },
                jwt: vec![JwtAuthenticator {
                    issuer: Issuer {
                        url: "https://dex.dex.svc.cluster.local:5556".into(),
                        audiences: vec!["jumpstarter-cli".into()],
                        audience_match_policy: "MatchAny".into(),
                        ..Default::default()
                    },
                    claim_mappings: ClaimMappings {
                        username: PrefixedClaimOrExpression {
                            claim: "name".into(),
                            prefix: Some("dex:".into()),
                            ..Default::default()
                        },
                        ..Default::default()
                    },
                    ..Default::default()
                }],
                ..Default::default()
            },
            ..Default::default()
        };
        let yaml = serde_yaml_ng::to_string(&cfg).expect("marshal");
        let parsed: Config = serde_yaml_ng::from_str(&yaml).expect("unmarshal");
        assert_eq!(parsed, cfg);
        // Go marshals ClaimMappings.Groups/UID unconditionally.
        assert!(yaml.contains("groups: {}"), "yaml was:\n{yaml}");
        assert!(yaml.contains("uid: {}"), "yaml was:\n{yaml}");
    }
}
