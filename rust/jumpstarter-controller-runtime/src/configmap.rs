//! Loading of the `jumpstarter-controller` ConfigMap, mirroring
//! `controller/internal/config/config.go` (`LoadConfiguration` and
//! `LoadRouterConfiguration`).
//!
//! Both the manager and the router binaries read the same ConfigMap
//! (`jumpstarter-controller` in `$NAMESPACE`, see `controller/cmd/main.go:228`
//! and `controller/cmd/router/main.go:70`) but consume different keys:
//!
//! - The **controller** (`LoadConfiguration`) requires the `router` key
//!   (endpoint map), then either the legacy `authentication` key (pre-0.7
//!   compatibility, short-circuits everything else) or the `config` key.
//! - The **router** (`LoadRouterConfiguration`) requires only the `config`
//!   key, of which it consumes just the `grpc` section.
//!
//! Every failure on this path is fatal in Go: the caller logs the error and
//! `os.Exit(1)`s (`controller/cmd/main.go:236-239`, `router/main.go:71-75`).
//! Nothing is defaulted on error; defaults exist only *inside* the parsed
//! structures (e.g. keepalive durations) and on the legacy path (see
//! [`ControllerConfiguration::Legacy`]).
//!
//! The parse entry points are generic over the deserialization targets so the
//! key-handling/error taxonomy stays decoupled from the concrete models; the
//! binaries instantiate them with `jumpstarter_controller_config` types
//! (`Config` for the `config` key, `Router` for the `router` key).

use std::collections::BTreeMap;

use k8s_openapi::api::core::v1::ConfigMap;
use kube::{Api, Client};
use serde::de::DeserializeOwned;
use thiserror::Error;

// Single source of truth for the ConfigMap name and data keys is the shared
// config-model crate (the operator will write them through the same
// constants). Re-exported under this module's Go-flavored names:
// - `CONFIGMAP_NAME`: `"jumpstarter-controller"` (`controller/cmd/main.go:228`,
//   `controller/cmd/router/main.go:70`)
// - `CONFIG_KEY`/`ROUTER_KEY`: the two configuration documents
// - `AUTHENTICATION_KEY`: legacy pre-0.7 key whose mere *presence* makes Go
//   take the backwards-compatibility path and skip `config` entirely
//   (`config.go:70-91`, "TODO: remove in 0.7.0")
pub use jumpstarter_controller_config::{
    CONFIG_MAP_KEY_CONFIG as CONFIG_KEY,
    CONFIG_MAP_KEY_LEGACY_AUTHENTICATION as AUTHENTICATION_KEY,
    CONFIG_MAP_KEY_ROUTER as ROUTER_KEY, CONFIG_MAP_NAME as CONFIGMAP_NAME,
};

/// Error taxonomy for ConfigMap loading. All variants are fatal at startup,
/// matching Go where `LoadConfiguration` errors make `main` exit.
#[derive(Debug, Error)]
pub enum ConfigMapError {
    /// The ConfigMap itself could not be fetched. Go returns the `client.Get`
    /// error verbatim (`config.go:24-26`, `config.go:56-58`); a missing
    /// ConfigMap surfaces here as a NotFound apierror.
    #[error("unable to get configmap {namespace}/{name}: {source}")]
    Fetch {
        namespace: String,
        name: String,
        /// Boxed to keep the `Result` payload small (clippy::result_large_err).
        #[source]
        source: Box<kube::Error>,
    },
    /// A required key is absent from `.data`. Message text mirrors Go's
    /// `fmt.Errorf("LoadConfiguration: missing router section")` /
    /// `"LoadConfiguration: missing config section"` /
    /// `"LoadRouterConfiguration: missing config section"` exactly.
    #[error("{context}: missing {key} section")]
    MissingKey {
        context: &'static str,
        key: &'static str,
    },
    /// The YAML under a key failed to deserialize. Go returns the
    /// `yaml.Unmarshal`/`yaml.UnmarshalStrict` error verbatim.
    #[error("unable to parse {key} section: {source}")]
    Parse {
        key: &'static str,
        #[source]
        source: serde_yaml_ng::Error,
    },
    /// The `config` document contains a key the target model does not know.
    /// Go parses the `config` key with `yaml.UnmarshalStrict` (`config.go:34`
    /// and `config.go:99`), so an unknown field anywhere in the document —
    /// including nested ones, e.g. inside `authentication.jwt[]` entries — is
    /// fatal at startup (Go surfaces encoding/json's
    /// `json: unknown field "<name>"`; we report the full dotted path). The
    /// `router` key uses lenient `yaml.Unmarshal` (`config.go:66`) and never
    /// produces this error. The models themselves stay lenient (the workspace
    /// forbids `deny_unknown_fields` — K8s pruning semantics); strictness is
    /// enforced here at the loader, per call site, via [`from_str_strict`].
    #[error("unable to parse {key} section: unknown field \"{path}\"")]
    UnknownField { key: &'static str, path: String },
}

impl ConfigMapError {
    /// True when the underlying failure is the ConfigMap not existing
    /// (HTTP 404 from the apiserver), as opposed to e.g. RBAC or transport
    /// errors. Go does not distinguish these — both are fatal — but callers
    /// can use this for friendlier startup diagnostics.
    pub fn is_not_found(&self) -> bool {
        match self {
            ConfigMapError::Fetch { source, .. } => {
                matches!(&**source, kube::Error::Api(status) if status.code == 404)
            }
            _ => false,
        }
    }
}

/// Result of `LoadConfiguration`-style parsing for the controller binary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControllerConfiguration<C, R> {
    /// The legacy `authentication` key was present (`config.go:70-91`).
    ///
    /// Go hands the raw bytes to `oidc.LoadAuthenticationConfiguration` and
    /// returns **fixed** values for everything else, without ever consulting
    /// the `config` key (which may be absent or even invalid):
    /// - gRPC options: keepalive enforcement `MinTime: 1s`,
    ///   `PermitWithoutStream: true` (`config.go:85-90`)
    /// - `Provisioning{Enabled: false}`
    /// - `LeasePolicy{MaxTags: 10}`
    ///
    /// The raw authentication document is surfaced for the auth stack to
    /// parse (Phase 3); callers must apply the fixed defaults above.
    Legacy { authentication: String, router: R },
    /// The modern `config` key was parsed (`config.go:93-119`).
    Config { config: C, router: R },
}

/// [`ControllerConfiguration`] instantiated with the real ConfigMap models —
/// the type the manager binary works with.
pub type LoadedControllerConfiguration = ControllerConfiguration<
    jumpstarter_controller_config::types::Config,
    jumpstarter_controller_config::router::Router,
>;

/// Strict deserialization of a configuration document, mirroring Go's
/// `yaml.UnmarshalStrict`: any key the target model does not know, at any
/// nesting depth, is an error (first offender reported, like encoding/json's
/// `DisallowUnknownFields`). Used for the `config` key only — Go parses the
/// `router` key with lenient `yaml.Unmarshal`. Strictness lives here at the
/// call site rather than on the models because the workspace forbids
/// `deny_unknown_fields` (K8s pruning semantics; the types are also consumed
/// by lenient readers such as the operator).
fn from_str_strict<T>(raw: &str, key: &'static str) -> Result<T, ConfigMapError>
where
    T: DeserializeOwned,
{
    let mut unknown: Option<String> = None;
    let value = serde_ignored::deserialize(
        serde_yaml_ng::Deserializer::from_str(raw),
        |path: serde_ignored::Path<'_>| {
            if unknown.is_none() {
                // serde_ignored renders opaque layers it cannot attribute — such
                // as the `deserialize_with = "null_default"` Option wrapper on
                // `authentication.jwt` — as a `?` segment (`Path::Unknown`).
                // Drop those so the diagnostic reads as the document path
                // (e.g. `authentication.jwt.0.issuer.<field>`).
                let path = path
                    .to_string()
                    .split('.')
                    .filter(|segment| *segment != "?")
                    .collect::<Vec<_>>()
                    .join(".");
                unknown = Some(path);
            }
        },
    )
    .map_err(|source| ConfigMapError::Parse { key, source })?;
    match unknown {
        Some(path) => Err(ConfigMapError::UnknownField { key, path }),
        None => Ok(value),
    }
}

/// Fetches the `jumpstarter-controller` ConfigMap from `namespace`.
pub async fn fetch_configmap(client: Client, namespace: &str) -> Result<ConfigMap, ConfigMapError> {
    Api::<ConfigMap>::namespaced(client, namespace)
        .get(CONFIGMAP_NAME)
        .await
        .map_err(|source| ConfigMapError::Fetch {
            namespace: namespace.to_string(),
            name: CONFIGMAP_NAME.to_string(),
            source: Box::new(source),
        })
}

/// Pure port of the key handling in `config.go` `LoadConfiguration`
/// (`config.go:47-120`), operating on the ConfigMap's `.data` so the error
/// taxonomy is testable without a cluster.
///
/// Order is load-bearing and mirrors Go exactly:
/// 1. `router` missing → fatal (takes precedence over everything else)
/// 2. `router` parse failure → fatal (lenient parse: unknown fields ignored)
/// 3. `authentication` present → legacy short-circuit (the `config` key is
///    neither required nor parsed)
/// 4. `config` missing → fatal
/// 5. `config` parse failure → fatal (strict parse: unknown fields fatal)
pub fn parse_controller_configuration<C, R>(
    data: &BTreeMap<String, String>,
) -> Result<ControllerConfiguration<C, R>, ConfigMapError>
where
    C: DeserializeOwned,
    R: DeserializeOwned,
{
    // config.go:60-63
    let raw_router = data.get(ROUTER_KEY).ok_or(ConfigMapError::MissingKey {
        context: "LoadConfiguration",
        key: ROUTER_KEY,
    })?;

    // config.go:65-68 (non-strict yaml.Unmarshal)
    let router: R =
        serde_yaml_ng::from_str(raw_router).map_err(|source| ConfigMapError::Parse {
            key: ROUTER_KEY,
            source,
        })?;

    // config.go:70-91 — presence check, not non-empty: an empty string under
    // the `authentication` key still selects the legacy path in Go.
    if let Some(raw_authentication) = data.get(AUTHENTICATION_KEY) {
        return Ok(ControllerConfiguration::Legacy {
            authentication: raw_authentication.clone(),
            router,
        });
    }

    // config.go:93-96
    let raw_config = data.get(CONFIG_KEY).ok_or(ConfigMapError::MissingKey {
        context: "LoadConfiguration",
        key: CONFIG_KEY,
    })?;

    // config.go:98-101 (yaml.UnmarshalStrict: unknown fields are fatal)
    let config: C = from_str_strict(raw_config, CONFIG_KEY)?;

    Ok(ControllerConfiguration::Config { config, router })
}

/// Pure port of `config.go` `LoadRouterConfiguration` (`config.go:18-45`):
/// the router binary requires only the `config` key (it consumes just the
/// `grpc` section) and never reads `router` or `authentication`.
pub fn parse_router_configuration<C>(data: &BTreeMap<String, String>) -> Result<C, ConfigMapError>
where
    C: DeserializeOwned,
{
    // config.go:28-31
    let raw_config = data.get(CONFIG_KEY).ok_or(ConfigMapError::MissingKey {
        context: "LoadRouterConfiguration",
        key: CONFIG_KEY,
    })?;

    // config.go:33-37 (yaml.UnmarshalStrict: unknown fields are fatal)
    from_str_strict(raw_config, CONFIG_KEY)
}

/// Async equivalent of Go `LoadConfiguration` up to (but excluding) the
/// authentication/gRPC-option construction: fetch the ConfigMap and apply
/// [`parse_controller_configuration`].
pub async fn load_controller_configuration<C, R>(
    client: Client,
    namespace: &str,
) -> Result<ControllerConfiguration<C, R>, ConfigMapError>
where
    C: DeserializeOwned,
    R: DeserializeOwned,
{
    let configmap = fetch_configmap(client, namespace).await?;
    parse_controller_configuration(&configmap.data.unwrap_or_default())
}

/// Async equivalent of Go `LoadRouterConfiguration`: fetch the ConfigMap and
/// apply [`parse_router_configuration`].
pub async fn load_router_configuration<C>(
    client: Client,
    namespace: &str,
) -> Result<C, ConfigMapError>
where
    C: DeserializeOwned,
{
    let configmap = fetch_configmap(client, namespace).await?;
    parse_router_configuration(&configmap.data.unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    /// Minimal stand-ins for the `jumpstarter-controller-config` models,
    /// shaped like `controller/internal/config/types.go`.
    #[derive(Debug, Deserialize, PartialEq, Eq)]
    struct TestConfig {
        #[serde(default)]
        provisioning: TestProvisioning,
    }

    #[derive(Debug, Default, Deserialize, PartialEq, Eq)]
    struct TestProvisioning {
        #[serde(default)]
        enabled: bool,
    }

    #[derive(Debug, Deserialize, PartialEq, Eq)]
    struct TestRouterEntry {
        endpoint: String,
    }

    type TestRouter = BTreeMap<String, TestRouterEntry>;

    fn data(entries: &[(&str, &str)]) -> BTreeMap<String, String> {
        entries
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    const ROUTER_YAML: &str = "default:\n  endpoint: router.example.com:8083\n";
    const CONFIG_YAML: &str = "provisioning:\n  enabled: true\n";

    fn parse(
        data: &BTreeMap<String, String>,
    ) -> Result<ControllerConfiguration<TestConfig, TestRouter>, ConfigMapError> {
        parse_controller_configuration::<TestConfig, TestRouter>(data)
    }

    #[test]
    fn controller_modern_happy_path() {
        let parsed = parse(&data(&[("router", ROUTER_YAML), ("config", CONFIG_YAML)])).unwrap();
        match parsed {
            ControllerConfiguration::Config { config, router } => {
                assert!(config.provisioning.enabled);
                assert_eq!(router["default"].endpoint, "router.example.com:8083");
            }
            other => panic!("expected Config variant, got {other:?}"),
        }
    }

    #[test]
    fn controller_missing_router_is_fatal_with_go_message() {
        // config.go:60-63: the router section is required before anything else.
        let err = parse(&data(&[("config", CONFIG_YAML)])).unwrap_err();
        assert!(matches!(
            err,
            ConfigMapError::MissingKey {
                context: "LoadConfiguration",
                key: "router"
            }
        ));
        assert_eq!(err.to_string(), "LoadConfiguration: missing router section");
    }

    #[test]
    fn controller_missing_router_takes_precedence_over_legacy() {
        // Go checks `router` before `authentication`; a legacy configmap
        // without a router section still fails on the router key.
        let err = parse(&data(&[("authentication", "{}")])).unwrap_err();
        assert!(matches!(
            err,
            ConfigMapError::MissingKey {
                context: "LoadConfiguration",
                key: "router"
            }
        ));
    }

    #[test]
    fn controller_router_parse_failure_is_fatal() {
        let err = parse(&data(&[
            ("router", "default: [unclosed"),
            ("config", CONFIG_YAML),
        ]))
        .unwrap_err();
        assert!(matches!(err, ConfigMapError::Parse { key: "router", .. }));
    }

    #[test]
    fn controller_legacy_short_circuits_even_when_config_is_invalid() {
        // config.go:70-91: the legacy path never consults the config key, so
        // an invalid `config` document must not be an error.
        let parsed = parse(&data(&[
            ("router", ROUTER_YAML),
            ("authentication", "jwt: []"),
            ("config", ": not : valid : yaml ["),
        ]))
        .unwrap();
        match parsed {
            ControllerConfiguration::Legacy {
                authentication,
                router,
            } => {
                assert_eq!(authentication, "jwt: []");
                assert_eq!(router["default"].endpoint, "router.example.com:8083");
            }
            other => panic!("expected Legacy variant, got {other:?}"),
        }
    }

    #[test]
    fn controller_legacy_selected_by_presence_even_if_empty() {
        // Go's `configmap.Data["authentication"]` comma-ok is a presence
        // check: an empty value still selects the legacy path.
        let parsed = parse(&data(&[("router", ROUTER_YAML), ("authentication", "")])).unwrap();
        assert!(matches!(
            parsed,
            ControllerConfiguration::Legacy { ref authentication, .. } if authentication.is_empty()
        ));
    }

    #[test]
    fn controller_missing_config_is_fatal_with_go_message() {
        let err = parse(&data(&[("router", ROUTER_YAML)])).unwrap_err();
        assert!(matches!(
            err,
            ConfigMapError::MissingKey {
                context: "LoadConfiguration",
                key: "config"
            }
        ));
        assert_eq!(err.to_string(), "LoadConfiguration: missing config section");
    }

    #[test]
    fn controller_config_parse_failure_is_fatal() {
        let err = parse(&data(&[
            ("router", ROUTER_YAML),
            ("config", "provisioning: [not a mapping]"),
        ]))
        .unwrap_err();
        assert!(matches!(err, ConfigMapError::Parse { key: "config", .. }));
    }

    #[test]
    fn controller_empty_data_reports_missing_router() {
        let err = parse(&BTreeMap::new()).unwrap_err();
        assert!(matches!(
            err,
            ConfigMapError::MissingKey {
                context: "LoadConfiguration",
                key: "router"
            }
        ));
    }

    #[test]
    fn router_happy_path_reads_only_config_key() {
        // The router binary never reads the `router` key.
        let config =
            parse_router_configuration::<TestConfig>(&data(&[("config", CONFIG_YAML)])).unwrap();
        assert!(config.provisioning.enabled);
    }

    #[test]
    fn router_missing_config_is_fatal_with_go_message() {
        // Even a configmap that satisfies the controller (router + legacy
        // authentication) fails for the router binary without `config`.
        let err = parse_router_configuration::<TestConfig>(&data(&[
            ("router", ROUTER_YAML),
            ("authentication", "{}"),
        ]))
        .unwrap_err();
        assert!(matches!(
            err,
            ConfigMapError::MissingKey {
                context: "LoadRouterConfiguration",
                key: "config"
            }
        ));
        assert_eq!(
            err.to_string(),
            "LoadRouterConfiguration: missing config section"
        );
    }

    #[test]
    fn router_config_parse_failure_is_fatal() {
        let err = parse_router_configuration::<TestConfig>(&data(&[(
            "config",
            ": not : valid : yaml [",
        )]))
        .unwrap_err();
        assert!(matches!(err, ConfigMapError::Parse { key: "config", .. }));
    }

    #[test]
    fn config_unknown_field_is_fatal_like_go() {
        // config.go:99 parses the `config` key with yaml.UnmarshalStrict:
        // a typo'd/unknown key refuses to start. (Go's message for this
        // document is `json: unknown field "typoedField"`.)
        let err = parse(&data(&[
            ("router", ROUTER_YAML),
            ("config", "provisioning:\n  enabled: true\ntypoedField: 1\n"),
        ]))
        .unwrap_err();
        assert!(matches!(
            err,
            ConfigMapError::UnknownField {
                key: "config",
                ref path
            } if path == "typoedField"
        ));
        assert_eq!(
            err.to_string(),
            "unable to parse config section: unknown field \"typoedField\""
        );
    }

    #[test]
    fn config_nested_unknown_field_is_fatal_like_go() {
        // Strictness reaches every nesting level, including jwt[] entries:
        // verified against Go apimachinery v0.33.0, where this document fails
        // UnmarshalStrict with `json: unknown field "egressSelectorType"`.
        // Uses the real models since the jwt subtree lives there.
        use jumpstarter_controller_config::types::Config;

        let config_yaml = "\
authentication:
  jwt:
  - issuer:
      url: https://issuer.example.com
      audiences: [aud]
      egressSelectorType: cluster
";
        let err = parse_controller_configuration::<Config, TestRouter>(&data(&[
            ("router", ROUTER_YAML),
            ("config", config_yaml),
        ]))
        .unwrap_err();
        match err {
            ConfigMapError::UnknownField { key, path } => {
                assert_eq!(key, "config");
                assert_eq!(path, "authentication.jwt.0.issuer.egressSelectorType");
            }
            other => panic!("expected UnknownField, got {other:?}"),
        }
    }

    #[test]
    fn router_key_unknown_fields_are_tolerated_like_go() {
        // config.go:66 parses the `router` key with *lenient* yaml.Unmarshal:
        // unknown fields in router entries are ignored, not fatal.
        let parsed = parse(&data(&[
            (
                "router",
                "default:\n  endpoint: router.example.com:8083\n  futureField: 1\n",
            ),
            ("config", CONFIG_YAML),
        ]))
        .unwrap();
        match parsed {
            ControllerConfiguration::Config { router, .. } => {
                assert_eq!(router["default"].endpoint, "router.example.com:8083");
            }
            other => panic!("expected Config variant, got {other:?}"),
        }
    }

    #[test]
    fn router_binary_config_unknown_field_is_fatal_like_go() {
        // config.go:34: the router binary reads the same `config` key with
        // yaml.UnmarshalStrict too.
        let err = parse_router_configuration::<TestConfig>(&data(&[(
            "config",
            "provisioning:\n  enabled: true\n  someFutureKnob: true\n",
        )]))
        .unwrap_err();
        assert!(matches!(
            err,
            ConfigMapError::UnknownField {
                key: "config",
                ref path
            } if path == "provisioning.someFutureKnob"
        ));
    }

    #[test]
    fn concrete_models_parse_operator_shaped_configmap() {
        // End-to-end with the real jumpstarter-controller-config models and
        // the operator-shaped fixtures committed with that crate.
        use jumpstarter_controller_config::{router::Router, types::Config};

        let fixture_data = data(&[
            (
                CONFIG_KEY,
                include_str!("../../jumpstarter-controller-config/tests/fixtures/config.yaml"),
            ),
            (
                ROUTER_KEY,
                include_str!("../../jumpstarter-controller-config/tests/fixtures/router.yaml"),
            ),
        ]);

        let parsed: LoadedControllerConfiguration =
            parse_controller_configuration::<Config, Router>(&fixture_data).unwrap();
        match parsed {
            ControllerConfiguration::Config { config, router } => {
                assert!(config.provisioning.enabled);
                assert_eq!(config.lease_policy.max_tags, 10);
                assert_eq!(config.grpc.keepalive.min_time, "1s");
                assert_eq!(router.len(), 3);
                assert_eq!(
                    router["default"].endpoint,
                    "router-0.jumpstarter.127.0.0.1.nip.io:443"
                );
                assert_eq!(router["router-1"].labels["router-index"], "1");
            }
            other => panic!("expected Config variant, got {other:?}"),
        }

        // The router binary reads the same `config` key into the same model.
        let config: Config = parse_router_configuration(&fixture_data).unwrap();
        assert!(config.grpc.keepalive.permit_without_stream);
    }

    #[test]
    fn fetch_error_taxonomy_not_found() {
        let err = ConfigMapError::Fetch {
            namespace: "jumpstarter-lab".into(),
            name: CONFIGMAP_NAME.into(),
            source: Box::new(kube::Error::Api(
                kube::core::Status::failure(
                    "configmaps \"jumpstarter-controller\" not found",
                    "NotFound",
                )
                .with_code(404)
                .boxed(),
            )),
        };
        assert!(err.is_not_found());
        let err = ConfigMapError::MissingKey {
            context: "LoadConfiguration",
            key: ROUTER_KEY,
        };
        assert!(!err.is_not_found());
    }
}
