//! Token validation: the union authenticator that fronts every authenticated
//! controller RPC, porting `LoadAuthenticationConfiguration` +
//! `newJWTAuthenticator` (`controller/internal/config/oidc.go`) and the
//! `tokenunion.NewFailOnError` semantics they build on, plus the
//! `VerifyOIDCToken` failure surface (`controller/internal/oidc/token.go`).
//!
//! # What Go does
//!
//! `LoadAuthenticationConfiguration` (config/oidc.go:18-64) takes the
//! externally-configured `authentication.jwt[]` list and **appends** one more
//! entry pointing at the in-process internal signer (issuer = the signer's
//! issuer, CA = the per-start self-signed discovery cert, audiences =
//! `["jumpstarter"]`, username claim `sub` with prefix
//! `authentication.internal.prefix`, defaulting to `"internal:"`). Every entry
//! — external and internal alike — becomes a Kubernetes apiserver OIDC token
//! authenticator (`koidc.New`, config/oidc.go:97-101) and they are combined
//! with `tokenunion.NewFailOnError` (config/oidc.go:107).
//!
//! The union + each authenticator route **by issuer**: a modern k8s JWT
//! authenticator first parses the token's `iss` claim *without* verifying it
//! (`hasCorrectIssuer`), and if it does not equal the authenticator's
//! configured issuer URL it returns `(nil, false, nil)` — "not my token" — and
//! the union skips it. If the issuer matches, the authenticator verifies for
//! real; any failure (bad signature, wrong audience, expiry, …) is returned as
//! an error, and because the union is `failOnError` that error propagates
//! immediately. If no authenticator claims the issuer, the union returns
//! `(nil, false, nil)`, which `VerifyOIDCToken` turns into
//! `fmt.Errorf("failed to authenticate token")` (token.go:52-54).
//!
//! This module reproduces that exactly: [`TokenValidator::authenticate`] parses
//! the unverified issuer, finds the first authenticator whose issuer matches,
//! and returns that authenticator's verdict; a non-matching / unparseable
//! issuer is [`ValidationError::NotAuthenticated`]. The distinction the k8s
//! union draws — "not this issuer's token" vs "this issuer's token but invalid"
//! — is preserved: the former is `NotAuthenticated`, the latter is the
//! authenticator's specific error (e.g. [`ValidationError::Expired`]).
//!
//! # Approved divergences (documented, per the Phase-3 plan)
//!
//! 1. **Internal verification is in-process.** Go's appended internal
//!    authenticator is a `koidc` authenticator that fetches JWKS from the
//!    controller's own HTTPS discovery server on `127.0.0.1:8085` over the
//!    self-signed CA. We instead validate internal tokens directly against the
//!    in-memory [`Signer`] key — no HTTP round-trip, no self-signed-cert
//!    plumbing — via [`Signer::validate_at`]. The observable result (issuer +
//!    audience + ES256 signature check, then username = `prefix + sub`) is
//!    identical; the cost, a network hop to loopback, is not part of the wire
//!    contract.
//! 2. **CEL is unimplemented and fails fast.** Any JWT authenticator that uses
//!    a CEL expression (`claimMappings.{username,groups,uid}.expression`,
//!    `claimMappings.extra[].valueExpression`,
//!    `claimValidationRules[].expression`, `userValidationRules[].expression`)
//!    is rejected at load time with a loud [`LoadError::CelUnsupported`]. The
//!    e2e/dex deployments use claim+prefix mapping only.
//! 3. **JWKS refetch is rate-limited.** External issuers cache their JWKS and,
//!    on a token whose `kid` is not cached, refetch — but no more often than
//!    [`DEFAULT_MIN_JWKS_REFETCH`] (a hand-rolled equivalent of go-oidc's
//!    remote-keyset singleflight, hardening against unknown-kid fetch storms).
//! 4. **`ES512` (P-521) is not supported** because `jsonwebtoken` has no such
//!    algorithm; the OIDC-valid set we accept is otherwise complete
//!    (RS256/384/512, ES256/384, PS256/384/512). `RS256` is the universal
//!    default, so this is a benign gap; see the crate concerns.
//! 5. **External issuers without a configured CA** cannot be reached over
//!    HTTPS in this build: `reqwest` is compiled with `rustls-tls-manual-roots`
//!    (no built-in roots), so a no-CA issuer has an empty root store. The
//!    e2e/dex path always sets `certificateAuthority`; plain-HTTP issuers
//!    (loopback / tests) work regardless.
//!
//! The expired-token error text is a **client-matched contract**: spec 07
//! §11.4 pins `"token is expired"` as the substring the Python client scans for
//! to trigger re-authentication (it originates in the vendored k8s OIDC plugin
//! → go-oidc, whose expiry error is `oidc: token is expired (...)`). Both the
//! internal and external paths here surface [`ValidationError::Expired`], whose
//! `Display` is exactly `"token is expired"`.

use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use jsonwebtoken::jwk::JwkSet;
use jsonwebtoken::{decode, decode_header, Algorithm, DecodingKey, Validation};
use jumpstarter_controller_config::jwt_authenticator::JwtAuthenticator;
use jumpstarter_controller_config::types::Authentication;
use serde::Deserialize;
use serde_json::Value;
use thiserror::Error;
use tokio::sync::Mutex;

use crate::signer::{Signer, ValidateError};

/// Default internal username prefix when `authentication.internal.prefix` is
/// empty (`config/oidc.go:25-27`).
pub const DEFAULT_INTERNAL_PREFIX: &str = "internal:";

/// Minimum interval between JWKS refetches triggered by an unknown `kid`. A
/// hand-rolled stand-in for go-oidc's remote-keyset dedup: caps the blast
/// radius of a stream of tokens carrying bogus key ids.
pub const DEFAULT_MIN_JWKS_REFETCH: Duration = Duration::from_secs(60);

/// Discovery/JWKS HTTP timeout for external issuers. Matches the 30 s the
/// Python login client uses for OIDC discovery (spec 07 §12.2); Go relies on
/// the request context deadline.
const JWKS_HTTP_TIMEOUT: Duration = Duration::from_secs(30);

/// The signing algorithms an external OIDC issuer may use — the intersection
/// of `koidc.AllValidSigningAlgorithms()` (RS/ES/PS 256/384/512) with what
/// `jsonwebtoken` implements. `ES512` (P-521) is absent from `jsonwebtoken`
/// (documented divergence); `HS*`/`EdDSA`/`none` are not OIDC-valid.
const ALLOWED_OIDC_ALGS: &[Algorithm] = &[
    Algorithm::RS256,
    Algorithm::RS384,
    Algorithm::RS512,
    Algorithm::ES256,
    Algorithm::ES384,
    Algorithm::PS256,
    Algorithm::PS384,
    Algorithm::PS512,
];

/// Failure building a [`TokenValidator`] from configuration. All variants are
/// fatal at controller startup, mirroring Go's `LoadAuthenticationConfiguration`
/// returning an error to `main` (which then `os.Exit`s).
#[derive(Debug, Error)]
pub enum LoadError {
    /// A JWT authenticator uses a CEL expression. Unimplemented by design —
    /// the Rust controller supports claim+prefix claim mappings only (Phase-3
    /// plan: "fail fast at startup on CEL claim-mapping expressions").
    #[error(
        "JWT authenticator for issuer {issuer:?} uses a CEL expression at \
         {field}, which the Rust controller does not implement (only claim + \
         prefix claim mappings are supported)"
    )]
    CelUnsupported {
        /// The offending authenticator's `issuer.url`.
        issuer: String,
        /// The config path of the CEL expression, e.g.
        /// `claimMappings.username.expression`.
        field: &'static str,
    },
    /// `issuer.url` was empty. k8s requires a non-empty, unique issuer URL.
    #[error("JWT authenticator has an empty issuer.url")]
    MissingIssuerUrl,
    /// `issuer.audiences` was empty. k8s requires at least one audience.
    #[error("JWT authenticator for issuer {issuer:?} has no audiences")]
    MissingAudiences {
        /// The offending authenticator's `issuer.url`.
        issuer: String,
    },
    /// No `claimMappings.username.claim`. With CEL unavailable, a claim-based
    /// username mapping is mandatory.
    #[error(
        "JWT authenticator for issuer {issuer:?} has no claimMappings.username.claim \
         (a claim + prefix username mapping is required)"
    )]
    MissingUsernameClaim {
        /// The offending authenticator's `issuer.url`.
        issuer: String,
    },
    /// `issuer.certificateAuthority` was set but is neither a readable file nor
    /// parseable PEM. Go's `NewStaticCAContent` likewise errors at load
    /// (config/oidc.go:84-91).
    #[error(
        "JWT authenticator for issuer {issuer:?} has a certificateAuthority that is \
         neither a readable file nor valid PEM: {reason}"
    )]
    InvalidCa {
        /// The offending authenticator's `issuer.url`.
        issuer: String,
        /// The underlying parse error.
        reason: String,
    },
    /// The per-issuer `reqwest` client could not be constructed.
    #[error("failed to build the HTTP client for issuer {issuer:?}: {reason}")]
    HttpClient {
        /// The offending authenticator's `issuer.url`.
        issuer: String,
        /// The underlying builder error.
        reason: String,
    },
}

/// Token-validation failure taxonomy. The `Display` strings are chosen to
/// preserve the Go/gRPC-visible contracts: [`ValidationError::Expired`] is the
/// spec-07 §11.4 client re-auth trigger `"token is expired"`, and
/// [`ValidationError::NotAuthenticated`] is `VerifyOIDCToken`'s
/// `"failed to authenticate token"` (token.go:52-54).
#[derive(Debug, Error)]
pub enum ValidationError {
    /// No configured authenticator claimed the token's issuer (or the token
    /// was unparseable). The `!ok` branch of `VerifyOIDCToken`.
    #[error("failed to authenticate token")]
    NotAuthenticated,
    /// `exp` is in the past (or absent, for external issuers — go-oidc treats a
    /// missing expiry as the zero time, i.e. already expired). Contains the
    /// spec-pinned `"token is expired"` substring.
    #[error("token is expired")]
    Expired,
    /// `nbf` is in the future (validated only when present).
    #[error("token is not valid yet")]
    NotValidYet,
    /// `iss` missing or not the expected issuer.
    #[error("token has invalid issuer")]
    InvalidIssuer,
    /// `aud` missing or not intersecting the configured audiences.
    #[error("token has invalid audience")]
    InvalidAudience,
    /// A non-CEL `claimValidationRule` failed: the claim was absent or did not
    /// equal the required value.
    #[error("claim {claim:?} does not have the required value {required:?}")]
    ClaimValidation {
        /// The rule's `claim`.
        claim: String,
        /// The rule's `requiredValue`.
        required: String,
    },
    /// The configured username claim was missing or not a string.
    #[error("username claim {claim:?} is missing or not a string")]
    UsernameClaim {
        /// The configured `claimMappings.username.claim`.
        claim: String,
    },
    /// Signature/structure/algorithm verification failed.
    #[error("token verification failed: {0}")]
    Verification(String),
    /// The issuer's JWKS could not be fetched.
    #[error("failed to fetch JWKS for issuer {issuer}: {reason}")]
    Jwks {
        /// The issuer whose JWKS fetch failed.
        issuer: String,
        /// The underlying HTTP/decode error.
        reason: String,
    },
}

impl From<ValidateError> for ValidationError {
    /// Maps the internal [`Signer`] validation taxonomy onto this one so the
    /// internal and external paths surface identical errors (in particular the
    /// `"token is expired"` contract).
    fn from(err: ValidateError) -> Self {
        match err {
            ValidateError::Expired => ValidationError::Expired,
            ValidateError::NotValidYet => ValidationError::NotValidYet,
            ValidateError::InvalidIssuer => ValidationError::InvalidIssuer,
            ValidateError::InvalidAudience => ValidationError::InvalidAudience,
            ValidateError::Verification(e) => ValidationError::Verification(e.to_string()),
        }
    }
}

/// A successfully authenticated identity. The Go union yields
/// `authenticator.Response{User}`; downstream name resolution / authorization
/// (`internal/authorization`) consumes only the username, so that is all this
/// carries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Authenticated {
    /// The authenticated username: `prefix + <claim value>` (internal: `sub`;
    /// external: the configured username claim). This is what the k8s
    /// authenticator puts in `user.Info.GetName()`.
    pub username: String,
}

/// The union authenticator: a port of the `tokenunion.NewFailOnError` list that
/// `LoadAuthenticationConfiguration` builds, with the internal signer as the
/// last member.
#[derive(Debug)]
pub struct TokenValidator {
    /// External authenticators (config order) followed by the internal one,
    /// exactly like Go appends the internal entry to `config.JWT` before
    /// constructing the union.
    authenticators: Vec<Authenticator>,
    /// The resolved internal prefix, which `LoadAuthenticationConfiguration`
    /// also returns for later use by the authorizer
    /// (`prefix + InternalSubject()`).
    internal_prefix: String,
}

impl TokenValidator {
    /// Port of `LoadAuthenticationConfiguration` + `newJWTAuthenticator`
    /// (config/oidc.go:18-108): builds an external authenticator per
    /// `authentication.jwt[]` entry, then appends the in-process internal
    /// authenticator over `signer`. Returns [`LoadError`] on CEL usage or
    /// malformed external configuration.
    ///
    /// `signer` must already carry its configured token lifetime
    /// (`SetTokenLifetime`, which Go also does inside
    /// `LoadAuthenticationConfiguration`); that wiring lives with the signer /
    /// bootstrap and is intentionally out of this function's scope, since token
    /// lifetime affects only issuance, not validation.
    pub fn load(config: &Authentication, signer: Arc<Signer>) -> Result<Self, LoadError> {
        let internal_prefix = if config.internal.prefix.is_empty() {
            DEFAULT_INTERNAL_PREFIX.to_string()
        } else {
            config.internal.prefix.clone()
        };

        let mut authenticators = Vec::with_capacity(config.jwt.len() + 1);
        for jwt in &config.jwt {
            authenticators.push(Authenticator::External(Box::new(build_external(
                jwt,
                DEFAULT_MIN_JWKS_REFETCH,
            )?)));
        }
        // The internal authenticator is appended LAST (config/oidc.go:40-52).
        authenticators.push(Authenticator::Internal(InternalAuthenticator {
            signer,
            prefix: internal_prefix.clone(),
        }));

        Ok(Self {
            authenticators,
            internal_prefix,
        })
    }

    /// The resolved internal username prefix (the second return value of Go's
    /// `LoadAuthenticationConfiguration`).
    pub fn internal_prefix(&self) -> &str {
        &self.internal_prefix
    }

    /// Authenticates a bearer token, returning the authenticated username on
    /// success. Routes by unverified issuer to the matching authenticator; a
    /// token whose issuer matches no authenticator (or which cannot be parsed
    /// for an issuer) is [`ValidationError::NotAuthenticated`].
    pub async fn authenticate(&self, token: &str) -> Result<Authenticated, ValidationError> {
        self.authenticate_at(token, unix_now_secs_f64(), Instant::now())
            .await
    }

    /// [`Self::authenticate`] with explicit clocks: `now_unix` (seconds since
    /// the Unix epoch) drives claim validation; `now_instant` drives the JWKS
    /// refetch rate limiter. Split out for deterministic tests.
    async fn authenticate_at(
        &self,
        token: &str,
        now_unix: f64,
        now_instant: Instant,
    ) -> Result<Authenticated, ValidationError> {
        // hasCorrectIssuer: parse `iss` from the unverified payload and match
        // it against each authenticator, in order. First issuer match wins.
        let issuer = match unverified_string_claim(token, "iss") {
            Some(issuer) => issuer,
            None => return Err(ValidationError::NotAuthenticated),
        };
        for authenticator in &self.authenticators {
            if authenticator.issuer() == issuer {
                return authenticator
                    .authenticate(token, now_unix, now_instant)
                    .await;
            }
        }
        Err(ValidationError::NotAuthenticated)
    }
}

/// One member of the union. The external variant is boxed: it is far larger
/// than the internal one (a `reqwest::Client` + JWKS cache), and the members
/// live in a `Vec` where the per-element size would otherwise be dominated by
/// it.
#[derive(Debug)]
enum Authenticator {
    Internal(InternalAuthenticator),
    External(Box<ExternalAuthenticator>),
}

impl Authenticator {
    fn issuer(&self) -> &str {
        match self {
            Authenticator::Internal(a) => a.issuer(),
            Authenticator::External(a) => &a.issuer,
        }
    }

    async fn authenticate(
        &self,
        token: &str,
        now_unix: f64,
        now_instant: Instant,
    ) -> Result<Authenticated, ValidationError> {
        match self {
            Authenticator::Internal(a) => a.authenticate(token, now_unix),
            Authenticator::External(a) => a.authenticate(token, now_unix, now_instant).await,
        }
    }
}

/// The appended internal authenticator, validating in-process against the
/// signer key (approved divergence #1).
#[derive(Debug)]
struct InternalAuthenticator {
    signer: Arc<Signer>,
    prefix: String,
}

impl InternalAuthenticator {
    fn issuer(&self) -> &str {
        self.signer.issuer()
    }

    /// Validates issuer + audience + ES256 signature via [`Signer::validate_at`]
    /// (the exact golang-jwt-matched checks), then maps `sub` to
    /// `prefix + sub`. Because `validate_at` has already authenticated the
    /// token, reading `sub` from the unverified payload is safe.
    fn authenticate(&self, token: &str, now_unix: f64) -> Result<Authenticated, ValidationError> {
        self.signer.validate_at(token, now_unix)?;
        let sub = unverified_string_claim(token, "sub").ok_or_else(|| {
            ValidationError::UsernameClaim {
                claim: "sub".into(),
            }
        })?;
        Ok(Authenticated {
            username: format!("{}{}", self.prefix, sub),
        })
    }
}

/// An external OIDC issuer with JWKS-based verification.
#[derive(Debug)]
struct ExternalAuthenticator {
    issuer: String,
    audiences: Vec<String>,
    username_claim: String,
    username_prefix: String,
    /// Non-CEL `claimValidationRules` as `(claim, requiredValue)` pairs.
    claim_validation_rules: Vec<(String, String)>,
    /// Overrides the `{issuer}/.well-known/openid-configuration` discovery URL.
    discovery_url: Option<String>,
    http: reqwest::Client,
    cache: JwksCache,
}

impl ExternalAuthenticator {
    async fn authenticate(
        &self,
        token: &str,
        now_unix: f64,
        now_instant: Instant,
    ) -> Result<Authenticated, ValidationError> {
        let header =
            decode_header(token).map_err(|e| ValidationError::Verification(e.to_string()))?;
        if !ALLOWED_OIDC_ALGS.contains(&header.alg) {
            return Err(ValidationError::Verification(format!(
                "unsupported signing algorithm {:?}",
                header.alg
            )));
        }

        let keys = self
            .candidate_keys(header.kid.as_deref(), now_instant)
            .await?;
        if keys.is_empty() {
            return Err(ValidationError::Verification(
                "no JWKS key matches the token's kid".into(),
            ));
        }

        // Signature + algorithm checking delegated to jsonwebtoken; every
        // registered-claim check is manual below (Go-exact accept/reject
        // boundaries, zero leeway), same approach as the internal signer and
        // the router validator.
        let mut validation = Validation::new(header.alg);
        validation.algorithms = vec![header.alg];
        validation.validate_exp = false;
        validation.validate_nbf = false;
        validation.validate_aud = false;
        validation.required_spec_claims.clear();
        validation.leeway = 0;

        let claims = keys
            .iter()
            .find_map(|key| decode::<Value>(token, key, &validation).ok())
            .map(|data| data.claims)
            .ok_or_else(|| ValidationError::Verification("signature verification failed".into()))?;

        // exp: go-oidc requires a valid, future expiry (a missing `exp` decodes
        // to the zero time, which is "already expired").
        match claims.get("exp").and_then(Value::as_f64) {
            Some(exp) if now_unix < exp => {}
            _ => return Err(ValidationError::Expired),
        }
        // nbf: validated only when present.
        if let Some(nbf) = claims.get("nbf").and_then(Value::as_f64) {
            if now_unix < nbf {
                return Err(ValidationError::NotValidYet);
            }
        }
        // iss: defensive exact match (routing already matched the unverified
        // issuer; this rejects a token whose signed `iss` differs).
        match claims.get("iss").and_then(Value::as_str) {
            Some(iss) if iss == self.issuer => {}
            _ => return Err(ValidationError::InvalidIssuer),
        }
        // aud: at least one configured audience must appear (MatchAny).
        if !audience_matches(claims.get("aud"), &self.audiences) {
            return Err(ValidationError::InvalidAudience);
        }
        // claimValidationRules (non-CEL): required exact string value.
        for (claim, required) in &self.claim_validation_rules {
            match claims.get(claim).and_then(Value::as_str) {
                Some(value) if value == required => {}
                _ => {
                    return Err(ValidationError::ClaimValidation {
                        claim: claim.clone(),
                        required: required.clone(),
                    })
                }
            }
        }

        let raw = claims
            .get(&self.username_claim)
            .and_then(Value::as_str)
            .ok_or_else(|| ValidationError::UsernameClaim {
                claim: self.username_claim.clone(),
            })?;
        Ok(Authenticated {
            username: format!("{}{}", self.username_prefix, raw),
        })
    }

    /// Returns the JWKS keys eligible to verify a token with the given `kid`,
    /// fetching/refetching as needed. On a cache miss for `kid`, refetches only
    /// if at least [`JwksCache::min_refetch`] has elapsed since the last fetch
    /// (or nothing has ever been fetched).
    async fn candidate_keys(
        &self,
        kid: Option<&str>,
        now: Instant,
    ) -> Result<Vec<DecodingKey>, ValidationError> {
        let mut state = self.cache.state.lock().await;

        if let Some(jwks) = satisfied(&state, kid) {
            return Ok(build_keys(&jwks));
        }

        let should_refetch = state
            .last_fetch
            .is_none_or(|last| now.duration_since(last) >= self.cache.min_refetch);
        if should_refetch {
            self.fetch_jwks(&mut state, now).await?;
        }

        match satisfied(&state, kid) {
            Some(jwks) => Ok(build_keys(&jwks)),
            None => Ok(Vec::new()),
        }
    }

    /// Fetches the discovery document (once, to learn `jwks_uri`) and then the
    /// JWKS, updating `state`. Mirrors `<issuer>/.well-known/openid-configuration`
    /// → `jwks_uri` → GET jwks_uri.
    async fn fetch_jwks(&self, state: &mut JwksState, now: Instant) -> Result<(), ValidationError> {
        if state.jwks_uri.is_none() {
            let discovery_url = self.discovery_url.clone().unwrap_or_else(|| {
                format!(
                    "{}/.well-known/openid-configuration",
                    self.issuer.trim_end_matches('/')
                )
            });
            let discovery: DiscoveryDocument = self
                .http
                .get(&discovery_url)
                .send()
                .await
                .and_then(reqwest::Response::error_for_status)
                .map_err(|e| self.jwks_err(e))?
                .json()
                .await
                .map_err(|e| self.jwks_err(e))?;
            if discovery.jwks_uri.is_empty() {
                return Err(ValidationError::Jwks {
                    issuer: self.issuer.clone(),
                    reason: "discovery document has no jwks_uri".into(),
                });
            }
            state.jwks_uri = Some(discovery.jwks_uri);
        }

        let jwks_uri = state.jwks_uri.clone().expect("jwks_uri set above");
        let jwks: JwkSet = self
            .http
            .get(&jwks_uri)
            .send()
            .await
            .and_then(reqwest::Response::error_for_status)
            .map_err(|e| self.jwks_err(e))?
            .json()
            .await
            .map_err(|e| self.jwks_err(e))?;

        state.jwks = Some(jwks);
        state.last_fetch = Some(now);
        Ok(())
    }

    fn jwks_err(&self, err: reqwest::Error) -> ValidationError {
        ValidationError::Jwks {
            issuer: self.issuer.clone(),
            reason: err.to_string(),
        }
    }
}

/// The keys eligible to verify a token with `kid`, from the current cache, or
/// `None` if the cache cannot serve it (never fetched, or `kid` absent from the
/// fetched set).
fn satisfied(state: &JwksState, kid: Option<&str>) -> Option<Vec<jsonwebtoken::jwk::Jwk>> {
    let jwks = state.jwks.as_ref()?;
    match kid {
        Some(kid) => jwks.find(kid).map(|jwk| vec![jwk.clone()]),
        // Kid-less token: try every key (go-oidc verifies against all keys when
        // no kid is present).
        None => Some(jwks.keys.clone()),
    }
}

/// Builds decoding keys from candidate JWKs, silently dropping any the key
/// library cannot represent (e.g. an unsupported key type in a mixed set).
fn build_keys(jwks: &[jsonwebtoken::jwk::Jwk]) -> Vec<DecodingKey> {
    jwks.iter()
        .filter_map(|jwk| DecodingKey::from_jwk(jwk).ok())
        .collect()
}

/// Per-issuer JWKS cache with an unknown-`kid` refetch rate limiter.
#[derive(Debug)]
struct JwksCache {
    min_refetch: Duration,
    state: Mutex<JwksState>,
}

#[derive(Debug, Default)]
struct JwksState {
    /// Learned from discovery; fetched once, then reused for JWKS refetches.
    jwks_uri: Option<String>,
    jwks: Option<JwkSet>,
    last_fetch: Option<Instant>,
}

/// OIDC discovery document — only `jwks_uri` is consumed.
#[derive(Deserialize)]
struct DiscoveryDocument {
    #[serde(default)]
    jwks_uri: String,
}

/// Builds an [`ExternalAuthenticator`] from a config entry, rejecting CEL and
/// malformed configuration at load time.
fn build_external(
    jwt: &JwtAuthenticator,
    min_refetch: Duration,
) -> Result<ExternalAuthenticator, LoadError> {
    let issuer = jwt.issuer.url.clone();
    if issuer.is_empty() {
        return Err(LoadError::MissingIssuerUrl);
    }
    reject_cel(jwt, &issuer)?;
    if jwt.issuer.audiences.is_empty() {
        return Err(LoadError::MissingAudiences {
            issuer: issuer.clone(),
        });
    }

    let username_claim = jwt.claim_mappings.username.claim.clone();
    if username_claim.is_empty() {
        return Err(LoadError::MissingUsernameClaim {
            issuer: issuer.clone(),
        });
    }
    // `prefix` must be set when `claim` is; an unset prefix is treated as empty.
    let username_prefix = jwt
        .claim_mappings
        .username
        .prefix
        .clone()
        .unwrap_or_default();

    let claim_validation_rules = jwt
        .claim_validation_rules
        .iter()
        .map(|rule| (rule.claim.clone(), rule.required_value.clone()))
        .collect();

    let http = build_http_client(&jwt.issuer.certificate_authority, &issuer)?;

    Ok(ExternalAuthenticator {
        issuer,
        audiences: jwt.issuer.audiences.clone(),
        username_claim,
        username_prefix,
        claim_validation_rules,
        discovery_url: jwt.issuer.discovery_url.clone(),
        http,
        cache: JwksCache {
            min_refetch,
            state: Mutex::new(JwksState::default()),
        },
    })
}

/// Fails fast if any CEL expression is configured anywhere on the authenticator
/// (approved divergence #2).
fn reject_cel(jwt: &JwtAuthenticator, issuer: &str) -> Result<(), LoadError> {
    let mappings = &jwt.claim_mappings;
    let cel = |field: &'static str| {
        Err(LoadError::CelUnsupported {
            issuer: issuer.to_string(),
            field,
        })
    };
    if !mappings.username.expression.is_empty() {
        return cel("claimMappings.username.expression");
    }
    if !mappings.groups.expression.is_empty() {
        return cel("claimMappings.groups.expression");
    }
    if !mappings.uid.expression.is_empty() {
        return cel("claimMappings.uid.expression");
    }
    if mappings
        .extra
        .iter()
        .any(|e| !e.value_expression.is_empty())
    {
        return cel("claimMappings.extra[].valueExpression");
    }
    if jwt
        .claim_validation_rules
        .iter()
        .any(|r| !r.expression.is_empty())
    {
        return cel("claimValidationRules[].expression");
    }
    if jwt
        .user_validation_rules
        .iter()
        .any(|r| !r.expression.is_empty())
    {
        return cel("userValidationRules[].expression");
    }
    Ok(())
}

/// Builds the per-issuer `reqwest` client. When `ca` is non-empty it is treated
/// as a file path if one exists, else as inline PEM (Go's dual-purpose
/// `certificateAuthority`, config/oidc.go:75-88), and installed as the sole
/// trust root (`rustls-tls-manual-roots`).
fn build_http_client(ca: &str, issuer: &str) -> Result<reqwest::Client, LoadError> {
    install_default_crypto_provider();

    let mut builder = reqwest::Client::builder().timeout(JWKS_HTTP_TIMEOUT);
    if !ca.is_empty() {
        let pem = std::fs::read(ca).unwrap_or_else(|_| ca.as_bytes().to_vec());
        let certs =
            reqwest::Certificate::from_pem_bundle(&pem).map_err(|e| LoadError::InvalidCa {
                issuer: issuer.to_string(),
                reason: e.to_string(),
            })?;
        for cert in certs {
            builder = builder.add_root_certificate(cert);
        }
    }
    builder.build().map_err(|e| LoadError::HttpClient {
        issuer: issuer.to_string(),
        reason: e.to_string(),
    })
}

/// Installs the ring `rustls` `CryptoProvider` process-wide if none is set yet.
/// The controller binaries do this at startup; doing it here (idempotently)
/// keeps the module self-sufficient for tests and any embedder that forgets.
fn install_default_crypto_provider() {
    static ONCE: std::sync::Once = std::sync::Once::new();
    ONCE.call_once(|| {
        let _ = rustls::crypto::ring::default_provider().install_default();
    });
}

/// Returns `true` iff at least one configured audience appears in the token's
/// `aud` claim (a string or array of strings). MatchAny is the only supported
/// (and only k8s-valid, for our single-purpose audiences) policy.
fn audience_matches(aud: Option<&Value>, configured: &[String]) -> bool {
    let contains = |candidate: &str| configured.iter().any(|a| a == candidate);
    match aud {
        Some(Value::String(one)) => contains(one),
        Some(Value::Array(many)) => many.iter().filter_map(Value::as_str).any(contains),
        _ => false,
    }
}

/// Decodes the (unverified) JWT payload and extracts a string claim. Used for
/// issuer-based routing and internal-token `sub` extraction, mirroring the
/// `base64.RawURLEncoding` + `json.Unmarshal` of k8s' `hasCorrectIssuer`.
/// Returns `None` if the token is not three base64url segments, the payload is
/// not JSON, or the claim is absent / not a string.
fn unverified_string_claim(token: &str, claim: &str) -> Option<String> {
    let mut parts = token.split('.');
    let (_header, payload, _sig) = match (parts.next(), parts.next(), parts.next()) {
        (Some(header), Some(payload), Some(sig)) if parts.next().is_none() => {
            (header, payload, sig)
        }
        _ => return None,
    };
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(payload)
        .ok()?;
    let value: Value = serde_json::from_slice(&bytes).ok()?;
    value.get(claim)?.as_str().map(str::to_owned)
}

fn unix_now_secs_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::go_compat;
    use crate::signer::{INTERNAL_AUDIENCE, INTERNAL_ISSUER};
    use jsonwebtoken::{EncodingKey, Header};
    use jumpstarter_controller_config::jwt_authenticator::{
        ClaimMappings, ClaimValidationRule, Issuer, PrefixedClaimOrExpression,
    };
    use jumpstarter_controller_config::types::{Authentication, Internal};
    use p256::elliptic_curve::sec1::ToEncodedPoint;
    use p256::pkcs8::EncodePrivateKey;
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};

    const NOW: f64 = 1_750_000_000.0;

    // ---- internal-path helpers -------------------------------------------

    fn signer() -> Arc<Signer> {
        Arc::new(
            Signer::from_seed(b"validator-test-key", INTERNAL_ISSUER, INTERNAL_AUDIENCE)
                .expect("signer"),
        )
    }

    fn internal_validator(signer: Arc<Signer>) -> TokenValidator {
        TokenValidator::load(&Authentication::default(), signer).expect("load")
    }

    // ---- external-path helpers -------------------------------------------

    /// An EC P-256 signing key + the public JWK the stub JWKS serves for it.
    fn ec_key(seed: &[u8], kid: &str) -> (EncodingKey, Value) {
        let secret = go_compat::derive_key_from_seed(seed).expect("derive");
        let pkcs8 = secret
            .to_pkcs8_pem(p256::pkcs8::LineEnding::LF)
            .expect("pkcs8");
        let encoding = EncodingKey::from_ec_pem(pkcs8.as_bytes()).expect("encoding key");
        let point = secret.public_key().to_encoded_point(false);
        let engine = base64::engine::general_purpose::URL_SAFE_NO_PAD;
        let jwk = json!({
            "kty": "EC",
            "crv": "P-256",
            "alg": "ES256",
            "use": "sig",
            "kid": kid,
            "x": engine.encode(point.x().expect("x")),
            "y": engine.encode(point.y().expect("y")),
        });
        (encoding, jwk)
    }

    fn mint_es256(encoding: &EncodingKey, kid: &str, claims: &Value) -> String {
        let mut header = Header::new(Algorithm::ES256);
        header.kid = Some(kid.to_string());
        jsonwebtoken::encode(&header, claims, encoding).expect("encode")
    }

    /// A local axum JWKS/discovery stub bound on port 0. Counts endpoint hits
    /// and lets the served JWKS be swapped to simulate key rotation.
    #[derive(Clone)]
    struct Stub {
        issuer: String,
        jwks: Arc<std::sync::Mutex<Value>>,
        discovery_hits: Arc<AtomicUsize>,
        jwks_hits: Arc<AtomicUsize>,
    }

    async fn spawn_stub(initial_jwks: Value) -> Stub {
        use axum::extract::State;
        use axum::routing::get;
        use axum::{Json, Router};

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind");
        let base = format!("http://{}", listener.local_addr().expect("addr"));
        let stub = Stub {
            issuer: base.clone(),
            jwks: Arc::new(std::sync::Mutex::new(initial_jwks)),
            discovery_hits: Arc::new(AtomicUsize::new(0)),
            jwks_hits: Arc::new(AtomicUsize::new(0)),
        };

        async fn discovery(State(stub): State<Stub>) -> Json<Value> {
            stub.discovery_hits.fetch_add(1, Ordering::SeqCst);
            Json(json!({
                "issuer": stub.issuer,
                "jwks_uri": format!("{}/jwks", stub.issuer),
            }))
        }
        async fn jwks(State(stub): State<Stub>) -> Json<Value> {
            stub.jwks_hits.fetch_add(1, Ordering::SeqCst);
            Json(stub.jwks.lock().unwrap().clone())
        }

        let app = Router::new()
            .route("/.well-known/openid-configuration", get(discovery))
            .route("/jwks", get(jwks))
            .with_state(stub.clone());
        tokio::spawn(async move {
            axum::serve(listener, app).await.expect("serve");
        });
        stub
    }

    fn external(
        issuer: &str,
        audiences: &[&str],
        claim: &str,
        prefix: &str,
        min: Duration,
    ) -> ExternalAuthenticator {
        ExternalAuthenticator {
            issuer: issuer.to_string(),
            audiences: audiences.iter().map(|s| s.to_string()).collect(),
            username_claim: claim.to_string(),
            username_prefix: prefix.to_string(),
            claim_validation_rules: Vec::new(),
            discovery_url: None,
            http: build_http_client("", issuer).expect("http client"),
            cache: JwksCache {
                min_refetch: min,
                state: Mutex::new(JwksState::default()),
            },
        }
    }

    // ---- internal verification -------------------------------------------

    #[tokio::test]
    async fn internal_token_verifies_with_prefixed_subject() {
        let signer = signer();
        let validator = internal_validator(signer.clone());
        let token = signer
            .token_at("client:default:sample:uid-1", NOW as i64)
            .unwrap();

        let authed = validator
            .authenticate_at(&token, NOW, Instant::now())
            .await
            .unwrap();
        // Default internal prefix + sub.
        assert_eq!(authed.username, "internal:client:default:sample:uid-1");
        assert_eq!(validator.internal_prefix(), "internal:");
    }

    #[tokio::test]
    async fn internal_configurable_prefix_is_applied() {
        let signer = signer();
        let config = Authentication {
            internal: Internal {
                prefix: "corp:".into(),
                ..Default::default()
            },
            ..Default::default()
        };
        let validator = TokenValidator::load(&config, signer.clone()).unwrap();
        let token = signer.token_at("exporter:ns:e:uid", NOW as i64).unwrap();

        let authed = validator
            .authenticate_at(&token, NOW, Instant::now())
            .await
            .unwrap();
        assert_eq!(authed.username, "corp:exporter:ns:e:uid");
        assert_eq!(validator.internal_prefix(), "corp:");
    }

    #[tokio::test]
    async fn internal_expired_error_contains_pinned_substring() {
        let mut signer =
            Signer::from_seed(b"validator-test-key", INTERNAL_ISSUER, INTERNAL_AUDIENCE).unwrap();
        signer.set_token_lifetime(Duration::from_secs(60));
        let signer = Arc::new(signer);
        let validator = internal_validator(signer.clone());
        let token = signer.token_at("client:default:s:u", NOW as i64).unwrap();

        let err = validator
            .authenticate_at(&token, NOW + 120.0, Instant::now())
            .await
            .unwrap_err();
        assert!(matches!(err, ValidationError::Expired));
        // spec 07 §11.4: the client re-auths on this exact substring.
        assert!(err.to_string().contains("token is expired"), "got: {err}");
    }

    #[tokio::test]
    async fn unknown_issuer_is_not_authenticated() {
        let signer = signer();
        let validator = internal_validator(signer);
        // A syntactically valid JWT whose issuer matches no authenticator.
        let (encoding, _jwk) = ec_key(b"stranger", "k");
        let token = mint_es256(
            &encoding,
            "k",
            &json!({"iss": "https://stranger.example.com", "sub": "s", "aud": ["x"], "exp": NOW + 60.0}),
        );
        let err = validator
            .authenticate_at(&token, NOW, Instant::now())
            .await
            .unwrap_err();
        assert!(matches!(err, ValidationError::NotAuthenticated));
        assert_eq!(err.to_string(), "failed to authenticate token");
    }

    #[tokio::test]
    async fn garbage_token_is_not_authenticated() {
        let validator = internal_validator(signer());
        for token in ["not-a-jwt", "a.b", "a.b.c.d", "..", ""] {
            let err = validator
                .authenticate_at(token, NOW, Instant::now())
                .await
                .unwrap_err();
            assert!(
                matches!(err, ValidationError::NotAuthenticated),
                "token {token:?}"
            );
        }
    }

    // ---- external verification -------------------------------------------

    fn ext_claims(issuer: &str, name: &str) -> Value {
        json!({
            "iss": issuer,
            "sub": "subject-123",
            "name": name,
            "aud": ["jumpstarter-cli"],
            "exp": NOW + 3600.0,
            "iat": NOW,
        })
    }

    #[tokio::test]
    async fn external_token_verifies_and_applies_prefix() {
        let (encoding, jwk) = ec_key(b"ext-key", "ext-kid-1");
        let stub = spawn_stub(json!({"keys": [jwk]})).await;
        let authn = external(
            &stub.issuer,
            &["jumpstarter-cli"],
            "name",
            "dex:",
            DEFAULT_MIN_JWKS_REFETCH,
        );

        let token = mint_es256(&encoding, "ext-kid-1", &ext_claims(&stub.issuer, "alice"));
        let authed = authn
            .authenticate(&token, NOW, Instant::now())
            .await
            .unwrap();
        assert_eq!(authed.username, "dex:alice");

        // Discovery once, JWKS once; a second auth for the same kid is cached.
        assert_eq!(stub.discovery_hits.load(Ordering::SeqCst), 1);
        assert_eq!(stub.jwks_hits.load(Ordering::SeqCst), 1);
        let again = mint_es256(&encoding, "ext-kid-1", &ext_claims(&stub.issuer, "bob"));
        assert_eq!(
            authn
                .authenticate(&again, NOW, Instant::now())
                .await
                .unwrap()
                .username,
            "dex:bob"
        );
        assert_eq!(
            stub.jwks_hits.load(Ordering::SeqCst),
            1,
            "cache hit, no refetch"
        );
        assert_eq!(stub.discovery_hits.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn external_expired_error_contains_pinned_substring() {
        let (encoding, jwk) = ec_key(b"ext-key", "ext-kid-1");
        let stub = spawn_stub(json!({"keys": [jwk]})).await;
        let authn = external(
            &stub.issuer,
            &["jumpstarter-cli"],
            "name",
            "dex:",
            DEFAULT_MIN_JWKS_REFETCH,
        );

        let mut claims = ext_claims(&stub.issuer, "alice");
        claims["exp"] = json!(NOW);
        let token = mint_es256(&encoding, "ext-kid-1", &claims);
        let err = authn
            .authenticate(&token, NOW, Instant::now())
            .await
            .unwrap_err();
        assert!(matches!(err, ValidationError::Expired));
        assert!(err.to_string().contains("token is expired"), "got: {err}");
    }

    #[tokio::test]
    async fn external_wrong_audience_is_rejected() {
        let (encoding, jwk) = ec_key(b"ext-key", "ext-kid-1");
        let stub = spawn_stub(json!({"keys": [jwk]})).await;
        let authn = external(
            &stub.issuer,
            &["jumpstarter-cli"],
            "name",
            "dex:",
            DEFAULT_MIN_JWKS_REFETCH,
        );

        let mut claims = ext_claims(&stub.issuer, "alice");
        claims["aud"] = json!(["someone-else"]);
        let token = mint_es256(&encoding, "ext-kid-1", &claims);
        let err = authn
            .authenticate(&token, NOW, Instant::now())
            .await
            .unwrap_err();
        assert!(
            matches!(err, ValidationError::InvalidAudience),
            "got: {err}"
        );
    }

    #[tokio::test]
    async fn external_wrong_signature_is_verification_failure() {
        // JWKS advertises the key for `good`, but the token is signed by `evil`
        // under the same kid: the issuer matches, so this is a hard verification
        // failure (fail-on-error), not "not authenticated".
        let (_good_enc, good_jwk) = ec_key(b"good-key", "ext-kid-1");
        let (evil_enc, _evil_jwk) = ec_key(b"evil-key", "ext-kid-1");
        let stub = spawn_stub(json!({"keys": [good_jwk]})).await;
        let authn = external(
            &stub.issuer,
            &["jumpstarter-cli"],
            "name",
            "dex:",
            DEFAULT_MIN_JWKS_REFETCH,
        );

        let token = mint_es256(&evil_enc, "ext-kid-1", &ext_claims(&stub.issuer, "alice"));
        let err = authn
            .authenticate(&token, NOW, Instant::now())
            .await
            .unwrap_err();
        assert!(
            matches!(err, ValidationError::Verification(_)),
            "got: {err}"
        );
    }

    #[tokio::test]
    async fn external_claim_validation_rule_enforced() {
        let (encoding, jwk) = ec_key(b"ext-key", "ext-kid-1");
        let stub = spawn_stub(json!({"keys": [jwk]})).await;
        let mut authn = external(
            &stub.issuer,
            &["jumpstarter-cli"],
            "name",
            "dex:",
            DEFAULT_MIN_JWKS_REFETCH,
        );
        authn.claim_validation_rules = vec![("hd".to_string(), "example.com".to_string())];

        // Missing required claim -> rejected.
        let token = mint_es256(&encoding, "ext-kid-1", &ext_claims(&stub.issuer, "alice"));
        assert!(matches!(
            authn
                .authenticate(&token, NOW, Instant::now())
                .await
                .unwrap_err(),
            ValidationError::ClaimValidation { .. }
        ));

        // Correct value -> accepted.
        let mut claims = ext_claims(&stub.issuer, "alice");
        claims["hd"] = json!("example.com");
        let token = mint_es256(&encoding, "ext-kid-1", &claims);
        assert_eq!(
            authn
                .authenticate(&token, NOW, Instant::now())
                .await
                .unwrap()
                .username,
            "dex:alice"
        );
    }

    #[tokio::test]
    async fn external_missing_username_claim_is_rejected() {
        let (encoding, jwk) = ec_key(b"ext-key", "ext-kid-1");
        let stub = spawn_stub(json!({"keys": [jwk]})).await;
        let authn = external(
            &stub.issuer,
            &["jumpstarter-cli"],
            "email",
            "dex:",
            DEFAULT_MIN_JWKS_REFETCH,
        );

        // Token has `name`, not the configured `email` claim.
        let token = mint_es256(&encoding, "ext-kid-1", &ext_claims(&stub.issuer, "alice"));
        assert!(matches!(
            authn
                .authenticate(&token, NOW, Instant::now())
                .await
                .unwrap_err(),
            ValidationError::UsernameClaim { .. }
        ));
    }

    // ---- JWKS cache / refetch behavior -----------------------------------

    #[tokio::test]
    async fn unknown_kid_refetches_when_interval_allows() {
        let (enc_a, jwk_a) = ec_key(b"key-a", "kid-a");
        let stub = spawn_stub(json!({"keys": [jwk_a]})).await;
        // min_refetch = 0: an unknown kid always triggers a refetch.
        let authn = external(
            &stub.issuer,
            &["jumpstarter-cli"],
            "name",
            "dex:",
            Duration::ZERO,
        );

        // First token (kid-a) populates the cache.
        let token_a = mint_es256(&enc_a, "kid-a", &ext_claims(&stub.issuer, "alice"));
        assert!(authn
            .authenticate(&token_a, NOW, Instant::now())
            .await
            .is_ok());
        assert_eq!(stub.jwks_hits.load(Ordering::SeqCst), 1);

        // Rotate the JWKS to a new kid, then present a token signed by it.
        let (enc_b, jwk_b) = ec_key(b"key-b", "kid-b");
        *stub.jwks.lock().unwrap() = json!({"keys": [jwk_b]});
        let token_b = mint_es256(&enc_b, "kid-b", &ext_claims(&stub.issuer, "carol"));
        let authed = authn
            .authenticate(&token_b, NOW, Instant::now())
            .await
            .unwrap();
        assert_eq!(authed.username, "dex:carol");
        // Unknown kid-b forced a refetch (discovery stays cached: 1).
        assert_eq!(stub.jwks_hits.load(Ordering::SeqCst), 2);
        assert_eq!(stub.discovery_hits.load(Ordering::SeqCst), 1);
    }

    #[tokio::test]
    async fn unknown_kid_respects_min_refetch_interval() {
        let (enc_a, jwk_a) = ec_key(b"key-a", "kid-a");
        let stub = spawn_stub(json!({"keys": [jwk_a]})).await;
        // Large min_refetch: after the first fetch, an unknown kid must NOT
        // refetch within the interval.
        let authn = external(
            &stub.issuer,
            &["jumpstarter-cli"],
            "name",
            "dex:",
            Duration::from_secs(3600),
        );

        let token_a = mint_es256(&enc_a, "kid-a", &ext_claims(&stub.issuer, "alice"));
        assert!(authn
            .authenticate(&token_a, NOW, Instant::now())
            .await
            .is_ok());
        assert_eq!(stub.jwks_hits.load(Ordering::SeqCst), 1);

        // Rotate, present a new-kid token immediately: the guard suppresses the
        // refetch, so the key is unknown and verification fails.
        let (enc_b, jwk_b) = ec_key(b"key-b", "kid-b");
        *stub.jwks.lock().unwrap() = json!({"keys": [jwk_b]});
        let token_b = mint_es256(&enc_b, "kid-b", &ext_claims(&stub.issuer, "carol"));
        let err = authn
            .authenticate(&token_b, NOW, Instant::now())
            .await
            .unwrap_err();
        assert!(
            matches!(err, ValidationError::Verification(_)),
            "got: {err}"
        );
        assert_eq!(
            stub.jwks_hits.load(Ordering::SeqCst),
            1,
            "min interval suppressed refetch"
        );
    }

    #[test]
    fn refetch_gate_is_time_bounded() {
        // Pure check of the rate-limit predicate the cache uses.
        let min = Duration::from_secs(60);
        let now = Instant::now();
        let recent = now.checked_sub(Duration::from_secs(10)).unwrap();
        let old = now.checked_sub(Duration::from_secs(120)).unwrap();

        // The exact predicate `candidate_keys` uses to gate an unknown-kid refetch.
        let should_refetch =
            |last: Option<Instant>| last.is_none_or(|t| now.duration_since(t) >= min);
        assert!(should_refetch(None), "never fetched -> refetch");
        assert!(!should_refetch(Some(recent)), "within interval -> suppress");
        assert!(should_refetch(Some(old)), "interval elapsed -> refetch");
    }

    // ---- union routing ----------------------------------------------------

    #[tokio::test]
    async fn union_routes_by_issuer_internal_last() {
        let (encoding, jwk) = ec_key(b"ext-key", "ext-kid-1");
        let stub = spawn_stub(json!({"keys": [jwk]})).await;

        let signer = signer();
        let config = Authentication {
            jwt: vec![JwtAuthenticator {
                issuer: Issuer {
                    url: stub.issuer.clone(),
                    audiences: vec!["jumpstarter-cli".into()],
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
        };
        let validator = TokenValidator::load(&config, signer.clone()).unwrap();

        // External token routes to the external authenticator.
        let ext = mint_es256(&encoding, "ext-kid-1", &ext_claims(&stub.issuer, "alice"));
        assert_eq!(
            validator
                .authenticate_at(&ext, NOW, Instant::now())
                .await
                .unwrap()
                .username,
            "dex:alice"
        );

        // Internal token (issued by the signer) routes to the internal member.
        let internal = signer.token_at("client:ns:name:uid", NOW as i64).unwrap();
        assert_eq!(
            validator
                .authenticate_at(&internal, NOW, Instant::now())
                .await
                .unwrap()
                .username,
            "internal:client:ns:name:uid"
        );

        // A token whose issuer matches neither is not authenticated.
        let (stranger_enc, _j) = ec_key(b"stranger", "k");
        let stranger = mint_es256(
            &stranger_enc,
            "k",
            &json!({"iss": "https://nope.example", "name": "x", "aud": ["jumpstarter-cli"], "exp": NOW + 60.0}),
        );
        assert!(matches!(
            validator
                .authenticate_at(&stranger, NOW, Instant::now())
                .await
                .unwrap_err(),
            ValidationError::NotAuthenticated
        ));
    }

    // ---- CEL fail-fast at load -------------------------------------------

    fn base_external_config(expr_field: impl FnOnce(&mut JwtAuthenticator)) -> Authentication {
        let mut jwt = JwtAuthenticator {
            issuer: Issuer {
                url: "https://issuer.example.com".into(),
                audiences: vec!["jumpstarter-cli".into()],
                ..Default::default()
            },
            claim_mappings: ClaimMappings {
                username: PrefixedClaimOrExpression {
                    claim: "sub".into(),
                    prefix: Some("oidc:".into()),
                    ..Default::default()
                },
                ..Default::default()
            },
            ..Default::default()
        };
        expr_field(&mut jwt);
        Authentication {
            jwt: vec![jwt],
            ..Default::default()
        }
    }

    #[test]
    fn cel_username_expression_fails_at_load() {
        let config = base_external_config(|jwt| {
            jwt.claim_mappings.username = PrefixedClaimOrExpression {
                expression: "claims.sub".into(),
                ..Default::default()
            };
        });
        let err = TokenValidator::load(&config, signer()).unwrap_err();
        assert!(
            matches!(&err, LoadError::CelUnsupported { field, .. } if *field == "claimMappings.username.expression"),
            "got: {err}"
        );
    }

    #[test]
    fn cel_claim_validation_rule_expression_fails_at_load() {
        let config = base_external_config(|jwt| {
            jwt.claim_validation_rules = vec![ClaimValidationRule {
                expression: "claims.hd == 'example.com'".into(),
                ..Default::default()
            }];
        });
        let err = TokenValidator::load(&config, signer()).unwrap_err();
        assert!(
            matches!(&err, LoadError::CelUnsupported { field, .. } if *field == "claimValidationRules[].expression"),
            "got: {err}"
        );
    }

    #[test]
    fn missing_username_claim_fails_at_load() {
        let config = base_external_config(|jwt| {
            jwt.claim_mappings.username = PrefixedClaimOrExpression::default();
        });
        assert!(matches!(
            TokenValidator::load(&config, signer()).unwrap_err(),
            LoadError::MissingUsernameClaim { .. }
        ));
    }

    #[test]
    fn empty_audiences_fails_at_load() {
        let config = base_external_config(|jwt| {
            jwt.issuer.audiences.clear();
        });
        assert!(matches!(
            TokenValidator::load(&config, signer()).unwrap_err(),
            LoadError::MissingAudiences { .. }
        ));
    }

    #[test]
    fn invalid_inline_ca_fails_at_load() {
        let config = base_external_config(|jwt| {
            jwt.issuer.certificate_authority =
                "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----".into();
        });
        // A bad CA fails fast at startup (like Go's NewStaticCAContent). reqwest
        // rejects it either while parsing the PEM (InvalidCa) or while building
        // the rustls root store from the decoded-but-unparseable DER
        // (HttpClient); both are load-time failures.
        let err = TokenValidator::load(&config, signer()).unwrap_err();
        assert!(
            matches!(
                err,
                LoadError::InvalidCa { .. } | LoadError::HttpClient { .. }
            ),
            "got: {err}"
        );
    }

    #[test]
    fn default_config_builds_internal_only() {
        // No external issuers: just the appended internal authenticator.
        let validator = TokenValidator::load(&Authentication::default(), signer()).unwrap();
        assert_eq!(validator.authenticators.len(), 1);
        assert_eq!(validator.internal_prefix(), "internal:");
    }
}
