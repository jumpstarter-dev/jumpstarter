//! The controller gRPC **error-string contract** — byte-identical `(code,
//! message)` pairs matched by deployed Python/Rust/Java clients (spec 02 §12,
//! and the plan's consolidated error table). This is the single most
//! field-critical parity surface: a drifted code silently stops a client
//! retrying, misclassifies a lease transfer, or never triggers re-auth.
//!
//! Every constructor here returns a [`tonic::Status`] whose code and message
//! reproduce a specific Go call site (each carries a `// go:` provenance
//! comment). The table test at the bottom pins every string.
//!
//! ## The three load-bearing warts (spec 02 §12.2)
//!
//! 1. **Plain `fmt.Errorf` → `UNKNOWN`.** Several Go paths return a bare
//!    `fmt.Errorf`, which grpc-go surfaces as code `UNKNOWN` with the raw
//!    string as the details. Clients therefore match on the *details string*
//!    — e.g. `"permission denied"` means "lease transferred" (lease.py) — so
//!    the message must be verbatim and the code must stay `UNKNOWN`, **not** a
//!    "correct" `PERMISSION_DENIED`. See [`permission_denied_transferred`],
//!    [`lease_not_active`], [`empty_lease_name`], [`no_router_available`].
//! 2. **Raw apiserver errors forwarded verbatim as `UNKNOWN`.** The Dial/Listen
//!    lease/exporter `Get`s propagate the kube error unmapped, so a lookup of a
//!    nonexistent Lease/Exporter surfaces as `UNKNOWN` with the raw
//!    `"…not found"` text — **never** `NOT_FOUND`. See
//!    [`forward_apiserver_error`]. The code-for-code [`k8s_to_grpc_code`]
//!    mapping (`token.go:19-40`) applies **only** to the auth-path lookups
//!    ([`client_lookup_error`]/[`exporter_lookup_error`]).
//! 3. **`"token is expired"` substring preserved.** Expired-token auth failures
//!    surface as `UNKNOWN` and clients key re-auth off the
//!    [`TOKEN_EXPIRED_SUBSTR`] substring; [`preserve_plain_error`] never
//!    rewrites the text. (The auth-crate validator is responsible for
//!    *producing* that substring — see the concern noted in the tests.)
//!
//! `%q` operands are quoted with [`go_quote`] (the faithful `strconv.Quote`
//! port already living in the auth crate), not Rust's `{:?}` — the two diverge
//! on non-ASCII (proven in [`tests::go_quote_beats_debug_on_unicode`]).

use tonic::{Code, Status};

pub use jumpstarter_controller_auth::authorize::{k8s_to_grpc_code, StoreError, StoreErrorKind};
pub use jumpstarter_controller_auth::normalize::go_quote;

/// Substring clients match to trigger token re-authentication. Emitted by the
/// auth-crate validator on an expired token and forwarded verbatim on the
/// auth path (`bearer`/`VerifyOIDCToken` → `UNKNOWN`); [`preserve_plain_error`]
/// must not rewrite it.
pub const TOKEN_EXPIRED_SUBSTR: &str = "token is expired";

// ===========================================================================
// Dial / Listen — status gate (FAILED_PRECONDITION)
// ===========================================================================
// The Dial-retry contract: clients key retry off the literal substring
// `"not ready"` in the details (lease.py:330). Both minted messages below
// contain it (`controller_service.go:498-519`).

/// `checkExporterStatusForDriverCalls`, `Available` arm
/// (`controller_service.go:511`). The transient lease-setup state that Dial
/// retries server-side. Message intentionally contains `"not ready"`.
// go: internal/service/controller_service.go:511
#[must_use]
pub fn exporter_not_ready_available() -> Status {
    Status::new(
        Code::FailedPrecondition,
        "exporter is not ready (status: Available)",
    )
}

/// `checkExporterStatusForDriverCalls`, `default` arm
/// (`controller_service.go:517`). Message contains `"not ready"`.
// go: internal/service/controller_service.go:517
#[must_use]
pub fn exporter_not_ready(status: &str) -> Status {
    Status::new(
        Code::FailedPrecondition,
        format!("exporter not ready (status: {status})"),
    )
}

/// `checkExporterStatusForDriverCalls`, `Offline` arm
/// (`controller_service.go:509`).
// go: internal/service/controller_service.go:509
#[must_use]
pub fn exporter_offline() -> Status {
    Status::new(Code::FailedPrecondition, "exporter is offline")
}

/// `checkExporterStatusForDriverCalls`, `BeforeLeaseHookFailed` arm
/// (`controller_service.go:513`).
// go: internal/service/controller_service.go:513
#[must_use]
pub fn exporter_before_lease_hook_failed() -> Status {
    Status::new(Code::FailedPrecondition, "exporter beforeLease hook failed")
}

/// `checkExporterStatusForDriverCalls`, `AfterLeaseHookFailed` arm
/// (`controller_service.go:515`).
// go: internal/service/controller_service.go:515
#[must_use]
pub fn exporter_after_lease_hook_failed() -> Status {
    Status::new(Code::FailedPrecondition, "exporter afterLease hook failed")
}

// ===========================================================================
// Dial / sendToListener — delivery (UNAVAILABLE / RESOURCE_EXHAUSTED)
// ===========================================================================

/// `sendToListener`: no active listener, or its queue was superseded
/// (`controller_service.go:178, 184`). Retried by clients.
// go: internal/service/controller_service.go:178,184
#[must_use]
pub fn exporter_not_listening(lease_name: &str) -> Status {
    Status::new(
        Code::Unavailable,
        format!("exporter is not listening on lease {lease_name}"),
    )
}

/// `sendToListener`: the listener's 8-slot buffer is full
/// (`controller_service.go:193`).
// go: internal/service/controller_service.go:193
#[must_use]
pub fn listener_buffer_full(lease_name: &str) -> Status {
    Status::new(
        Code::ResourceExhausted,
        format!("listener buffer full on lease {lease_name}"),
    )
}

// ===========================================================================
// Dial — plain fmt.Errorf → UNKNOWN (spec 02 §12.2 wart #1)
// ===========================================================================

/// Dial ownership check: the lease is not held by the authenticated client
/// (`controller_service.go:783`). A plain `fmt.Errorf("permission denied")` →
/// code `UNKNOWN`; clients match the exact string `"permission denied"` to
/// mean **the lease was transferred** (lease.py:369-374). Must NOT be a proper
/// `PERMISSION_DENIED`.
// go: internal/service/controller_service.go:782-784
#[must_use]
pub fn permission_denied_transferred() -> Status {
    Status::new(Code::Unknown, "permission denied")
}

/// Dial: `status.exporterRef` is nil (`controller_service.go:789`). Plain
/// error → `UNKNOWN`.
// go: internal/service/controller_service.go:788-790
#[must_use]
pub fn lease_not_active() -> Status {
    Status::new(Code::Unknown, "lease not active")
}

/// Dial: empty `lease_name` in the request (`controller_service.go:760`).
/// Plain error → `UNKNOWN`.
// go: internal/service/controller_service.go:759-761
#[must_use]
pub fn empty_lease_name() -> Status {
    Status::new(Code::Unknown, "empty lease name")
}

/// Dial: the router config is empty, no candidates (`controller_service.go:857`).
/// Plain error → `UNKNOWN`.
// go: internal/service/controller_service.go:856-858
#[must_use]
pub fn no_router_available() -> Status {
    Status::new(Code::Unknown, "no router available")
}

/// Dial: HS256 signing failed (`controller_service.go:877`). Effectively
/// unreachable over an in-memory key, but preserved for parity — this one *is*
/// a proper `status.Errorf(codes.Internal, ...)`.
// go: internal/service/controller_service.go:876-878
#[must_use]
pub fn unable_to_sign_token() -> Status {
    Status::new(Code::Internal, "unable to sign token")
}

// ===========================================================================
// Auth headers / metadata (bearer.go, metadata.go)
// ===========================================================================

/// `bearer.go:41` — no `authorization` header. The only `UNAUTHENTICATED` in
/// the set.
// go: internal/authentication/bearer.go:41
#[must_use]
pub fn missing_authorization_header() -> Status {
    Status::new(Code::Unauthenticated, "missing authorization header")
}

/// `bearer.go:35` / `metadata.go:121` — no incoming metadata at all.
// go: internal/authentication/bearer.go:35, internal/authorization/metadata.go:121
#[must_use]
pub fn missing_metadata() -> Status {
    Status::new(Code::InvalidArgument, "missing metadata")
}

/// `bearer.go:47` — more than one `authorization` header.
// go: internal/authentication/bearer.go:47
#[must_use]
pub fn multiple_authorization_headers() -> Status {
    Status::new(Code::InvalidArgument, "multiple authorization headers")
}

/// `bearer.go:55` — an `authorization` header that is not `Bearer <token>`.
// go: internal/authentication/bearer.go:55
#[must_use]
pub fn malformed_authorization_header() -> Status {
    Status::new(Code::InvalidArgument, "malformed authorization header")
}

/// `mdGet` (`metadata.go:181`) — a required `jumpstarter-*` metadata key is
/// absent.
// go: internal/authorization/metadata.go:181
#[must_use]
pub fn missing_metadata_key(key: &str) -> Status {
    Status::new(Code::InvalidArgument, format!("missing metadata: {key}"))
}

/// `mdGet` (`metadata.go:184`) — a `jumpstarter-*` metadata key appears more
/// than once.
// go: internal/authorization/metadata.go:184
#[must_use]
pub fn multiple_metadata_key(key: &str) -> Status {
    Status::new(Code::InvalidArgument, format!("multiple metadata: {key}"))
}

/// `metadata.go:148` — internal/service-account identity with no resource name
/// supplied.
// go: internal/authorization/metadata.go:147-149
#[must_use]
pub fn resource_name_required() -> Status {
    Status::new(
        Code::InvalidArgument,
        "resource name required for pre-existing authentication",
    )
}

/// `metadata.go:158-164` — a provided resource name that does not match the
/// name derived from the OIDC username. All three operands are Go `%q`-quoted
/// (they may carry arbitrary Unicode), so [`go_quote`] — not `{:?}` — is used.
// go: internal/authorization/metadata.go:157-165
#[must_use]
pub fn resource_name_mismatch(provided: &str, expected: &str, oidc_username: &str) -> Status {
    Status::new(
        Code::InvalidArgument,
        format!(
            "resource name mismatch: provided {} but expected {} (derived from OIDC username {})",
            go_quote(provided),
            go_quote(expected),
            go_quote(oidc_username),
        ),
    )
}

/// `VerifyClientObjectToken`/`VerifyExporterObjectToken` kind check
/// (`token.go:71-73, 110-112`) — the authenticated object is not the expected
/// kind.
// go: internal/oidc/token.go:71-73, 110-112
#[must_use]
pub fn object_kind_mismatch() -> Status {
    Status::new(Code::InvalidArgument, "object kind mismatch")
}

/// `CreateLease` validation (`client_service.go:266`) — neither a selector nor
/// an exporter name was supplied.
// go: internal/service/client/v1/client_service.go:266
#[must_use]
pub fn one_of_selector_or_exporter_name_required() -> Status {
    Status::new(
        Code::InvalidArgument,
        "one of selector or exporter_name is required",
    )
}

// ---- AIP resource-identifier parsing (utils/identifier.go) ---------------
// Note: the identifier is wrapped in **literal** double quotes (`\"%s\"`) in
// the Go format string — this is not `%q`, so no escaping is applied.

/// `ParseObjectIdentifier`/`ParseNamespaceIdentifier` segment-count check
/// (`identifier.go:15-21, 39-45`).
// go: internal/service/utils/identifier.go:15-21, 39-45
#[must_use]
pub fn invalid_segment_count(identifier: &str, expecting: usize, got: usize) -> Status {
    Status::new(
        Code::InvalidArgument,
        format!(
            "invalid number of segments in identifier \"{identifier}\", expecting {expecting}, got {got}"
        ),
    )
}

/// First-segment check (`identifier.go:24-30, 48-54`).
// go: internal/service/utils/identifier.go:24-30, 48-54
#[must_use]
pub fn invalid_first_segment(identifier: &str, got: &str) -> Status {
    Status::new(
        Code::InvalidArgument,
        format!(
            "invalid first segment in identifier \"{identifier}\", expecting \"namespaces\", got \"{got}\""
        ),
    )
}

/// Third-segment (kind) check (`identifier.go:57-64`).
// go: internal/service/utils/identifier.go:57-64
#[must_use]
pub fn invalid_third_segment(identifier: &str, expecting: &str, got: &str) -> Status {
    Status::new(
        Code::InvalidArgument,
        format!(
            "invalid third segment in identifier \"{identifier}\", expecting \"{expecting}\", got \"{got}\""
        ),
    )
}

// ===========================================================================
// Authorization denials (PERMISSION_DENIED)
// ===========================================================================

/// `VerifyClientObjectToken` deny (`token.go:83-85`).
// go: internal/oidc/token.go:83-85
#[must_use]
pub fn permission_denied_for_client(namespace: &str, name: &str) -> Status {
    Status::new(
        Code::PermissionDenied,
        format!("permission denied for client {namespace}/{name}"),
    )
}

/// `VerifyExporterObjectToken` deny (`token.go:122-124`).
// go: internal/oidc/token.go:122-124
#[must_use]
pub fn permission_denied_for_exporter(namespace: &str, name: &str) -> Status {
    Status::new(
        Code::PermissionDenied,
        format!("permission denied for exporter {namespace}/{name}"),
    )
}

/// ClientService namespace guard (`service/auth/auth.go:51, 71`).
// go: internal/service/auth/auth.go:51,71
#[must_use]
pub fn namespace_mismatch() -> Status {
    Status::new(Code::PermissionDenied, "namespace mismatch")
}

// ===========================================================================
// Lease release idempotence (FAILED_PRECONDITION)
// ===========================================================================

/// `DeleteLease` on an already-released lease (`client_service.go:382`). The
/// lease name is Go `%q`-quoted.
// go: internal/service/client/v1/client_service.go:382
#[must_use]
pub fn lease_already_released(name: &str) -> Status {
    Status::new(
        Code::FailedPrecondition,
        format!("lease {} has already been released", go_quote(name)),
    )
}

// ===========================================================================
// Auth-path k8s lookups: code-for-code mapped (token.go:19-40) — §12.1
// ===========================================================================
// Distinct from the verbatim-UNKNOWN forwarding of §12.2: these two sites
// (and only these two) map the kube error code-for-code and format the raw
// error with `%v`.

/// `VerifyClientObjectToken` Get failure (`token.go:92`):
/// `status.Errorf(k8sToGRPCCode(err), "client %s/%s: %v", ns, name, err)`.
// go: internal/oidc/token.go:88-92
#[must_use]
pub fn client_lookup_error(namespace: &str, name: &str, err: &StoreError) -> Status {
    Status::new(err.grpc_code(), format!("client {namespace}/{name}: {err}"))
}

/// `VerifyExporterObjectToken` Get failure (`token.go:131`):
/// `status.Errorf(k8sToGRPCCode(err), "exporter %s/%s: %v", ns, name, err)`.
// go: internal/oidc/token.go:127-131
#[must_use]
pub fn exporter_lookup_error(namespace: &str, name: &str, err: &StoreError) -> Status {
    Status::new(
        err.grpc_code(),
        format!("exporter {namespace}/{name}: {err}"),
    )
}

// ===========================================================================
// Verbatim passthrough of plain errors → UNKNOWN (spec 02 §12.2 warts #2, #3)
// ===========================================================================

/// Forward a **non-auth-path** kube error verbatim as `UNKNOWN`, exactly like
/// Go's `return nil, err` at the Dial/Listen/ClientService CRUD `Get`/`List`
/// sites. A nonexistent Lease/Exporter therefore surfaces as `UNKNOWN` with
/// the raw `"…not found"` text — **do not** map it to `NOT_FOUND`
/// (spec 02 §12.2).
///
/// For an apiserver [`kube::Error::Api`] we use the status **`message`** — the
/// same field Go's `apierrors.StatusError.Error()` returns (byte-identical,
/// since both clients deserialize the identical apiserver `Status`). kube's own
/// `Display` for `Error::Api` is `"ApiError: {msg}: {reason} (…)"`, which would
/// *not* match Go, so we deliberately reach past it. Non-API errors (transport,
/// decode) fall back to kube's `Display` — the closest analog to Go's raw
/// error text for those failure modes (a residual conformance concern for
/// non-apiserver failures).
// go: internal/service/controller_service.go:772-779 (Dial lease Get), etc.
#[must_use]
pub fn forward_apiserver_error(err: &kube::Error) -> Status {
    let message = match err {
        kube::Error::Api(status) => status.message.clone(),
        other => other.to_string(),
    };
    Status::new(Code::Unknown, message)
}

/// Forward any other plain Go-style error verbatim as `UNKNOWN` without
/// rewriting the message — the general form of §12.2. This is the path that
/// preserves the [`TOKEN_EXPIRED_SUBSTR`] substring on expired-token
/// failures, so clients still trigger re-auth.
#[must_use]
pub fn preserve_plain_error(err: impl std::fmt::Display) -> Status {
    Status::new(Code::Unknown, err.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The full consolidated table: every constructor pinned to its exact
    /// `(code, message)`. A single wrong byte here is a client-visible
    /// regression.
    #[test]
    fn error_string_contract_table() {
        let se_notfound = StoreError::new(
            StoreErrorKind::NotFound,
            "clients.jumpstarter.dev \"c1\" not found",
        );
        let se_forbidden = StoreError::new(StoreErrorKind::Forbidden, "forbidden: nope");

        let cases: Vec<(Status, Code, String)> = vec![
            // FAILED_PRECONDITION status gate ("not ready" substring).
            (
                exporter_not_ready_available(),
                Code::FailedPrecondition,
                "exporter is not ready (status: Available)".into(),
            ),
            (
                exporter_not_ready("Weird"),
                Code::FailedPrecondition,
                "exporter not ready (status: Weird)".into(),
            ),
            (
                exporter_offline(),
                Code::FailedPrecondition,
                "exporter is offline".into(),
            ),
            (
                exporter_before_lease_hook_failed(),
                Code::FailedPrecondition,
                "exporter beforeLease hook failed".into(),
            ),
            (
                exporter_after_lease_hook_failed(),
                Code::FailedPrecondition,
                "exporter afterLease hook failed".into(),
            ),
            // UNAVAILABLE / RESOURCE_EXHAUSTED delivery.
            (
                exporter_not_listening("lease-x"),
                Code::Unavailable,
                "exporter is not listening on lease lease-x".into(),
            ),
            (
                listener_buffer_full("lease-x"),
                Code::ResourceExhausted,
                "listener buffer full on lease lease-x".into(),
            ),
            // UNKNOWN plain-error warts (§12.2 wart #1).
            (
                permission_denied_transferred(),
                Code::Unknown,
                "permission denied".into(),
            ),
            (lease_not_active(), Code::Unknown, "lease not active".into()),
            (empty_lease_name(), Code::Unknown, "empty lease name".into()),
            (
                no_router_available(),
                Code::Unknown,
                "no router available".into(),
            ),
            // Internal (proper status).
            (
                unable_to_sign_token(),
                Code::Internal,
                "unable to sign token".into(),
            ),
            // Auth headers / metadata.
            (
                missing_authorization_header(),
                Code::Unauthenticated,
                "missing authorization header".into(),
            ),
            (
                missing_metadata(),
                Code::InvalidArgument,
                "missing metadata".into(),
            ),
            (
                multiple_authorization_headers(),
                Code::InvalidArgument,
                "multiple authorization headers".into(),
            ),
            (
                malformed_authorization_header(),
                Code::InvalidArgument,
                "malformed authorization header".into(),
            ),
            (
                missing_metadata_key("jumpstarter-client-name"),
                Code::InvalidArgument,
                "missing metadata: jumpstarter-client-name".into(),
            ),
            (
                multiple_metadata_key("jumpstarter-client-name"),
                Code::InvalidArgument,
                "multiple metadata: jumpstarter-client-name".into(),
            ),
            (
                resource_name_required(),
                Code::InvalidArgument,
                "resource name required for pre-existing authentication".into(),
            ),
            (
                resource_name_mismatch("provided-x", "expected-y", "user@example.com"),
                Code::InvalidArgument,
                "resource name mismatch: provided \"provided-x\" but expected \"expected-y\" (derived from OIDC username \"user@example.com\")".into(),
            ),
            (
                object_kind_mismatch(),
                Code::InvalidArgument,
                "object kind mismatch".into(),
            ),
            (
                one_of_selector_or_exporter_name_required(),
                Code::InvalidArgument,
                "one of selector or exporter_name is required".into(),
            ),
            // Identifier parsing (literal-quoted operands).
            (
                invalid_segment_count("namespaces/ns/leases", 4, 3),
                Code::InvalidArgument,
                "invalid number of segments in identifier \"namespaces/ns/leases\", expecting 4, got 3".into(),
            ),
            (
                invalid_first_segment("nope/ns/leases/l", "nope"),
                Code::InvalidArgument,
                "invalid first segment in identifier \"nope/ns/leases/l\", expecting \"namespaces\", got \"nope\"".into(),
            ),
            (
                invalid_third_segment("namespaces/ns/pods/l", "leases", "pods"),
                Code::InvalidArgument,
                "invalid third segment in identifier \"namespaces/ns/pods/l\", expecting \"leases\", got \"pods\"".into(),
            ),
            // PERMISSION_DENIED.
            (
                permission_denied_for_client("ns", "c1"),
                Code::PermissionDenied,
                "permission denied for client ns/c1".into(),
            ),
            (
                permission_denied_for_exporter("ns", "e1"),
                Code::PermissionDenied,
                "permission denied for exporter ns/e1".into(),
            ),
            (
                namespace_mismatch(),
                Code::PermissionDenied,
                "namespace mismatch".into(),
            ),
            // Lease release idempotence (%q-quoted name).
            (
                lease_already_released("my-lease"),
                Code::FailedPrecondition,
                "lease \"my-lease\" has already been released".into(),
            ),
            // Auth-path code-for-code mapping (§12.1).
            (
                client_lookup_error("ns", "c1", &se_notfound),
                Code::NotFound,
                "client ns/c1: clients.jumpstarter.dev \"c1\" not found".into(),
            ),
            (
                exporter_lookup_error("ns", "e1", &se_forbidden),
                Code::PermissionDenied,
                "exporter ns/e1: forbidden: nope".into(),
            ),
        ];

        for (status, code, message) in &cases {
            assert_eq!(status.code(), *code, "code for {message:?}");
            assert_eq!(status.message(), message, "message for {message:?}");
        }
    }

    /// `k8s_to_grpc_code` reproduces `token.go:19-40` code-for-code.
    #[test]
    fn k8s_to_grpc_code_maps_token_go() {
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

    /// §12.2: a plain error carrying the expired-token substring is forwarded
    /// verbatim as `UNKNOWN` — the code is not "fixed" and the substring
    /// survives so clients re-auth.
    #[test]
    fn expired_token_substring_preserved_as_unknown() {
        let status = preserve_plain_error("oidc: verify token: token is expired");
        assert_eq!(status.code(), Code::Unknown);
        assert!(status.message().contains(TOKEN_EXPIRED_SUBSTR));
    }

    /// The verbatim apiserver-error forward keeps `UNKNOWN` and does NOT map a
    /// not-found to `NOT_FOUND` (§12.2 wart #2).
    #[test]
    fn apiserver_error_stays_unknown_not_notfound() {
        let mut api =
            kube::core::Status::failure("leases.jumpstarter.dev \"absent\" not found", "NotFound");
        api.code = 404;
        let err = kube::Error::Api(Box::new(api));
        let status = forward_apiserver_error(&err);
        assert_eq!(status.code(), Code::Unknown);
        assert_ne!(status.code(), Code::NotFound);
        // Byte-identical to Go's `apierrors.StatusError.Error()` (the raw
        // apiserver status message), not kube's wrapped `Display`.
        assert_eq!(
            status.message(),
            "leases.jumpstarter.dev \"absent\" not found"
        );
    }

    /// Go `%q` (via [`go_quote`]) diverges from Rust `{:?}` on non-ASCII, which
    /// is exactly why the mismatch message uses `go_quote`: Go renders
    /// U+007F DELETE as `\x7f` where Rust `{:?}` renders `\u{7f}`. This pins
    /// that the constructor uses `go_quote`.
    #[test]
    fn go_quote_beats_debug_on_unicode() {
        let status = resource_name_mismatch("a\u{7f}b", "e", "u");
        assert!(
            status.message().contains("\\x7f"),
            "expected Go %q \\x7f escape, got: {}",
            status.message()
        );
        assert!(!status.message().contains("\\u{7f}"));
    }
}
