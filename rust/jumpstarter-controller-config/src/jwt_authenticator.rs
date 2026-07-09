//! Hand-written mirror of the Kubernetes apiserver `JWTAuthenticator`
//! configuration types (`k8s.io/apiserver/pkg/apis/apiserver/v1beta1`,
//! v0.33.0 — the version the Go controller pins), as embedded in the
//! controller ConfigMap's `authentication.jwt` list.
//!
//! Only the JWTAuthenticator subtree is ported (the Go config path passes the
//! whole struct to the upstream OIDC authenticator); the rest of the
//! AuthenticationConfiguration API is out of scope. The types carry no
//! `deny_unknown_fields` (workspace rule), but do not mistake that for the
//! reader contract: Go parses the ConfigMap `config` document — jwt entries
//! included — with `yaml.UnmarshalStrict` (`config.go:34`/`:99`), so a field
//! from a newer upstream schema is FATAL there, and the Rust ConfigMap loader
//! (`jumpstarter-controller-runtime::configmap::from_str_strict`) matches
//! that. Unknown fields are tolerated only when these types are parsed
//! standalone by other, lenient readers.
//!
//! Serde mapping rules are the same as in [`crate::types`]: Go json tags are
//! the wire contract, `omitempty` on scalars omits zero values, `omitempty`
//! on struct values is ineffective (always serialized, `{}` when zero).

use serde::{Deserialize, Serialize};

use crate::serde_util::null_default;

/// Valid value for [`Issuer::audience_match_policy`]: the "aud" claim in the
/// presented JWT must match at least one of the entries in the "audiences"
/// field. (Go: `AudienceMatchPolicyMatchAny`.)
pub const AUDIENCE_MATCH_POLICY_MATCH_ANY: &str = "MatchAny";

/// JWTAuthenticator provides the configuration for a single JWT authenticator.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JwtAuthenticator {
    /// issuer contains the basic OIDC provider connection options.
    #[serde(default)]
    pub issuer: Issuer,

    /// claimValidationRules are rules that are applied to validate token
    /// claims to authenticate users.
    #[serde(
        default,
        deserialize_with = "null_default",
        skip_serializing_if = "Vec::is_empty"
    )]
    pub claim_validation_rules: Vec<ClaimValidationRule>,

    /// claimMappings points claims of a token to be treated as user attributes.
    #[serde(default)]
    pub claim_mappings: ClaimMappings,

    /// userValidationRules are rules that are applied to final user before
    /// completing authentication. These allow invariants to be applied to
    /// incoming identities such as preventing the use of the system: prefix
    /// that is commonly used by Kubernetes components. The validation rules
    /// are logically ANDed together and must all return true for the
    /// validation to pass.
    #[serde(
        default,
        deserialize_with = "null_default",
        skip_serializing_if = "Vec::is_empty"
    )]
    pub user_validation_rules: Vec<UserValidationRule>,
}

/// Issuer provides the configuration for an external provider's specific settings.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Issuer {
    /// url points to the issuer URL in a format https://url or https://url/path.
    /// This must match the "iss" claim in the presented JWT, and the issuer
    /// returned from discovery. Discovery information is fetched from
    /// "{url}/.well-known/openid-configuration" unless overridden by
    /// discoveryURL. Required to be unique across all JWT authenticators.
    #[serde(default)]
    pub url: String,

    /// discoveryURL, if specified, overrides the URL used to fetch discovery
    /// information instead of using "{url}/.well-known/openid-configuration".
    /// The exact value specified is used, so "/.well-known/openid-configuration"
    /// must be included in discoveryURL if needed. The "issuer" field in the
    /// fetched discovery information must match the "issuer.url" field.
    /// discoveryURL must be different from url. Required to be unique across
    /// all JWT authenticators.
    #[serde(
        default,
        rename = "discoveryURL",
        skip_serializing_if = "Option::is_none"
    )]
    pub discovery_url: Option<String>,

    /// certificateAuthority contains PEM-encoded certificate authority
    /// certificates used to validate the connection when fetching discovery
    /// information. If unset, the system verifier is used.
    ///
    /// NOTE: in the jumpstarter controller this field is dual-purpose — if
    /// the value is a path to an existing file the CA is loaded from that
    /// file, otherwise the value itself is treated as PEM content
    /// (see `newJWTAuthenticator` in controller/internal/config/oidc.go).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub certificate_authority: String,

    /// audiences is the set of acceptable audiences the JWT must be issued to.
    /// At least one of the entries must match the "aud" claim in presented
    /// JWTs. Required to be non-empty.
    #[serde(default, deserialize_with = "null_default")]
    pub audiences: Vec<String>,

    /// audienceMatchPolicy defines how the "audiences" field is used to match
    /// the "aud" claim in the presented JWT. Allowed values are "MatchAny"
    /// (when multiple audiences are specified) and empty/unset or "MatchAny"
    /// when a single audience is specified.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub audience_match_policy: String,
}

/// ClaimValidationRule provides the configuration for a single claim validation rule.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClaimValidationRule {
    /// claim is the name of a required claim. Only string claim keys are
    /// supported. Mutually exclusive with expression and message.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub claim: String,

    /// requiredValue is the value of a required claim. Only string claim
    /// values are supported. If claim is set and requiredValue is not set,
    /// the claim must be present with a value set to the empty string.
    /// Mutually exclusive with expression and message.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub required_value: String,

    /// expression represents the expression which will be evaluated by CEL.
    /// Must produce a boolean. CEL expressions have access to the contents of
    /// the token claims via the 'claims' variable. Must return true for the
    /// validation to pass. Mutually exclusive with claim and requiredValue.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub expression: String,

    /// message customizes the returned error message when expression returns
    /// false. message is a literal string. Mutually exclusive with claim and
    /// requiredValue.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub message: String,
}

/// ClaimMappings provides the configuration for claim mapping.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClaimMappings {
    /// username represents an option for the username attribute. The claim's
    /// value must be a singular string. If username.expression is set, the
    /// expression must produce a string value. For the authentication config,
    /// there is no defaulting for claim or prefix — both must be set
    /// explicitly when claim-based mapping is used.
    #[serde(default)]
    pub username: PrefixedClaimOrExpression,

    /// groups represents an option for the groups attribute. The claim's
    /// value must be a string or string array claim. If groups.claim is set,
    /// the prefix must be specified (and can be the empty string).
    // Go: `json:"groups,omitempty"` — omitempty is ineffective on struct
    // values, so this always serializes (`groups: {}` when zero).
    #[serde(default)]
    pub groups: PrefixedClaimOrExpression,

    /// uid represents an option for the uid attribute. Claim must be a
    /// singular string claim. If uid.expression is set, the expression must
    /// produce a string value.
    #[serde(default)]
    pub uid: ClaimOrExpression,

    /// extra represents an option for the extra attribute. expression must
    /// produce a string or string array value. If the value is empty, the
    /// extra mapping will not be present.
    #[serde(
        default,
        deserialize_with = "null_default",
        skip_serializing_if = "Vec::is_empty"
    )]
    pub extra: Vec<ExtraMapping>,
}

/// PrefixedClaimOrExpression provides the configuration for a single prefixed
/// claim or expression.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PrefixedClaimOrExpression {
    /// claim is the JWT claim to use. Mutually exclusive with expression.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub claim: String,

    /// prefix is prepended to claim's value to prevent clashes with existing
    /// names. prefix needs to be set if claim is set and can be the empty
    /// string. Mutually exclusive with expression.
    // Go: `*string` — present-and-empty is distinct from absent, hence Option.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prefix: Option<String>,

    /// expression represents the expression which will be evaluated by CEL.
    /// CEL expressions have access to the contents of the token claims via
    /// the 'claims' variable. Mutually exclusive with claim and prefix.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub expression: String,
}

/// ClaimOrExpression provides the configuration for a single claim or expression.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClaimOrExpression {
    /// claim is the JWT claim to use. Either claim or expression must be set.
    /// Mutually exclusive with expression.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub claim: String,

    /// expression represents the expression which will be evaluated by CEL.
    /// CEL expressions have access to the contents of the token claims via
    /// the 'claims' variable. Mutually exclusive with claim.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub expression: String,
}

/// ExtraMapping provides the configuration for a single extra mapping.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExtraMapping {
    /// key is a string to use as the extra attribute key. key must be a
    /// domain-prefix path (e.g. example.org/foo) and must be lowercase.
    /// Required to be unique.
    #[serde(default)]
    pub key: String,

    /// valueExpression is a CEL expression to extract extra attribute value.
    /// valueExpression must produce a string or string array value. "", [],
    /// and null values are treated as the extra mapping not being present.
    #[serde(default)]
    pub value_expression: String,
}

/// UserValidationRule provides the configuration for a single user info
/// validation rule.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UserValidationRule {
    /// expression represents the expression which will be evaluated by CEL.
    /// Must return true for the validation to pass. CEL expressions have
    /// access to the contents of UserInfo via the 'user' variable.
    #[serde(default)]
    pub expression: String,

    /// message customizes the returned error message when rule returns false.
    /// message is a literal string.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub message: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The dex authenticator from e2e/values.kind.yaml, exactly as it appears
    /// under `authentication.jwt[0]` in the ConfigMap.
    const DEX_YAML: &str = "\
issuer:
  url: https://dex.dex.svc.cluster.local:5556
  audiences:
  - jumpstarter-cli
  audienceMatchPolicy: MatchAny
  certificateAuthority: placeholder
claimMappings:
  username:
    claim: \"name\"
    prefix: \"dex:\"
";

    #[test]
    fn parses_dex_authenticator() {
        let parsed: JwtAuthenticator = serde_yaml_ng::from_str(DEX_YAML).expect("parse");
        assert_eq!(parsed.issuer.url, "https://dex.dex.svc.cluster.local:5556");
        assert_eq!(parsed.issuer.audiences, vec!["jumpstarter-cli"]);
        assert_eq!(
            parsed.issuer.audience_match_policy,
            AUDIENCE_MATCH_POLICY_MATCH_ANY
        );
        assert_eq!(parsed.issuer.certificate_authority, "placeholder");
        assert_eq!(parsed.issuer.discovery_url, None);
        assert_eq!(parsed.claim_mappings.username.claim, "name");
        assert_eq!(
            parsed.claim_mappings.username.prefix.as_deref(),
            Some("dex:")
        );
        assert!(parsed.claim_validation_rules.is_empty());
        assert!(parsed.user_validation_rules.is_empty());
    }

    /// prefix is a *string in Go: "" (set) and absent are distinct states.
    #[test]
    fn prefix_empty_vs_absent() {
        let with_empty: PrefixedClaimOrExpression =
            serde_yaml_ng::from_str("claim: groups\nprefix: \"\"\n").expect("parse");
        assert_eq!(with_empty.prefix.as_deref(), Some(""));
        let yaml = serde_yaml_ng::to_string(&with_empty).expect("marshal");
        assert!(yaml.contains("prefix: ''"), "yaml was:\n{yaml}");

        let absent: PrefixedClaimOrExpression =
            serde_yaml_ng::from_str("claim: groups\n").expect("parse");
        assert_eq!(absent.prefix, None);
        let yaml = serde_yaml_ng::to_string(&absent).expect("marshal");
        assert!(!yaml.contains("prefix"), "yaml was:\n{yaml}");
    }

    /// The types themselves stay lenient (no deny_unknown_fields — workspace
    /// rule): fields the port does not model parse when the type is used
    /// standalone. Inside the ConfigMap `config` document these same fields
    /// are fatal, matching Go's yaml.UnmarshalStrict — see
    /// `config_nested_unknown_field_is_fatal_like_go` in
    /// jumpstarter-controller-runtime::configmap.
    #[test]
    fn tolerates_unknown_fields() {
        let yaml = "\
issuer:
  url: https://issuer.example.com
  audiences: [aud]
  egressSelectorType: cluster
claimMappings:
  username:
    claim: sub
    prefix: ''
futureField: 42
";
        let parsed: JwtAuthenticator = serde_yaml_ng::from_str(yaml).expect("parse");
        assert_eq!(parsed.issuer.url, "https://issuer.example.com");
    }

    /// Round trip: what we serialize must match the Go wire shape (groups/uid
    /// always present; empty rule lists omitted) and re-parse identically.
    #[test]
    fn round_trip_matches_go_shape() {
        let authn = JwtAuthenticator {
            issuer: Issuer {
                url: "https://oidc.example.com".into(),
                audiences: vec!["foo".into(), "bar".into()],
                audience_match_policy: AUDIENCE_MATCH_POLICY_MATCH_ANY.into(),
                ..Default::default()
            },
            claim_validation_rules: vec![ClaimValidationRule {
                claim: "hd".into(),
                required_value: "example.com".into(),
                ..Default::default()
            }],
            claim_mappings: ClaimMappings {
                username: PrefixedClaimOrExpression {
                    claim: "sub".into(),
                    prefix: Some("oidc:".into()),
                    ..Default::default()
                },
                uid: ClaimOrExpression {
                    claim: "uid".into(),
                    ..Default::default()
                },
                extra: vec![ExtraMapping {
                    key: "example.com/tenant".into(),
                    value_expression: "claims.tenant".into(),
                }],
                ..Default::default()
            },
            user_validation_rules: vec![UserValidationRule {
                expression: "!user.username.startsWith('system:')".into(),
                message: "username cannot use reserved system: prefix".into(),
            }],
        };
        let yaml = serde_yaml_ng::to_string(&authn).expect("marshal");
        assert!(yaml.contains("groups: {}"), "yaml was:\n{yaml}");
        let parsed: JwtAuthenticator = serde_yaml_ng::from_str(&yaml).expect("unmarshal");
        assert_eq!(parsed, authn);
    }
}
