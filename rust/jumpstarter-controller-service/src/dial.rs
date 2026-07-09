//! `ControllerService.Dial` — the client's request for a router rendezvous on
//! an active lease (`controller/internal/service/controller_service.go:745-895`).
//!
//! Flow (Go order, preserved exactly):
//!
//! 1. Authenticate the client (`authenticateClient` → `VerifyClientObjectToken`).
//! 2. Reject an empty `lease_name` (`UNKNOWN "empty lease name"`).
//! 3. `Get` the lease in the client's namespace (apiserver errors forwarded
//!    verbatim as `UNKNOWN`, spec 02 §12.2).
//! 4. Ownership: `lease.spec.clientRef.name == client` else
//!    `UNKNOWN "permission denied"` — the string clients read as "lease
//!    transferred" (**not** a proper `PERMISSION_DENIED`).
//! 5. `status.exporterRef` present else `UNKNOWN "lease not active"`.
//! 6. `Get` the referenced exporter.
//! 7. **Status gate with server-side retry** — retry *only* while the exporter
//!    is `Available` (the transient lease-setup state), 500 ms backoff ×2 capped
//!    at 3 s, ≤ 30 s total, stopping when the next sleep would exceed the
//!    deadline; re-fetch the exporter each attempt; a cancelled context aborts.
//!    This protects old clients without client-side Dial retry (issue #309) and
//!    keys the client retry off the `"not ready"` substring.
//! 8. Router selection via a [`match_labels`] port: a router is eligible only if
//!    it carries **every** exporter label (else score −1); −1 scorers are still
//!    eligible; the highest score wins. Go iterates the router map in *random*
//!    order and breaks ties arbitrarily; we iterate the [`Router`] `BTreeMap` in
//!    key order and break ties by router name — a **benign, deterministic**
//!    documented divergence (the score, not the tie order, is contractual).
//! 9. Mint a fresh HS256 router token (`ROUTER_KEY`), push it to the exporter's
//!    active listener, and return the **same** `(endpoint, token)` to the
//!    client — the token's `sub` is the rendezvous key both peers dial with.
//!
//! ## Dependency seams
//!
//! The Dial logic is factored over three narrow traits so the retry timing and
//! router selection are unit-testable without a cluster:
//! [`ClientAuthenticator`], [`DialStore`], and [`ListenerSink`]. The production
//! [`ListenRegistry`](crate::listen_registry::ListenRegistry) satisfies
//! [`ListenerSink`] via the blanket impl below; the controller-service wiring
//! (sibling module) supplies the kube-backed authenticator and store.

#![allow(clippy::result_large_err)] // tonic::Status is the RPC error convention.

use std::collections::BTreeMap;
use std::time::Duration;

use jumpstarter_controller_api::client::Client;
use jumpstarter_controller_api::exporter::{Exporter, ExporterStatusValue};
use jumpstarter_controller_api::lease::Lease;
use jumpstarter_controller_auth::router_token::mint_router_token;
use jumpstarter_controller_config::router::{Router, RouterEntry};
use jumpstarter_protocol::v1::{DialRequest, DialResponse, ListenResponse};
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;
use tonic::metadata::MetadataMap;
use tonic::{Code, Status};

use crate::errors;

// Status-gate retry constants (`controller_service.go:806-808`).
const RETRY_DELAY_INITIAL: Duration = Duration::from_millis(500);
const RETRY_DELAY_MAX: Duration = Duration::from_secs(3);
const MAX_TOTAL_WAIT: Duration = Duration::from_secs(30);

// ---------------------------------------------------------------------------
// Dependency seams
// ---------------------------------------------------------------------------

/// Authenticates the calling client from request metadata, port of
/// `ControllerService.authenticateClient` → `oidc.VerifyClientObjectToken`
/// (`controller_service.go:222`). The returned [`Client`] carries the resolved
/// namespace/name; failures are already shaped into wire [`Status`]es by the
/// [`errors`](crate::errors) contract (bearer, authz deny, code-mapped lookup).
// Internal seam (never `dyn`); native `async fn` is fine.
#[allow(async_fn_in_trait)]
pub trait ClientAuthenticator {
    async fn authenticate_client(&self, metadata: &MetadataMap) -> Result<Client, Status>;
}

/// The two CR reads Dial performs. Non-auth-path `Get` failures must be
/// forwarded **verbatim as `UNKNOWN`** by the implementation
/// ([`errors::forward_apiserver_error`]) — never mapped to `NOT_FOUND`
/// (spec 02 §12.2).
#[allow(async_fn_in_trait)]
pub trait DialStore {
    async fn get_lease(&self, namespace: &str, name: &str) -> Result<Lease, Status>;
    async fn get_exporter(&self, namespace: &str, name: &str) -> Result<Exporter, Status>;
}

/// Non-blocking hand-off of the `(endpoint, token)` pair to the exporter's
/// active `Listen` queue — port of `ControllerService.sendToListener`
/// (`controller_service.go:171`). Returns `UNAVAILABLE`
/// (`"exporter is not listening on lease %s"`) or `RESOURCE_EXHAUSTED`
/// (`"listener buffer full on lease %s"`).
///
/// The production [`ListenRegistry`](crate::listen_registry::ListenRegistry)
/// implements this via the blanket impl below (its inherent
/// `send_to_listener` has the identical signature).
pub trait ListenerSink {
    fn send_to_listener(&self, lease_name: &str, response: ListenResponse) -> Result<(), Status>;
}

impl ListenerSink for crate::listen_registry::ListenRegistry {
    fn send_to_listener(&self, lease_name: &str, response: ListenResponse) -> Result<(), Status> {
        crate::listen_registry::ListenRegistry::send_to_listener(self, lease_name, response)
    }
}

// ---------------------------------------------------------------------------
// Pure ports
// ---------------------------------------------------------------------------

/// Port of `MatchLabels` (`helpers.go:3-13`): the score of a router `candidate`
/// against an exporter's `target` labels. Returns `-1` the moment the candidate
/// is missing (or mismatches) **any** target label; otherwise returns the
/// number of matched labels (`== target.len()`). A router therefore scores
/// `>= 0` only when it carries every exporter label; empty target labels score
/// `0` for every router.
// go: internal/service/helpers.go:3-13
#[must_use]
pub fn match_labels(
    candidate: &BTreeMap<String, String>,
    target: &BTreeMap<String, String>,
) -> i64 {
    let mut count: i64 = 0;
    for (k, vt) in target {
        match candidate.get(k) {
            Some(vc) if vc == vt => count += 1,
            _ => return -1,
        }
    }
    count
}

/// Router selection (`controller_service.go:849-861`). Ranks the configured
/// routers by [`match_labels`] against the exporter labels (highest first) and
/// returns the winner; `-1` scorers remain eligible. Empty config →
/// `UNKNOWN "no router available"`.
///
/// **Divergence (documented, benign):** Go sorts `maps.Values` (random order)
/// with an unstable sort, so ties among equal-scoring routers resolve
/// arbitrarily. We iterate the [`Router`] `BTreeMap` in key (name) order and
/// stable-sort by score descending, so ties resolve to the lowest router name —
/// deterministic. Only the score is contractual.
// go: internal/service/controller_service.go:849-861
pub fn select_router<'a>(
    routers: &'a Router,
    exporter_labels: &BTreeMap<String, String>,
) -> Result<&'a RouterEntry, Status> {
    if routers.is_empty() {
        return Err(errors::no_router_available());
    }
    // `routers.values()` is already ordered by router name (BTreeMap); a stable
    // sort by score-descending keeps that as the tie-break.
    let mut ranked: Vec<&RouterEntry> = routers.values().collect();
    ranked.sort_by(|a, b| {
        match_labels(&b.labels, exporter_labels).cmp(&match_labels(&a.labels, exporter_labels))
    });
    // Non-empty checked above.
    Ok(ranked[0])
}

/// Port of `checkExporterStatusForDriverCalls` (`controller_service.go:498-519`).
/// `Ok(())` means driver calls are allowed; the `Err` messages carry the
/// `"not ready"` substring clients key Dial retry off of.
///
/// The Go `default` arm (`"exporter not ready (status: %s)"`,
/// [`errors::exporter_not_ready`]) is unreachable through this typed enum —
/// the CRD `+kubebuilder:validation:Enum` restricts the field to the eight
/// variants below, and any unknown/empty string already collapses to
/// [`ExporterStatusValue::Unspecified`] (allowed) at deserialization. The
/// string constructor is still exported for the conformance harness.
// go: internal/service/controller_service.go:498-519
pub fn check_exporter_status_for_driver_calls(status: ExporterStatusValue) -> Result<(), Status> {
    use ExporterStatusValue::*;
    match status {
        // Normal operation + hook windows (allow `j` from hooks) + old
        // exporters that don't report status.
        LeaseReady | BeforeLeaseHook | AfterLeaseHook | Unspecified => Ok(()),
        Offline => Err(errors::exporter_offline()),
        Available => Err(errors::exporter_not_ready_available()),
        BeforeLeaseHookFailed => Err(errors::exporter_before_lease_hook_failed()),
        AfterLeaseHookFailed => Err(errors::exporter_after_lease_hook_failed()),
    }
}

/// The exporter's reported status, defaulting to
/// [`ExporterStatusValue::Unspecified`] when unset (matches Go reading the
/// empty string as the backwards-compat "allow" case).
fn exporter_status_value(exporter: &Exporter) -> ExporterStatusValue {
    exporter
        .status
        .as_ref()
        .and_then(|s| s.exporter_status)
        .unwrap_or_default()
}

// ---------------------------------------------------------------------------
// Status gate with server-side retry
// ---------------------------------------------------------------------------

/// The Dial status gate (`controller_service.go:805-846`). Returns the
/// **exporter as it existed when its status passed the gate** as soon as the
/// status allows driver calls — this is the object Go's router selection scores
/// against, and it is the *re-fetched* exporter after any retry, not the initial
/// one. Retries **only** while the status is `Available` (transient during lease
/// setup): sleep `retry_delay` (500 ms, doubling, capped at 3 s), re-fetch via
/// `refetch`, re-check — until the status clears, becomes a non-`Available`
/// error (returned immediately), or the next sleep would exceed the 30 s
/// deadline (the last error is returned). A cancelled `cancel` token aborts with
/// `CANCELLED` (`ctx.Err()`).
///
/// `refetch` returns the freshly-read whole exporter, or a wire [`Status`] if
/// the re-fetch `Get` failed (Go returns that error directly — verbatim
/// `UNKNOWN` for apiserver errors). Go re-fetches into the *same* `exporter`
/// variable inside the loop, so both the status check **and** the later
/// `exporter.Labels` router selection observe the post-retry object; returning
/// the final exporter here preserves that (parity fix — the initial fetch's
/// labels must not be used).
pub async fn wait_for_exporter_ready<F, Fut>(
    initial: Exporter,
    mut refetch: F,
    cancel: Option<&CancellationToken>,
) -> Result<Exporter, Status>
where
    F: FnMut() -> Fut,
    Fut: std::future::Future<Output = Result<Exporter, Status>>,
{
    let mut retry_delay = RETRY_DELAY_INITIAL;
    let deadline = Instant::now() + MAX_TOTAL_WAIT;
    let mut exporter = initial;

    loop {
        let status = exporter_status_value(&exporter);
        let status_err = match check_exporter_status_for_driver_calls(status) {
            // Gate passed: hand back the exporter whose status just cleared —
            // its labels are what router selection scores against.
            Ok(()) => return Ok(exporter),
            Err(err) => err,
        };
        // Only retry for Available (transient during lease setup). Other error
        // statuses (Offline, HookFailed, …) are not transient.
        if status != ExporterStatusValue::Available {
            return Err(status_err);
        }
        // Next sleep would exceed the deadline; stop retrying.
        if Instant::now() + retry_delay > deadline {
            return Err(status_err);
        }
        // Sleep, aborting on context cancellation (`ctx.Done()`).
        match cancel {
            Some(token) => {
                tokio::select! {
                    biased;
                    () = token.cancelled() => {
                        return Err(Status::new(Code::Cancelled, "context canceled"));
                    }
                    () = tokio::time::sleep(retry_delay) => {}
                }
            }
            None => tokio::time::sleep(retry_delay).await,
        }
        // Exponential backoff capped at maxDelay.
        retry_delay = (retry_delay * 2).min(RETRY_DELAY_MAX);
        // Re-fetch the whole exporter for the next iteration (Go re-`Get`s into
        // the same `exporter` var, updating both its status and its labels).
        exporter = refetch().await?;
    }
}

// ---------------------------------------------------------------------------
// Dial orchestrator
// ---------------------------------------------------------------------------

/// `ControllerService.Dial` (`controller_service.go:745-895`), factored over the
/// dependency seams. `now_unix_secs` is the router-token issue time (production
/// passes the current wall clock; tests pin it). `cancel` mirrors the request
/// context for the status-gate abort.
#[allow(clippy::too_many_arguments)]
pub async fn dial<A, S, L>(
    metadata: &MetadataMap,
    req: &DialRequest,
    authenticator: &A,
    store: &S,
    routers: &Router,
    router_key: &[u8],
    listener: &L,
    now_unix_secs: i64,
    cancel: Option<&CancellationToken>,
) -> Result<DialResponse, Status>
where
    A: ClientAuthenticator,
    S: DialStore,
    L: ListenerSink,
{
    // 1. Authenticate.
    let client = authenticator.authenticate_client(metadata).await?;
    let namespace = client.metadata.namespace.clone().unwrap_or_default();
    let client_name = client.metadata.name.clone().unwrap_or_default();

    // 2. Empty lease name → UNKNOWN "empty lease name".
    let lease_name = req.lease_name.as_str();
    if lease_name.is_empty() {
        return Err(errors::empty_lease_name());
    }

    // 3. Get the lease (verbatim apiserver errors handled by the store impl).
    let lease = store.get_lease(&namespace, lease_name).await?;

    // 4. Ownership: lease.spec.clientRef.name must equal the caller.
    if lease.spec.client_ref.name != client_name {
        return Err(errors::permission_denied_transferred());
    }

    // 5. status.exporterRef must be present.
    let exporter_name = match lease.status.as_ref().and_then(|s| s.exporter_ref.as_ref()) {
        Some(reference) => reference.name.clone(),
        None => return Err(errors::lease_not_active()),
    };

    // 6. Get the referenced exporter.
    let exporter = store.get_exporter(&namespace, &exporter_name).await?;

    // 7. Status gate with server-side retry (Available-only). The `move`
    // refetch closure owns its copies so its future borrows nothing external.
    // The gate returns the exporter as it existed when the gate passed — the
    // *re-fetched* object after any retry, since Go re-`Get`s into the same
    // `exporter` variable and later scores router labels against it.
    let gate_ns = namespace.clone();
    let gate_name = exporter_name.clone();
    let exporter = wait_for_exporter_ready(
        exporter,
        move || {
            let store = store;
            let ns = gate_ns.clone();
            let name = gate_name.clone();
            async move { store.get_exporter(&ns, &name).await }
        },
        cancel,
    )
    .await?;

    // 8. Router selection scores against the post-gate exporter's labels
    // (`controller_service.go:849-850` uses `exporter.Labels` after the retry
    // loop, so relabels applied during lease setup are honored).
    let exporter_labels = exporter.metadata.labels.clone().unwrap_or_default();
    let router = select_router(routers, &exporter_labels)?;
    let endpoint = router.endpoint.clone();

    // 9. Mint the HS256 router token; push it to the listener, return the same.
    let (token, _stream_id) =
        mint_router_token(router_key, now_unix_secs).map_err(|_| errors::unable_to_sign_token())?;

    let response = ListenResponse {
        router_endpoint: endpoint.clone(),
        router_token: token.clone(),
    };
    listener.send_to_listener(lease_name, response)?;

    Ok(DialResponse {
        router_endpoint: endpoint,
        router_token: token,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    fn labels(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect()
    }

    fn router(endpoint: &str, label_pairs: &[(&str, &str)]) -> RouterEntry {
        RouterEntry {
            endpoint: endpoint.to_string(),
            labels: labels(label_pairs),
        }
    }

    // -- match_labels ------------------------------------------------------

    #[test]
    fn match_labels_requires_every_target_label() {
        // Candidate carrying all target labels scores == target.len().
        assert_eq!(
            match_labels(&labels(&[("a", "1"), ("b", "2")]), &labels(&[("a", "1")])),
            1
        );
        assert_eq!(
            match_labels(
                &labels(&[("a", "1"), ("b", "2")]),
                &labels(&[("a", "1"), ("b", "2")])
            ),
            2
        );
        // Missing a target label → -1.
        assert_eq!(
            match_labels(&labels(&[("a", "1")]), &labels(&[("a", "1"), ("b", "2")])),
            -1
        );
        // Value mismatch → -1.
        assert_eq!(
            match_labels(&labels(&[("a", "9")]), &labels(&[("a", "1")])),
            -1
        );
        // Empty target → 0 for any candidate.
        assert_eq!(match_labels(&labels(&[("a", "1")]), &labels(&[])), 0);
        assert_eq!(match_labels(&labels(&[]), &labels(&[])), 0);
    }

    // -- select_router -----------------------------------------------------

    #[test]
    fn select_router_empty_config_is_no_router_available() {
        let routers: Router = BTreeMap::new();
        let err = select_router(&routers, &labels(&[])).unwrap_err();
        assert_eq!(err.code(), Code::Unknown);
        assert_eq!(err.message(), "no router available");
    }

    #[test]
    fn select_router_prefers_higher_score() {
        let mut routers: Router = BTreeMap::new();
        // "match" carries every exporter label (score 1); "nomatch" is missing
        // it (score -1) but stays eligible.
        routers.insert(
            "match".into(),
            router("ep-match", &[("region", "eu"), ("x", "y")]),
        );
        routers.insert("nomatch".into(), router("ep-nomatch", &[]));
        let chosen = select_router(&routers, &labels(&[("region", "eu")])).unwrap();
        assert_eq!(chosen.endpoint, "ep-match");
    }

    #[test]
    fn select_router_all_minus_one_still_selects_first_by_name() {
        // Every router scores -1; the first by BTreeMap key order wins
        // (deterministic tie-break, documented divergence from Go's random).
        let mut routers: Router = BTreeMap::new();
        routers.insert("b-router".into(), router("ep-b", &[]));
        routers.insert("a-router".into(), router("ep-a", &[]));
        let chosen = select_router(&routers, &labels(&[("region", "eu")])).unwrap();
        assert_eq!(chosen.endpoint, "ep-a");
    }

    #[test]
    fn select_router_ties_break_by_name_deterministically() {
        // Both score 0 (empty exporter labels); lowest name wins.
        let mut routers: Router = BTreeMap::new();
        routers.insert("z-router".into(), router("ep-z", &[]));
        routers.insert("a-router".into(), router("ep-a", &[]));
        let chosen = select_router(&routers, &labels(&[])).unwrap();
        assert_eq!(chosen.endpoint, "ep-a");
    }

    // -- check_exporter_status_for_driver_calls ----------------------------

    #[test]
    fn status_gate_allows_and_rejects_per_go() {
        use ExporterStatusValue::*;
        for s in [LeaseReady, BeforeLeaseHook, AfterLeaseHook, Unspecified] {
            assert!(check_exporter_status_for_driver_calls(s).is_ok(), "{s:?}");
        }
        let offline = check_exporter_status_for_driver_calls(Offline).unwrap_err();
        assert_eq!(offline.code(), Code::FailedPrecondition);
        assert_eq!(offline.message(), "exporter is offline");

        let avail = check_exporter_status_for_driver_calls(Available).unwrap_err();
        assert_eq!(avail.code(), Code::FailedPrecondition);
        assert_eq!(avail.message(), "exporter is not ready (status: Available)");
        assert!(avail.message().contains("not ready"));
    }

    // -- wait_for_exporter_ready (retry timing) ----------------------------

    /// Already-ready on the first check: returns immediately, zero re-fetches.
    #[tokio::test(start_paused = true)]
    async fn gate_ready_immediately_no_refetch() {
        let calls = Cell::new(0);
        let res = wait_for_exporter_ready(
            make_exporter("exp-1", ExporterStatusValue::LeaseReady, &[]),
            || {
                calls.set(calls.get() + 1);
                async { Ok(make_exporter("exp-1", ExporterStatusValue::LeaseReady, &[])) }
            },
            None,
        )
        .await;
        assert!(res.is_ok());
        assert_eq!(calls.get(), 0, "no refetch when already ready");
    }

    /// Non-Available error status fails immediately without retrying.
    #[tokio::test(start_paused = true)]
    async fn gate_non_available_fails_immediately() {
        let calls = Cell::new(0);
        let res = wait_for_exporter_ready(
            make_exporter("exp-1", ExporterStatusValue::Offline, &[]),
            || {
                calls.set(calls.get() + 1);
                async { Ok(make_exporter("exp-1", ExporterStatusValue::Offline, &[])) }
            },
            None,
        )
        .await;
        let err = res.unwrap_err();
        assert_eq!(err.message(), "exporter is offline");
        assert_eq!(calls.get(), 0, "Offline is not transient — no retry");
    }

    /// Available that flips to LeaseReady within the budget succeeds after the
    /// expected number of re-fetches.
    #[tokio::test(start_paused = true)]
    async fn gate_available_flips_within_budget_succeeds() {
        let calls = Cell::new(0);
        let res = wait_for_exporter_ready(
            make_exporter("exp-1", ExporterStatusValue::Available, &[]),
            || {
                let n = calls.get() + 1;
                calls.set(n);
                // Ready on the 3rd re-fetch.
                async move {
                    let status = if n >= 3 {
                        ExporterStatusValue::LeaseReady
                    } else {
                        ExporterStatusValue::Available
                    };
                    Ok(make_exporter("exp-1", status, &[]))
                }
            },
            None,
        )
        .await;
        assert!(res.is_ok(), "should succeed once it flips ready");
        assert_eq!(calls.get(), 3);
    }

    /// Available that never flips fails with the Available message once the
    /// 30 s deadline is reached (the next sleep would exceed it).
    #[tokio::test(start_paused = true)]
    async fn gate_available_past_deadline_fails_with_available_message() {
        let start = Instant::now();
        let res = wait_for_exporter_ready(
            make_exporter("exp-1", ExporterStatusValue::Available, &[]),
            || async { Ok(make_exporter("exp-1", ExporterStatusValue::Available, &[])) },
            None,
        )
        .await;
        let err = res.unwrap_err();
        assert_eq!(err.code(), Code::FailedPrecondition);
        assert_eq!(err.message(), "exporter is not ready (status: Available)");
        // Total virtual wait stayed within the 30 s budget (no sleep past it).
        assert!(Instant::now().duration_since(start) <= MAX_TOTAL_WAIT);
    }

    /// A refetch error is propagated verbatim (Go returns the Get error).
    #[tokio::test(start_paused = true)]
    async fn gate_refetch_error_propagates() {
        let res = wait_for_exporter_ready(
            make_exporter("exp-1", ExporterStatusValue::Available, &[]),
            || async { Err(errors::forward_apiserver_error(&kube_notfound())) },
            None,
        )
        .await;
        let err = res.unwrap_err();
        assert_eq!(err.code(), Code::Unknown);
        assert!(err.message().contains("not found"));
    }

    /// A cancelled context aborts the gate with CANCELLED (`ctx.Err()`).
    #[tokio::test(start_paused = true)]
    async fn gate_cancel_aborts() {
        let token = CancellationToken::new();
        token.cancel(); // pre-cancelled: the biased select takes this branch.
        let res = wait_for_exporter_ready(
            make_exporter("exp-1", ExporterStatusValue::Available, &[]),
            || async { Ok(make_exporter("exp-1", ExporterStatusValue::Available, &[])) },
            Some(&token),
        )
        .await;
        let err = res.unwrap_err();
        assert_eq!(err.code(), Code::Cancelled);
    }

    fn kube_notfound() -> kube::Error {
        let mut api =
            kube::core::Status::failure("leases.jumpstarter.dev \"x\" not found", "NotFound");
        api.code = 404;
        kube::Error::Api(Box::new(api))
    }

    // -- dial orchestration ------------------------------------------------

    struct FakeAuth {
        client: Client,
    }
    impl ClientAuthenticator for FakeAuth {
        async fn authenticate_client(&self, _metadata: &MetadataMap) -> Result<Client, Status> {
            Ok(self.client.clone())
        }
    }

    struct FakeStore {
        lease: Lease,
        exporter: Exporter,
    }
    impl DialStore for FakeStore {
        async fn get_lease(&self, _ns: &str, _name: &str) -> Result<Lease, Status> {
            Ok(self.lease.clone())
        }
        async fn get_exporter(&self, _ns: &str, _name: &str) -> Result<Exporter, Status> {
            Ok(self.exporter.clone())
        }
    }

    #[derive(Default)]
    struct FakeListener {
        received: std::sync::Mutex<Vec<ListenResponse>>,
        fail: bool,
    }
    impl ListenerSink for FakeListener {
        fn send_to_listener(
            &self,
            lease_name: &str,
            response: ListenResponse,
        ) -> Result<(), Status> {
            if self.fail {
                return Err(errors::exporter_not_listening(lease_name));
            }
            self.received.lock().unwrap().push(response);
            Ok(())
        }
    }

    fn make_client(namespace: &str, name: &str) -> Client {
        use jumpstarter_controller_api::client::ClientSpec;
        let mut c = Client::new(name, ClientSpec::default());
        c.metadata.namespace = Some(namespace.to_string());
        c
    }

    fn make_lease(client_name: &str, exporter_name: Option<&str>) -> Lease {
        use jumpstarter_controller_api::lease::{LeaseSpec, LeaseStatus};
        use k8s_openapi::api::core::v1::LocalObjectReference;
        let mut lease = Lease::new(
            "lease-1",
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: client_name.to_string(),
                },
                ..Default::default()
            },
        );
        lease.status = Some(LeaseStatus {
            exporter_ref: exporter_name.map(|n| LocalObjectReference {
                name: n.to_string(),
            }),
            ..Default::default()
        });
        lease
    }

    fn make_exporter(
        name: &str,
        status: ExporterStatusValue,
        label_pairs: &[(&str, &str)],
    ) -> Exporter {
        use jumpstarter_controller_api::exporter::{ExporterSpec, ExporterStatus};
        let mut e = Exporter::new(name, ExporterSpec::default());
        e.metadata.labels = Some(labels(label_pairs));
        e.status = Some(ExporterStatus {
            exporter_status: Some(status),
            ..Default::default()
        });
        e
    }

    fn one_router() -> Router {
        let mut r: Router = BTreeMap::new();
        r.insert("r0".into(), router("router-0.example:443", &[]));
        r
    }

    const KEY: &[u8] = b"router-key";
    const NOW: i64 = 1_750_000_000;

    #[tokio::test(start_paused = true)]
    async fn dial_happy_path_returns_and_pushes_same_token() {
        let auth = FakeAuth {
            client: make_client("ns1", "client-a"),
        };
        let store = FakeStore {
            lease: make_lease("client-a", Some("exp-1")),
            exporter: make_exporter("exp-1", ExporterStatusValue::LeaseReady, &[]),
        };
        let listener = FakeListener::default();
        let routers = one_router();

        let resp = dial(
            &MetadataMap::new(),
            &DialRequest {
                lease_name: "lease-1".into(),
            },
            &auth,
            &store,
            &routers,
            KEY,
            &listener,
            NOW,
            None,
        )
        .await
        .expect("dial succeeds");

        assert_eq!(resp.router_endpoint, "router-0.example:443");
        // The exact same (endpoint, token) was pushed to the listener.
        let pushed = listener.received.lock().unwrap();
        assert_eq!(pushed.len(), 1);
        assert_eq!(pushed[0].router_endpoint, resp.router_endpoint);
        assert_eq!(pushed[0].router_token, resp.router_token);
        assert!(!resp.router_token.is_empty());
    }

    #[tokio::test(start_paused = true)]
    async fn dial_empty_lease_name_is_unknown() {
        let auth = FakeAuth {
            client: make_client("ns1", "client-a"),
        };
        let store = FakeStore {
            lease: make_lease("client-a", Some("exp-1")),
            exporter: make_exporter("exp-1", ExporterStatusValue::LeaseReady, &[]),
        };
        let err = dial(
            &MetadataMap::new(),
            &DialRequest {
                lease_name: String::new(),
            },
            &auth,
            &store,
            &one_router(),
            KEY,
            &FakeListener::default(),
            NOW,
            None,
        )
        .await
        .unwrap_err();
        assert_eq!(err.code(), Code::Unknown);
        assert_eq!(err.message(), "empty lease name");
    }

    #[tokio::test(start_paused = true)]
    async fn dial_wrong_owner_is_unknown_permission_denied() {
        let auth = FakeAuth {
            client: make_client("ns1", "client-a"),
        };
        let store = FakeStore {
            lease: make_lease("someone-else", Some("exp-1")),
            exporter: make_exporter("exp-1", ExporterStatusValue::LeaseReady, &[]),
        };
        let err = dial(
            &MetadataMap::new(),
            &DialRequest {
                lease_name: "lease-1".into(),
            },
            &auth,
            &store,
            &one_router(),
            KEY,
            &FakeListener::default(),
            NOW,
            None,
        )
        .await
        .unwrap_err();
        // The lease-transfer classification: UNKNOWN "permission denied".
        assert_eq!(err.code(), Code::Unknown);
        assert_eq!(err.message(), "permission denied");
    }

    #[tokio::test(start_paused = true)]
    async fn dial_no_exporter_ref_is_lease_not_active() {
        let auth = FakeAuth {
            client: make_client("ns1", "client-a"),
        };
        let store = FakeStore {
            lease: make_lease("client-a", None),
            exporter: make_exporter("exp-1", ExporterStatusValue::LeaseReady, &[]),
        };
        let err = dial(
            &MetadataMap::new(),
            &DialRequest {
                lease_name: "lease-1".into(),
            },
            &auth,
            &store,
            &one_router(),
            KEY,
            &FakeListener::default(),
            NOW,
            None,
        )
        .await
        .unwrap_err();
        assert_eq!(err.code(), Code::Unknown);
        assert_eq!(err.message(), "lease not active");
    }

    #[tokio::test(start_paused = true)]
    async fn dial_no_router_available() {
        let auth = FakeAuth {
            client: make_client("ns1", "client-a"),
        };
        let store = FakeStore {
            lease: make_lease("client-a", Some("exp-1")),
            exporter: make_exporter("exp-1", ExporterStatusValue::LeaseReady, &[]),
        };
        let err = dial(
            &MetadataMap::new(),
            &DialRequest {
                lease_name: "lease-1".into(),
            },
            &auth,
            &store,
            &BTreeMap::new(),
            KEY,
            &FakeListener::default(),
            NOW,
            None,
        )
        .await
        .unwrap_err();
        assert_eq!(err.code(), Code::Unknown);
        assert_eq!(err.message(), "no router available");
    }

    #[tokio::test(start_paused = true)]
    async fn dial_listener_not_listening_propagates() {
        let auth = FakeAuth {
            client: make_client("ns1", "client-a"),
        };
        let store = FakeStore {
            lease: make_lease("client-a", Some("exp-1")),
            exporter: make_exporter("exp-1", ExporterStatusValue::LeaseReady, &[]),
        };
        let listener = FakeListener {
            fail: true,
            ..Default::default()
        };
        let err = dial(
            &MetadataMap::new(),
            &DialRequest {
                lease_name: "lease-1".into(),
            },
            &auth,
            &store,
            &one_router(),
            KEY,
            &listener,
            NOW,
            None,
        )
        .await
        .unwrap_err();
        assert_eq!(err.code(), Code::Unavailable);
        assert_eq!(err.message(), "exporter is not listening on lease lease-1");
    }

    /// Parity: router selection must score against the exporter's labels **as
    /// they exist after the status gate's retry loop**, not the initially
    /// fetched labels. Go re-`Get`s the whole exporter inside the Available
    /// retry loop and then selects the router from that post-retry
    /// `exporter.Labels` (`controller_service.go:837-850`). Here the initial
    /// fetch is `Available` with `region=eu`; the post-gate re-fetch flips to
    /// `LeaseReady` with `region=us`. The `us` router must win — if selection
    /// used the initial labels it would pick the `eu` router.
    #[tokio::test(start_paused = true)]
    async fn dial_selects_router_from_post_gate_exporter_labels() {
        // A store returning a different exporter object per get_exporter call:
        // index 0 is the initial fetch, index 1 is the post-gate re-fetch.
        struct SequencedStore {
            lease: Lease,
            exporters: Vec<Exporter>,
            idx: std::sync::Mutex<usize>,
        }
        impl DialStore for SequencedStore {
            async fn get_lease(&self, _ns: &str, _name: &str) -> Result<Lease, Status> {
                Ok(self.lease.clone())
            }
            async fn get_exporter(&self, _ns: &str, _name: &str) -> Result<Exporter, Status> {
                let mut i = self.idx.lock().unwrap();
                // Last entry is sticky once reached.
                let e = self.exporters[(*i).min(self.exporters.len() - 1)].clone();
                *i += 1;
                Ok(e)
            }
        }

        let auth = FakeAuth {
            client: make_client("ns1", "client-a"),
        };
        let store = SequencedStore {
            lease: make_lease("client-a", Some("exp-1")),
            exporters: vec![
                // Initial fetch: transient Available, labels region=eu.
                make_exporter("exp-1", ExporterStatusValue::Available, &[("region", "eu")]),
                // Post-gate re-fetch: ready, relabeled region=us.
                make_exporter(
                    "exp-1",
                    ExporterStatusValue::LeaseReady,
                    &[("region", "us")],
                ),
            ],
            idx: std::sync::Mutex::new(0),
        };

        // Two routers keyed on region; only one matches each label set.
        let mut routers: Router = BTreeMap::new();
        routers.insert("r-eu".into(), router("ep-eu", &[("region", "eu")]));
        routers.insert("r-us".into(), router("ep-us", &[("region", "us")]));

        let listener = FakeListener::default();
        let resp = dial(
            &MetadataMap::new(),
            &DialRequest {
                lease_name: "lease-1".into(),
            },
            &auth,
            &store,
            &routers,
            KEY,
            &listener,
            NOW,
            None,
        )
        .await
        .expect("dial succeeds");

        // Post-gate labels are region=us → the us router wins. If selection had
        // used the initial (region=eu) labels this would be "ep-eu".
        assert_eq!(resp.router_endpoint, "ep-us");
        assert_eq!(
            listener.received.lock().unwrap()[0].router_endpoint,
            "ep-us"
        );
    }
}
