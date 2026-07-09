//! Bearer extraction, metadata attribute extraction, and the basic authorizer,
//! ported from `controller/internal/authentication/bearer.go`,
//! `controller/internal/authorization/metadata.go`,
//! `controller/internal/authorization/basic.go`, and the `k8sToGRPCCode`
//! mapping in `controller/internal/oidc/token.go`.
//!
//! ## Where this sits in the Go request flow
//!
//! `VerifyOIDCToken` (`oidc/token.go:42-57`) authenticates the bearer token
//! then calls `ContextAttributes` to derive `(user, namespace, resource,
//! name)`; `VerifyClientObjectToken` / `VerifyExporterObjectToken`
//! (`oidc/token.go:59-135`) then run [`BasicAuthorizer`] and, on allow, fetch
//! the CR (its lookup errors mapped by [`k8s_to_grpc_code`]). The token
//! *authentication* half lives in `validator.rs`; this module is the
//! *authorization* half plus the bearer/metadata plumbing it needs.
//!
//! ## Deliberate bearer-extraction duplication
//!
//! [`bearer_token_from_metadata`] is a byte-identical re-port of the same Go
//! function that `jumpstarter-router-service::auth` already ports
//! (`bearer.go:32-62`). We keep an independent copy rather than depend on the
//! router-service crate: the controller must not pull the router's RPC surface
//! into its dependency graph just to share ~20 lines. The two copies are held
//! identical by test (`bearer_extraction_statuses_match_go` here mirrors the
//! router's `bearer_extraction_statuses_match_go`); if one changes, both must.

use jumpstarter_controller_api::client::{Client, ClientSpec};
use jumpstarter_controller_api::exporter::Exporter;
use kube::api::{Api, PostParams};
use thiserror::Error;
use tonic::metadata::MetadataMap;
use tonic::{Code, Status};

use crate::normalize::{go_quote, is_kubernetes_service_account, normalize_oidc_username};

// ---------------------------------------------------------------------------
// Bearer extraction (bearer.go)
// ---------------------------------------------------------------------------

/// Extracts the bearer token from `authorization` metadata, porting
/// `BearerTokenFromContext` (`controller/internal/authentication/bearer.go:32-62`)
/// including its status codes and message strings:
///   - no header → `UNAUTHENTICATED "missing authorization header"`;
///   - multiple headers → `INVALID_ARGUMENT "multiple authorization headers"`;
///   - not `Bearer ` (case-insensitive) or shorter than 7 chars →
///     `INVALID_ARGUMENT "malformed authorization header"`.
///
/// Go's `INVALID_ARGUMENT "missing metadata"` branch (`bearer.go:33-36`) is
/// unreachable under tonic, which always materializes a `MetadataMap`.
// `tonic::Status` is the RPC-layer error convention across the workspace;
// boxing it would churn every call site for nothing.
#[allow(clippy::result_large_err)]
pub fn bearer_token_from_metadata(metadata: &MetadataMap) -> Result<String, Status> {
    let values: Vec<_> = metadata.get_all("authorization").iter().collect();

    if values.is_empty() {
        return Err(Status::unauthenticated("missing authorization header"));
    }

    // RFC 7230 §3.2.2: a sender MUST NOT generate multiple header fields with
    // the same field name (bearer.go:44-48).
    if values.len() > 1 {
        return Err(Status::invalid_argument("multiple authorization headers"));
    }

    let authorization = values[0]
        .to_str()
        .map_err(|_| Status::invalid_argument("malformed authorization header"))?;

    // Go: len < 7 || !strings.EqualFold(authorization[:7], "Bearer ").
    if authorization.len() < 7 || !authorization.as_bytes()[..7].eq_ignore_ascii_case(b"Bearer ") {
        return Err(Status::invalid_argument("malformed authorization header"));
    }

    Ok(authorization[7..].to_string())
}

// ---------------------------------------------------------------------------
// Attribute extraction (metadata.go)
// ---------------------------------------------------------------------------

/// The authorization attributes derived from an authenticated request,
/// mirroring `authorizer.AttributesRecord` (`metadata.go:170-175`) for the
/// fields Jumpstarter populates.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Attributes {
    /// `userInfo.GetName()` — the authenticated username / OIDC subject.
    pub user: String,
    /// `jumpstarter-namespace` metadata value.
    pub namespace: String,
    /// `jumpstarter-kind` metadata value (`"Client"` or `"Exporter"`).
    pub resource: String,
    /// The resolved resource name (provided-or-derived; see
    /// [`MetadataAttributesGetter::context_attributes`]).
    pub name: String,
}

/// Configurable metadata keys, ported from `MetadataAttributesGetterConfig`
/// (`metadata.go:25-39`). Wired in `cmd/main.go:279-282` to
/// `jumpstarter-namespace` / `jumpstarter-kind` / `jumpstarter-name`; see
/// [`MetadataAttributesGetter::jumpstarter`].
#[derive(Debug, Clone)]
pub struct MetadataAttributesGetter {
    pub namespace_key: String,
    pub resource_key: String,
    pub name_key: String,
}

impl MetadataAttributesGetter {
    /// Arbitrary keys (`NewMetadataAttributesGetter`).
    pub fn new(
        namespace_key: impl Into<String>,
        resource_key: impl Into<String>,
        name_key: impl Into<String>,
    ) -> Self {
        Self {
            namespace_key: namespace_key.into(),
            resource_key: resource_key.into(),
            name_key: name_key.into(),
        }
    }

    /// The production wiring from `cmd/main.go:279-282`.
    pub fn jumpstarter() -> Self {
        Self::new(
            "jumpstarter-namespace",
            "jumpstarter-kind",
            "jumpstarter-name",
        )
    }

    /// Ports `MetadataAttributesGetter.ContextAttributes` (`metadata.go:115-176`).
    ///
    /// `user` is `userInfo.GetName()`. Namespace and resource are required
    /// metadata. For the name:
    ///   - **internal** (`internal:` prefix) and **service-account** subjects
    ///     skip auto-provisioning — the resource already exists, so a name
    ///     MUST be provided and is accepted verbatim;
    ///   - **external OIDC** subjects derive the name from the username via
    ///     [`normalize_oidc_username`]; a provided name that disagrees is
    ///     rejected with the exact `resource name mismatch` message
    ///     (`%q`-quoted operands via [`go_quote`]).
    ///
    /// Go's `FromIncomingContext` "missing metadata" branch
    /// (`metadata.go:119-122`) is unreachable under tonic (a `MetadataMap`
    /// always exists), so it is omitted.
    // See `bearer_token_from_metadata` for the `result_large_err` rationale.
    #[allow(clippy::result_large_err)]
    pub fn context_attributes(
        &self,
        metadata: &MetadataMap,
        user: &str,
    ) -> Result<Attributes, Status> {
        let namespace = md_get(metadata, &self.namespace_key)?;
        let resource = md_get(metadata, &self.resource_key)?;

        // Check if the client provided a name via metadata. Go swallows any
        // InvalidArgument here (both "missing" and "multiple") and proceeds
        // with providedName == "" (metadata.go:135-139); only a non-
        // InvalidArgument error would propagate, and `md_get` never returns
        // one, so nothing ever propagates from this lookup.
        let provided_name = match md_get(metadata, &self.name_key) {
            Ok(value) => value,
            Err(status) if status.code() == Code::InvalidArgument => String::new(),
            Err(status) => return Err(status),
        };

        // Determine the resource name.
        let resource_name = if user.starts_with("internal:") || is_kubernetes_service_account(user)
        {
            // Pre-existing authentication: accept the provided name as-is.
            if provided_name.is_empty() {
                return Err(Status::invalid_argument(
                    "resource name required for pre-existing authentication",
                ));
            }
            provided_name
        } else {
            // External OIDC with auto-provisioning: derive the name from the
            // username to prevent identity confusion.
            let expected_name = normalize_oidc_username(user);

            if !provided_name.is_empty() && provided_name != expected_name {
                return Err(Status::invalid_argument(format!(
                    "resource name mismatch: provided {} but expected {} (derived from OIDC username {})",
                    go_quote(&provided_name),
                    go_quote(&expected_name),
                    go_quote(user),
                )));
            }

            expected_name
        };

        Ok(Attributes {
            user: user.to_string(),
            namespace,
            resource,
            name: resource_name,
        })
    }
}

/// Ports `mdGet` (`metadata.go:178-187`): exactly one value for `key`, else
/// `INVALID_ARGUMENT "missing metadata: <key>"` (zero) or
/// `INVALID_ARGUMENT "multiple metadata: <key>"` (more than one).
// See `bearer_token_from_metadata` for the `result_large_err` rationale.
#[allow(clippy::result_large_err)]
fn md_get(metadata: &MetadataMap, key: &str) -> Result<String, Status> {
    let mut values = metadata.get_all(key).iter();
    let first = match values.next() {
        Some(value) => value,
        None => {
            return Err(Status::invalid_argument(format!("missing metadata: {key}")));
        }
    };
    if values.next().is_some() {
        return Err(Status::invalid_argument(format!(
            "multiple metadata: {key}"
        )));
    }
    // tonic pre-validates ASCII metadata values as visible-ASCII header
    // strings, so `to_str` cannot fail for a conformant client; a non-ASCII
    // byte sequence (which could not name a real resource anyway) is rejected
    // rather than silently lost. Go, receiving raw bytes, has no analogue.
    first
        .to_str()
        .map(str::to_owned)
        .map_err(|_| Status::invalid_argument(format!("malformed metadata: {key}")))
}

// ---------------------------------------------------------------------------
// k8s error -> gRPC code mapping (oidc/token.go:19-40)
// ---------------------------------------------------------------------------

/// The Kubernetes status reasons `k8sToGRPCCode` switches on
/// (`oidc/token.go:19-40`), used to classify CR lookup/create failures.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StoreErrorKind {
    NotFound,
    Forbidden,
    Unauthorized,
    AlreadyExists,
    Conflict,
    Invalid,
    ServiceUnavailable,
    Timeout,
    /// Anything not matching an `apierrors.Is*` predicate (`default` arm).
    Other,
}

/// A classified error from the CR [`ObjectStore`], carrying the original
/// message (Go forwards the raw apiserver text verbatim into the wire status
/// in `oidc/token.go`; preserving it keeps that contract available to the
/// service layer).
#[derive(Debug, Clone, Error)]
#[error("{message}")]
pub struct StoreError {
    pub kind: StoreErrorKind,
    pub message: String,
}

impl StoreError {
    pub fn new(kind: StoreErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    /// `apierrors.IsNotFound` — gates the auto-provisioning branch
    /// (`basic.go:54`).
    pub fn is_not_found(&self) -> bool {
        self.kind == StoreErrorKind::NotFound
    }

    /// The gRPC code Go would attach via `k8sToGRPCCode` (`oidc/token.go`).
    pub fn grpc_code(&self) -> Code {
        k8s_to_grpc_code(self.kind)
    }

    /// Classifies a `kube::Error` into a [`StoreError`], preserving the full
    /// error text. Non-API errors (transport, decode) map to `Other`
    /// (`Internal`), matching Go's `default` arm for anything that is not a
    /// recognized apiserver status.
    fn from_kube(err: kube::Error) -> Self {
        let kind = match &err {
            kube::Error::Api(response) => classify_status(&response.reason, response.code),
            _ => StoreErrorKind::Other,
        };
        Self {
            kind,
            message: err.to_string(),
        }
    }
}

/// Ports the `k8sToGRPCCode` switch (`oidc/token.go:19-40`).
pub fn k8s_to_grpc_code(kind: StoreErrorKind) -> Code {
    match kind {
        StoreErrorKind::NotFound => Code::NotFound,
        StoreErrorKind::Forbidden => Code::PermissionDenied,
        StoreErrorKind::Unauthorized => Code::Unauthenticated,
        StoreErrorKind::AlreadyExists => Code::AlreadyExists,
        StoreErrorKind::Conflict => Code::Aborted,
        StoreErrorKind::Invalid => Code::InvalidArgument,
        StoreErrorKind::ServiceUnavailable => Code::Unavailable,
        StoreErrorKind::Timeout => Code::DeadlineExceeded,
        StoreErrorKind::Other => Code::Internal,
    }
}

/// Maps a Kubernetes status `reason`/`code` to a [`StoreErrorKind`], mirroring
/// the `apierrors.Is*` predicates: prefer the status reason (as k8s does),
/// falling back to the HTTP status code for the reasons that are unambiguous
/// by code (404/403/401/503/504). A `409` with no recognized reason cannot be
/// told apart from Conflict/AlreadyExists and maps to `Other`, exactly as
/// Go's `default` arm would.
fn classify_status(reason: &str, code: u16) -> StoreErrorKind {
    match reason {
        "NotFound" => StoreErrorKind::NotFound,
        "Forbidden" => StoreErrorKind::Forbidden,
        "Unauthorized" => StoreErrorKind::Unauthorized,
        "AlreadyExists" => StoreErrorKind::AlreadyExists,
        "Conflict" => StoreErrorKind::Conflict,
        "Invalid" => StoreErrorKind::Invalid,
        "ServiceUnavailable" => StoreErrorKind::ServiceUnavailable,
        "Timeout" | "ServerTimeout" => StoreErrorKind::Timeout,
        _ => match code {
            404 => StoreErrorKind::NotFound,
            403 => StoreErrorKind::Forbidden,
            401 => StoreErrorKind::Unauthorized,
            503 => StoreErrorKind::ServiceUnavailable,
            504 => StoreErrorKind::Timeout,
            _ => StoreErrorKind::Other,
        },
    }
}

// ---------------------------------------------------------------------------
// Basic authorizer (basic.go)
// ---------------------------------------------------------------------------

/// Authorization decision, mirroring `authorizer.Decision`. `BasicAuthorizer`
/// only ever yields `Allow`/`Deny` (never `NoOpinion`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Decision {
    Allow,
    Deny,
}

/// The CR-fetch seam the authorizer needs, factored into a trait so the
/// decision logic is testable without a cluster. The real implementation is
/// [`KubeObjectStore`]; tests supply a mock.
///
/// Mirrors the two `client.Client` operations `basic.go` performs: get an
/// Exporter/Client, and (auto-provisioning) create a Client.
// Internal seam only (never used as `dyn`), so native `async fn` is fine.
#[allow(async_fn_in_trait)]
pub trait ObjectStore {
    async fn get_client(&self, namespace: &str, name: &str) -> Result<Client, StoreError>;
    async fn create_client(&self, client: &Client) -> Result<(), StoreError>;
    async fn get_exporter(&self, namespace: &str, name: &str) -> Result<Exporter, StoreError>;
}

/// Ports `BasicAuthorizer` (`controller/internal/authorization/basic.go`).
pub struct BasicAuthorizer<S> {
    store: S,
    prefix: String,
    provisioning: bool,
}

impl<S: ObjectStore> BasicAuthorizer<S> {
    /// `NewBasicAuthorizer(client, prefix, provisioning)` (`basic.go:21-27`).
    pub fn new(store: S, prefix: impl Into<String>, provisioning: bool) -> Self {
        Self {
            store,
            prefix: prefix.into(),
            provisioning,
        }
    }

    /// Ports `BasicAuthorizer.Authorize` (`basic.go:29-80`).
    ///
    /// Returns `Ok((decision, reason))` for the non-error outcomes (the reason
    /// mirrors Go's second return: `""` on allow/plain-deny, `"invalid object
    /// kind"` for an unknown resource — it is log-only and wire-invisible, the
    /// Go caller discards it). CR get/create failures return `Err(StoreError)`,
    /// mirroring Go's third return; the caller maps it via [`k8s_to_grpc_code`].
    ///
    /// Auto-provisioning: when a Client is not found **and** provisioning is
    /// enabled, a Client CR is created with `spec.username = user`, then the
    /// membership check runs against that just-built object (which therefore
    /// always allows). Provisioning **never** applies to Exporters
    /// (`basic.go:34-46` has no create path).
    pub async fn authorize(
        &self,
        attributes: &Attributes,
    ) -> Result<(Decision, String), StoreError> {
        match attributes.resource.as_str() {
            "Exporter" => {
                // Go: Deny "failed to get exporter" + err on failure.
                let exporter = self
                    .store
                    .get_exporter(&attributes.namespace, &attributes.name)
                    .await?;
                Ok(decide(
                    exporter.usernames(&self.prefix).contains(&attributes.user),
                ))
            }
            "Client" => {
                let client = match self
                    .store
                    .get_client(&attributes.namespace, &attributes.name)
                    .await
                {
                    Ok(client) => client,
                    Err(err) => {
                        if err.is_not_found() && self.provisioning {
                            // Build and create the Client CR (basic.go:55-66).
                            let mut client = Client::new(
                                &attributes.name,
                                ClientSpec {
                                    username: Some(attributes.user.clone()),
                                },
                            );
                            client.metadata.namespace = Some(attributes.namespace.clone());
                            // Go: Deny "failed to provision client" + err.
                            self.store.create_client(&client).await?;
                            client
                        } else {
                            // Go: Deny "failed to get client" + err.
                            return Err(err);
                        }
                    }
                };
                Ok(decide(
                    client.usernames(&self.prefix).contains(&attributes.user),
                ))
            }
            // Go: DecisionDeny, "invalid object kind", nil.
            _ => Ok((Decision::Deny, "invalid object kind".to_string())),
        }
    }
}

/// `slices.Contains(usernames, user)` → `(Allow, "")` else `(Deny, "")`.
fn decide(allowed: bool) -> (Decision, String) {
    if allowed {
        (Decision::Allow, String::new())
    } else {
        (Decision::Deny, String::new())
    }
}

/// The production [`ObjectStore`], backed by a `kube::Client`. Compiles now;
/// exercised end-to-end in the controller-service phase.
pub struct KubeObjectStore {
    client: kube::Client,
}

impl KubeObjectStore {
    pub fn new(client: kube::Client) -> Self {
        Self { client }
    }
}

impl ObjectStore for KubeObjectStore {
    async fn get_client(&self, namespace: &str, name: &str) -> Result<Client, StoreError> {
        let api: Api<Client> = Api::namespaced(self.client.clone(), namespace);
        api.get(name).await.map_err(StoreError::from_kube)
    }

    async fn create_client(&self, client: &Client) -> Result<(), StoreError> {
        let namespace = client.metadata.namespace.as_deref().unwrap_or_default();
        let api: Api<Client> = Api::namespaced(self.client.clone(), namespace);
        api.create(&PostParams::default(), client)
            .await
            .map(|_| ())
            .map_err(StoreError::from_kube)
    }

    async fn get_exporter(&self, namespace: &str, name: &str) -> Result<Exporter, StoreError> {
        let api: Api<Exporter> = Api::namespaced(self.client.clone(), namespace);
        api.get(name).await.map_err(StoreError::from_kube)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex;
    use tonic::metadata::MetadataValue;

    // -- bearer extraction (bearer.go) --------------------------------------

    /// Mirrors the router-service port's `bearer_extraction_statuses_match_go`
    /// (the two bearer ports are held identical by these twin tests).
    #[test]
    fn bearer_extraction_statuses_match_go() {
        // Missing header: UNAUTHENTICATED (bearer.go:40-42).
        let err = bearer_token_from_metadata(&MetadataMap::new()).unwrap_err();
        assert_eq!(err.code(), Code::Unauthenticated);
        assert_eq!(err.message(), "missing authorization header");

        // Multiple headers: INVALID_ARGUMENT (bearer.go:46-48).
        let mut metadata = MetadataMap::new();
        metadata.append("authorization", MetadataValue::from_static("Bearer a"));
        metadata.append("authorization", MetadataValue::from_static("Bearer b"));
        let err = bearer_token_from_metadata(&metadata).unwrap_err();
        assert_eq!(err.code(), Code::InvalidArgument);
        assert_eq!(err.message(), "multiple authorization headers");

        // Malformed scheme: INVALID_ARGUMENT (bearer.go:54-56).
        for value in ["Basic dXNlcg==", "Bearer", "Bear er x", ""] {
            let mut metadata = MetadataMap::new();
            metadata.insert("authorization", value.parse().unwrap());
            let err = bearer_token_from_metadata(&metadata).unwrap_err();
            assert_eq!(err.code(), Code::InvalidArgument, "value {value:?}");
            assert_eq!(err.message(), "malformed authorization header");
        }

        // strings.EqualFold: any case of "bearer " is accepted (bearer.go:54).
        for value in ["Bearer tok", "bearer tok", "BEARER tok", "BeArEr tok"] {
            let mut metadata = MetadataMap::new();
            metadata.insert("authorization", value.parse().unwrap());
            assert_eq!(bearer_token_from_metadata(&metadata).unwrap(), "tok");
        }
    }

    // -- metadata attributes (metadata_test.go) -----------------------------

    fn getter() -> MetadataAttributesGetter {
        // Same custom keys the Go tests use (metadata_test.go:145-149).
        MetadataAttributesGetter::new("test-namespace", "test-resource", "test-name")
    }

    /// Builds the metadata the Go tests build via `metadata.Pairs(...)` +
    /// optional `md.Append("test-name", providedName)`.
    fn md(resource: &str, provided_name: &str) -> MetadataMap {
        let mut metadata = MetadataMap::new();
        metadata.insert("test-namespace", "default".parse().unwrap());
        metadata.insert("test-resource", resource.parse().unwrap());
        if !provided_name.is_empty() {
            metadata.insert("test-name", provided_name.parse().unwrap());
        }
        metadata
    }

    // Transliterated from `TestContextAttributes_ExternalOIDC`
    // (metadata_test.go:144-248).
    #[test]
    fn context_attributes_external_oidc() {
        let getter = getter();
        let test_username = "dex:test-user@example.com";
        // Hardcoded in Go to avoid depending on the function under test
        // (metadata_test.go:155).
        let expected_name = "test-user-example-com";

        // metadata_test.go:165-169 — empty name uses the OIDC-derived name.
        let attrs = getter
            .context_attributes(&md("Client", ""), test_username)
            .expect("empty name should succeed");
        assert_eq!(attrs.name, expected_name);
        assert_eq!(attrs.user, test_username);

        // metadata_test.go:170-174 — matching name is accepted.
        let attrs = getter
            .context_attributes(&md("Client", expected_name), test_username)
            .expect("matching name should succeed");
        assert_eq!(attrs.name, expected_name);
        assert_eq!(attrs.user, test_username);

        // metadata_test.go:175-188 — mismatched / partial-match names reject
        // with InvalidArgument + a message naming provided + expected.
        for provided in ["arbitrary-name", "oidc-test-user"] {
            let err = getter
                .context_attributes(&md("Client", provided), test_username)
                .expect_err("mismatched name should reject");
            assert_eq!(err.code(), Code::InvalidArgument, "provided {provided:?}");
            assert!(
                err.message().contains("resource name mismatch"),
                "message: {}",
                err.message()
            );
            assert!(
                err.message().contains(provided),
                "message: {}",
                err.message()
            );
            assert!(
                err.message().contains(expected_name),
                "message: {}",
                err.message()
            );
        }
    }

    // Transliterated from `TestContextAttributes_InternalOIDC`
    // (metadata_test.go:250-340).
    #[test]
    fn context_attributes_internal_oidc() {
        let getter = getter();

        // metadata_test.go:266-273 — internal with no name errors.
        let err = getter
            .context_attributes(
                &md("Client", ""),
                "internal:exporter:default:my-exporter:uuid",
            )
            .expect_err("internal with no name should error");
        assert_eq!(err.code(), Code::InvalidArgument);
        assert!(err.message().contains("resource name required"));

        // metadata_test.go:274-279 — internal with a name accepts it as-is.
        let attrs = getter
            .context_attributes(
                &md("Client", "my-exporter"),
                "internal:exporter:default:my-exporter:uuid",
            )
            .expect("internal with name should succeed");
        assert_eq!(attrs.name, "my-exporter");
        assert_eq!(attrs.user, "internal:exporter:default:my-exporter:uuid");

        // metadata_test.go:280-285 — internal accepts an arbitrary name.
        let attrs = getter
            .context_attributes(
                &md("Client", "arbitrary-name"),
                "internal:client:default:test:uuid",
            )
            .expect("internal with different name should succeed");
        assert_eq!(attrs.name, "arbitrary-name");
    }

    // Transliterated from `TestContextAttributes_KubernetesServiceAccount`
    // (metadata_test.go:342-432).
    #[test]
    fn context_attributes_kubernetes_service_account() {
        let getter = getter();
        let sa = "dex:system:serviceaccount:jumpstarter-lab:test-exporter-sa";

        // metadata_test.go:358-365 — SA with no name errors.
        let err = getter
            .context_attributes(&md("Exporter", ""), sa)
            .expect_err("SA with no name should error");
        assert_eq!(err.code(), Code::InvalidArgument);
        assert!(err.message().contains("resource name required"));

        // metadata_test.go:366-371 — SA with a matching SA name accepts it.
        let attrs = getter
            .context_attributes(&md("Exporter", "test-exporter-sa"), sa)
            .expect("SA with matching name should succeed");
        assert_eq!(attrs.name, "test-exporter-sa");
        assert_eq!(attrs.user, sa);

        // metadata_test.go:372-377 — SA accepts an arbitrary name.
        let attrs = getter
            .context_attributes(
                &md("Exporter", "any-name-works"),
                "dex:system:serviceaccount:default:my-sa",
            )
            .expect("SA with arbitrary name should succeed");
        assert_eq!(attrs.name, "any-name-works");
    }

    /// Missing required namespace / resource metadata surfaces
    /// `INVALID_ARGUMENT "missing metadata: <key>"` (metadata.go:124-132, 178-187).
    #[test]
    fn missing_required_metadata_errors() {
        let getter = getter();

        let mut metadata = MetadataMap::new();
        metadata.insert("test-resource", "Client".parse().unwrap());
        let err = getter
            .context_attributes(&metadata, "dex:user")
            .unwrap_err();
        assert_eq!(err.code(), Code::InvalidArgument);
        assert_eq!(err.message(), "missing metadata: test-namespace");

        let mut metadata = MetadataMap::new();
        metadata.insert("test-namespace", "default".parse().unwrap());
        let err = getter
            .context_attributes(&metadata, "dex:user")
            .unwrap_err();
        assert_eq!(err.code(), Code::InvalidArgument);
        assert_eq!(err.message(), "missing metadata: test-resource");
    }

    /// Duplicate namespace metadata → `multiple metadata` (metadata.go:183-185).
    #[test]
    fn multiple_metadata_errors() {
        let getter = getter();
        let mut metadata = MetadataMap::new();
        metadata.append("test-namespace", "a".parse().unwrap());
        metadata.append("test-namespace", "b".parse().unwrap());
        metadata.insert("test-resource", "Client".parse().unwrap());
        let err = getter
            .context_attributes(&metadata, "dex:user")
            .unwrap_err();
        assert_eq!(err.code(), Code::InvalidArgument);
        assert_eq!(err.message(), "multiple metadata: test-namespace");
    }

    /// Multiple `name` headers are swallowed to `providedName == ""`, so an
    /// external-OIDC request falls back to the derived name rather than
    /// erroring (metadata.go:135-139).
    #[test]
    fn multiple_name_metadata_is_swallowed() {
        let getter = getter();
        let mut metadata = MetadataMap::new();
        metadata.insert("test-namespace", "default".parse().unwrap());
        metadata.insert("test-resource", "Client".parse().unwrap());
        metadata.append("test-name", "one".parse().unwrap());
        metadata.append("test-name", "two".parse().unwrap());
        let attrs = getter
            .context_attributes(&metadata, "dex:test-user@example.com")
            .expect("multiple name headers should be swallowed, not error");
        assert_eq!(attrs.name, "test-user-example-com");
    }

    // -- k8sToGRPCCode (oidc/token.go:19-40) --------------------------------

    #[test]
    fn k8s_to_grpc_code_mapping() {
        use StoreErrorKind::*;
        assert_eq!(k8s_to_grpc_code(NotFound), Code::NotFound);
        assert_eq!(k8s_to_grpc_code(Forbidden), Code::PermissionDenied);
        assert_eq!(k8s_to_grpc_code(Unauthorized), Code::Unauthenticated);
        assert_eq!(k8s_to_grpc_code(AlreadyExists), Code::AlreadyExists);
        assert_eq!(k8s_to_grpc_code(Conflict), Code::Aborted);
        assert_eq!(k8s_to_grpc_code(Invalid), Code::InvalidArgument);
        assert_eq!(k8s_to_grpc_code(ServiceUnavailable), Code::Unavailable);
        assert_eq!(k8s_to_grpc_code(Timeout), Code::DeadlineExceeded);
        assert_eq!(k8s_to_grpc_code(Other), Code::Internal);
    }

    #[test]
    fn classify_status_prefers_reason_then_code() {
        assert_eq!(classify_status("NotFound", 404), StoreErrorKind::NotFound);
        // 409 disambiguated only by reason.
        assert_eq!(classify_status("Conflict", 409), StoreErrorKind::Conflict);
        assert_eq!(
            classify_status("AlreadyExists", 409),
            StoreErrorKind::AlreadyExists
        );
        // Code fallback when the reason is empty.
        assert_eq!(classify_status("", 404), StoreErrorKind::NotFound);
        assert_eq!(classify_status("", 403), StoreErrorKind::Forbidden);
        assert_eq!(classify_status("", 504), StoreErrorKind::Timeout);
        // 409 with no reason is ambiguous -> Other (Go default arm).
        assert_eq!(classify_status("", 409), StoreErrorKind::Other);
        assert_eq!(classify_status("", 500), StoreErrorKind::Other);
    }

    // -- BasicAuthorizer (basic.go) -----------------------------------------

    const PREFIX: &str = "internal:";

    #[derive(Default)]
    struct MockStore {
        clients: HashMap<(String, String), Client>,
        exporters: HashMap<(String, String), Exporter>,
        created: Mutex<Vec<Client>>,
        create_error: Option<StoreError>,
    }

    impl MockStore {
        fn with_client(mut self, namespace: &str, client: Client) -> Self {
            let name = client.metadata.name.clone().unwrap();
            self.clients.insert((namespace.to_string(), name), client);
            self
        }
        fn with_exporter(mut self, namespace: &str, exporter: Exporter) -> Self {
            let name = exporter.metadata.name.clone().unwrap();
            self.exporters
                .insert((namespace.to_string(), name), exporter);
            self
        }
    }

    impl ObjectStore for MockStore {
        async fn get_client(&self, namespace: &str, name: &str) -> Result<Client, StoreError> {
            self.clients
                .get(&(namespace.to_string(), name.to_string()))
                .cloned()
                .ok_or_else(|| {
                    StoreError::new(
                        StoreErrorKind::NotFound,
                        "clients.jumpstarter.dev \"x\" not found",
                    )
                })
        }
        async fn create_client(&self, client: &Client) -> Result<(), StoreError> {
            if let Some(err) = &self.create_error {
                return Err(err.clone());
            }
            self.created.lock().unwrap().push(client.clone());
            Ok(())
        }
        async fn get_exporter(&self, namespace: &str, name: &str) -> Result<Exporter, StoreError> {
            self.exporters
                .get(&(namespace.to_string(), name.to_string()))
                .cloned()
                .ok_or_else(|| {
                    StoreError::new(
                        StoreErrorKind::NotFound,
                        "exporters.jumpstarter.dev \"x\" not found",
                    )
                })
        }
    }

    fn client_named(name: &str, namespace: &str, uid: &str, username: Option<&str>) -> Client {
        let mut client = Client::new(
            name,
            ClientSpec {
                username: username.map(str::to_owned),
            },
        );
        client.metadata.namespace = Some(namespace.to_string());
        client.metadata.uid = Some(uid.to_string());
        client
    }

    fn exporter_named(name: &str, namespace: &str, uid: &str, username: Option<&str>) -> Exporter {
        let mut exporter = Exporter::new(
            name,
            jumpstarter_controller_api::exporter::ExporterSpec {
                username: username.map(str::to_owned),
            },
        );
        exporter.metadata.namespace = Some(namespace.to_string());
        exporter.metadata.uid = Some(uid.to_string());
        exporter
    }

    fn attrs(resource: &str, name: &str, user: &str) -> Attributes {
        Attributes {
            user: user.to_string(),
            namespace: "default".to_string(),
            resource: resource.to_string(),
            name: name.to_string(),
        }
    }

    #[tokio::test]
    async fn client_allows_matching_internal_subject() {
        let client = client_named("my-client", "default", "uid-1", None);
        // usernames = ["internal:client:default:my-client:uid-1"].
        let user = "internal:client:default:my-client:uid-1";
        let authorizer = BasicAuthorizer::new(
            MockStore::default().with_client("default", client),
            PREFIX,
            false,
        );
        let (decision, reason) = authorizer
            .authorize(&attrs("Client", "my-client", user))
            .await
            .unwrap();
        assert_eq!(decision, Decision::Allow);
        assert_eq!(reason, "");
    }

    #[tokio::test]
    async fn client_allows_spec_username() {
        let client = client_named(
            "my-client",
            "default",
            "uid-1",
            Some("dex:alice@example.com"),
        );
        let authorizer = BasicAuthorizer::new(
            MockStore::default().with_client("default", client),
            PREFIX,
            false,
        );
        let (decision, _) = authorizer
            .authorize(&attrs("Client", "my-client", "dex:alice@example.com"))
            .await
            .unwrap();
        assert_eq!(decision, Decision::Allow);
    }

    #[tokio::test]
    async fn client_denies_unknown_user() {
        let client = client_named("my-client", "default", "uid-1", None);
        let authorizer = BasicAuthorizer::new(
            MockStore::default().with_client("default", client),
            PREFIX,
            false,
        );
        let (decision, reason) = authorizer
            .authorize(&attrs("Client", "my-client", "dex:someone-else"))
            .await
            .unwrap();
        assert_eq!(decision, Decision::Deny);
        assert_eq!(reason, "");
    }

    #[tokio::test]
    async fn client_not_found_without_provisioning_errors() {
        let authorizer = BasicAuthorizer::new(MockStore::default(), PREFIX, false);
        let err = authorizer
            .authorize(&attrs("Client", "ghost", "dex:someone"))
            .await
            .unwrap_err();
        // Go: Deny "failed to get client" + the NotFound error, which the
        // caller maps to gRPC NotFound (oidc/token.go:92).
        assert!(err.is_not_found());
        assert_eq!(err.grpc_code(), Code::NotFound);
    }

    #[tokio::test]
    async fn client_not_found_with_provisioning_creates_and_allows() {
        let store = MockStore::default();
        let authorizer = BasicAuthorizer::new(store, PREFIX, true);
        let user = "dex:new-user@example.com";
        let (decision, _) = authorizer
            .authorize(&attrs("Client", "new-user-example-com", user))
            .await
            .unwrap();
        // The provisioned Client carries spec.username = user, so the
        // membership check on the just-built object allows.
        assert_eq!(decision, Decision::Allow);
        let created = authorizer.store.created.lock().unwrap();
        assert_eq!(created.len(), 1);
        assert_eq!(
            created[0].metadata.name.as_deref(),
            Some("new-user-example-com")
        );
        assert_eq!(created[0].metadata.namespace.as_deref(), Some("default"));
        assert_eq!(created[0].spec.username.as_deref(), Some(user));
    }

    #[tokio::test]
    async fn client_provisioning_create_failure_errors() {
        let store = MockStore {
            create_error: Some(StoreError::new(StoreErrorKind::Conflict, "already exists")),
            ..Default::default()
        };
        let authorizer = BasicAuthorizer::new(store, PREFIX, true);
        let err = authorizer
            .authorize(&attrs("Client", "new-user", "dex:new-user"))
            .await
            .unwrap_err();
        // Go: Deny "failed to provision client" + err.
        assert_eq!(err.grpc_code(), Code::Aborted);
    }

    #[tokio::test]
    async fn exporter_allows_and_denies_but_never_provisions() {
        let exporter = exporter_named("my-exporter", "default", "uid-9", None);
        let user = "internal:exporter:default:my-exporter:uid-9";
        let authorizer = BasicAuthorizer::new(
            MockStore::default().with_exporter("default", exporter),
            PREFIX,
            // Even with provisioning enabled, Exporters are never created.
            true,
        );
        let (decision, _) = authorizer
            .authorize(&attrs("Exporter", "my-exporter", user))
            .await
            .unwrap();
        assert_eq!(decision, Decision::Allow);

        let (decision, _) = authorizer
            .authorize(&attrs("Exporter", "my-exporter", "dex:intruder"))
            .await
            .unwrap();
        assert_eq!(decision, Decision::Deny);

        // Missing Exporter is a hard error even with provisioning on.
        let err = authorizer
            .authorize(&attrs("Exporter", "ghost", "dex:x"))
            .await
            .unwrap_err();
        assert!(err.is_not_found());
        assert!(authorizer.store.created.lock().unwrap().is_empty());
    }

    #[tokio::test]
    async fn unknown_resource_denies_with_reason() {
        let authorizer = BasicAuthorizer::new(MockStore::default(), PREFIX, true);
        let (decision, reason) = authorizer
            .authorize(&attrs("Lease", "whatever", "dex:x"))
            .await
            .unwrap();
        assert_eq!(decision, Decision::Deny);
        assert_eq!(reason, "invalid object kind");
    }
}
