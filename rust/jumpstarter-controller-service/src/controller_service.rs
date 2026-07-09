//! The exporter-facing gRPC service, `jumpstarter.v1.ControllerService`, ported
//! from `controller/internal/service/controller_service.go`.
//!
//! This module assembles the pieces the sibling modules already provide:
//! per-call authentication ([`ControllerAuth`], a port of
//! `oidc.VerifyExporterObjectToken` / `VerifyClientObjectToken`), the
//! supersede/drain [`ListenRegistry`](crate::listen_registry) for `Listen`, the
//! [`run_status_stream`](crate::status_stream) heartbeat/watchdog for `Status`,
//! the [`dial`](crate::dial) orchestrator for `Dial`, and the shared
//! byte-identical [`errors`](crate::errors) contract.
//!
//! Every RPC authenticates the caller per-call (there is no session): exporter
//! RPCs (`Register`/`Unregister`/`ReportStatus`/`Listen`/`Status`) resolve the
//! `Exporter` CR via [`ControllerAuth::verify_exporter`]; client RPCs (`Dial`
//! and the legacy `GetLease`/`RequestLease`/`ReleaseLease`/`ListLeases`) resolve
//! the `Client` CR via [`ControllerAuth::verify_client`]. `AuditStream` is
//! intentionally `UNIMPLEMENTED` (Go never implemented it) but stays in the
//! descriptor.

#![allow(clippy::result_large_err)] // tonic::Status is the RPC error convention.

use std::pin::Pin;
use std::sync::Arc;

use futures::Stream;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::metadata::MetadataMap;
use tonic::{Request, Response, Status, Streaming};

use k8s_openapi::api::core::v1::LocalObjectReference;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{
    LabelSelector, LabelSelectorRequirement, Time,
};
use k8s_openapi::jiff::Timestamp;
use kube::api::{Api, ListParams, Patch, PatchParams, PostParams};

use jumpstarter_controller_api::client::Client;
use jumpstarter_controller_api::conditions::EXPORTER_CONDITION_TYPE_ONLINE;
use jumpstarter_controller_api::device::Device;
use jumpstarter_controller_api::exporter::{Exporter, ExporterStatusValue};
use jumpstarter_controller_api::go_duration::GoDuration;
use jumpstarter_controller_api::labels::LEASE_LABEL_ENDED;
use jumpstarter_controller_api::lease::{Lease, LeaseSpec};
use jumpstarter_controller_auth::authorize::{
    bearer_token_from_metadata, Attributes, BasicAuthorizer, Decision, KubeObjectStore,
    MetadataAttributesGetter, ObjectStore,
};
use jumpstarter_controller_auth::validator::TokenValidator;
use jumpstarter_controller_config::router::Router;
use jumpstarter_controller_core::conditions::{condition, set_status_condition};

use jumpstarter_protocol::v1 as pb;
use jumpstarter_protocol::v1::controller_service_server::ControllerService as ControllerServiceTrait;

use crate::client_service::AuthClient;
use crate::dial::{self, ClientAuthenticator, DialStore};
use crate::errors;
use crate::listen_registry::{drive_listen_loop, ListenRegistry};
use crate::status_stream::run_status_stream;

/// The `jumpstarter.dev/` label prefix. Only labels under it are controller-
/// managed on `Register` (`controller_service.go:264-275`).
const MANAGED_LABEL_PREFIX: &str = "jumpstarter.dev/";

// ===========================================================================
// Per-call authentication (oidc/token.go)
// ===========================================================================

/// The shared per-call authenticator, a port of the `ControllerService` auth
/// fields (`Authn`/`Authz`/`Attr`) plus `oidc.VerifyClientObjectToken` /
/// `VerifyExporterObjectToken` (`controller/internal/oidc/token.go`).
///
/// It is shared (behind an `Arc`) by both [`ControllerService`] and the
/// [`ClientService`](crate::client_service::ClientService): the same
/// authenticate-then-authorize-then-fetch pipeline backs every RPC.
pub struct ControllerAuth {
    client: kube::Client,
    validator: Arc<TokenValidator>,
    attr: MetadataAttributesGetter,
    /// The internal username prefix (`TokenValidator::internal_prefix`), passed
    /// to [`BasicAuthorizer`] for membership checks (Go `prefix`).
    internal_prefix: String,
    /// Whether Client CRs are auto-provisioned (`provisioning.Enabled`);
    /// Exporters are never provisioned.
    provisioning: bool,
}

impl ControllerAuth {
    /// Wire the authenticator. `internal_prefix` is normally
    /// `validator.internal_prefix()`.
    pub fn new(
        client: kube::Client,
        validator: Arc<TokenValidator>,
        internal_prefix: impl Into<String>,
        provisioning: bool,
    ) -> Self {
        Self {
            client,
            validator,
            attr: MetadataAttributesGetter::jumpstarter(),
            internal_prefix: internal_prefix.into(),
            provisioning,
        }
    }

    /// Port of `VerifyOIDCToken` (`token.go:41-56`): extract the bearer token,
    /// authenticate it against the validator union, then derive the request
    /// [`Attributes`] from metadata + the authenticated username.
    async fn verify_oidc(&self, metadata: &MetadataMap) -> Result<Attributes, Status> {
        // bearer.go: missing/multiple/malformed already shaped into Status.
        let token = bearer_token_from_metadata(metadata)?;
        // Go: `auth.AuthenticateContext` error (or the `!ok`
        // "failed to authenticate token" branch) returns verbatim → UNKNOWN,
        // preserving the "token is expired" substring clients key re-auth off.
        let authenticated = self
            .validator
            .authenticate(&token)
            .await
            .map_err(errors::preserve_plain_error)?;
        self.attr
            .context_attributes(metadata, &authenticated.username)
    }

    /// Port of `VerifyClientObjectToken` (`token.go:58-95`): OIDC verify, kind
    /// check, authorize (with Client auto-provisioning), then re-`Get` the CR.
    pub async fn verify_client(&self, metadata: &MetadataMap) -> Result<Client, Status> {
        let attrs = self.verify_oidc(metadata).await?;
        if attrs.resource != "Client" {
            return Err(errors::object_kind_mismatch());
        }
        let store = KubeObjectStore::new(self.client.clone());
        let authorizer = BasicAuthorizer::new(
            KubeObjectStore::new(self.client.clone()),
            self.internal_prefix.clone(),
            self.provisioning,
        );
        match authorizer.authorize(&attrs).await {
            Ok((Decision::Allow, _)) => {}
            Ok((Decision::Deny, _)) => {
                return Err(errors::permission_denied_for_client(
                    &attrs.namespace,
                    &attrs.name,
                ))
            }
            // Go: status.Errorf(status.Code(err), "client %s/%s: %v", ...).
            Err(err) => {
                return Err(errors::client_lookup_error(
                    &attrs.namespace,
                    &attrs.name,
                    &err,
                ))
            }
        }
        // Go performs a second `kclient.Get` after the authorize decision.
        store
            .get_client(&attrs.namespace, &attrs.name)
            .await
            .map_err(|err| errors::client_lookup_error(&attrs.namespace, &attrs.name, &err))
    }

    /// Port of `VerifyExporterObjectToken` (`token.go:97-134`). Exporters are
    /// never auto-provisioned.
    pub async fn verify_exporter(&self, metadata: &MetadataMap) -> Result<Exporter, Status> {
        let attrs = self.verify_oidc(metadata).await?;
        if attrs.resource != "Exporter" {
            return Err(errors::object_kind_mismatch());
        }
        let store = KubeObjectStore::new(self.client.clone());
        let authorizer = BasicAuthorizer::new(
            KubeObjectStore::new(self.client.clone()),
            self.internal_prefix.clone(),
            self.provisioning,
        );
        match authorizer.authorize(&attrs).await {
            Ok((Decision::Allow, _)) => {}
            Ok((Decision::Deny, _)) => {
                return Err(errors::permission_denied_for_exporter(
                    &attrs.namespace,
                    &attrs.name,
                ))
            }
            Err(err) => {
                return Err(errors::exporter_lookup_error(
                    &attrs.namespace,
                    &attrs.name,
                    &err,
                ))
            }
        }
        store
            .get_exporter(&attrs.namespace, &attrs.name)
            .await
            .map_err(|err| errors::exporter_lookup_error(&attrs.namespace, &attrs.name, &err))
    }

    /// Port of `auth.Auth.AuthClient` (`service/auth/auth.go:37-55`): verify the
    /// client token, then enforce `client.Namespace == namespace`.
    async fn verify_client_in_namespace(
        &self,
        metadata: &MetadataMap,
        namespace: &str,
    ) -> Result<Client, Status> {
        let client = self.verify_client(metadata).await?;
        let client_ns = client.metadata.namespace.as_deref().unwrap_or_default();
        if client_ns != namespace {
            return Err(errors::namespace_mismatch());
        }
        Ok(client)
    }
}

// -- Dependency-seam impls used by `dial` and `ClientService` ---------------

impl ClientAuthenticator for ControllerAuth {
    async fn authenticate_client(&self, metadata: &MetadataMap) -> Result<Client, Status> {
        self.verify_client(metadata).await
    }
}

impl DialStore for ControllerAuth {
    async fn get_lease(&self, namespace: &str, name: &str) -> Result<Lease, Status> {
        Api::<Lease>::namespaced(self.client.clone(), namespace)
            .get(name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))
    }

    async fn get_exporter(&self, namespace: &str, name: &str) -> Result<Exporter, Status> {
        Api::<Exporter>::namespaced(self.client.clone(), namespace)
            .get(name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))
    }
}

/// `ClientService`'s auth seam is satisfied by the shared `Arc<ControllerAuth>`
/// (the local `AuthClient` trait on a local type through `Arc` is allowed).
#[tonic::async_trait]
impl AuthClient for Arc<ControllerAuth> {
    async fn auth_client(&self, metadata: &MetadataMap, namespace: &str) -> Result<Client, Status> {
        self.verify_client_in_namespace(metadata, namespace).await
    }
}

// ===========================================================================
// ControllerService
// ===========================================================================

/// The `jumpstarter.v1.ControllerService` implementation. Mirrors Go's
/// `ControllerService` struct (kube client + auth + router config + signer's
/// router key + the in-memory listen registry).
pub struct ControllerService {
    client: kube::Client,
    auth: Arc<ControllerAuth>,
    registry: Arc<ListenRegistry>,
    router: Router,
    /// HS256 router-token key (`[]byte(os.Getenv("ROUTER_KEY"))`).
    router_key: Vec<u8>,
}

impl ControllerService {
    pub fn new(
        client: kube::Client,
        auth: Arc<ControllerAuth>,
        registry: Arc<ListenRegistry>,
        router: Router,
        router_key: Vec<u8>,
    ) -> Self {
        Self {
            client,
            auth,
            registry,
            router,
            router_key,
        }
    }

    fn exporters(&self, namespace: &str) -> Api<Exporter> {
        Api::namespaced(self.client.clone(), namespace)
    }

    fn leases(&self, namespace: &str) -> Api<Lease> {
        Api::namespaced(self.client.clone(), namespace)
    }

    /// Port of `handleExporterLeaseRelease` (`controller_service.go:392-435`).
    /// Returns a display error; the caller only logs it (never fails the RPC).
    async fn handle_exporter_lease_release(&self, exporter: &Exporter) -> Result<(), String> {
        let namespace = exporter.metadata.namespace.clone().unwrap_or_default();
        let exporter_name = exporter.metadata.name.clone().unwrap_or_default();

        let Some(lease_ref) = exporter.status.as_ref().and_then(|s| s.lease_ref.as_ref()) else {
            tracing::info!("No active lease to release for exporter");
            return Ok(());
        };

        let leases = self.leases(&namespace);
        let lease = leases
            .get(&lease_ref.name)
            .await
            .map_err(|err| format!("failed to get lease: {err}"))?;

        let held = lease
            .status
            .as_ref()
            .and_then(|s| s.exporter_ref.as_ref())
            .is_some_and(|r| r.name == exporter_name);
        if !held {
            return Err(format!(
                "lease {} is not held by exporter {}",
                lease.metadata.name.clone().unwrap_or_default(),
                exporter_name
            ));
        }

        let already = lease.status.as_ref().is_some_and(|s| s.ended) || lease.spec.release;
        if already {
            tracing::info!("Lease already ended or marked for release");
            return Ok(());
        }

        let patch = json!({ "spec": { "release": true } });
        leases
            .patch(
                &lease.metadata.name.clone().unwrap_or_default(),
                &PatchParams::default(),
                &Patch::Merge(&patch),
            )
            .await
            .map_err(|err| format!("failed to mark lease for release: {err}"))?;

        tracing::info!(lease = %lease_ref.name, exporter = %exporter_name, "Lease marked for release by exporter");
        Ok(())
    }
}

/// The controller-side status gate translation of the proto `ExporterStatus`
/// enum to the CRD [`ExporterStatusValue`], a port of `protoStatusToString`
/// (`controller_service.go:459-482`). Unknown values fall back to `Unspecified`.
fn proto_status_to_value(status: i32) -> ExporterStatusValue {
    match pb::ExporterStatus::try_from(status) {
        Ok(pb::ExporterStatus::Offline) => ExporterStatusValue::Offline,
        Ok(pb::ExporterStatus::Available) => ExporterStatusValue::Available,
        Ok(pb::ExporterStatus::BeforeLeaseHook) => ExporterStatusValue::BeforeLeaseHook,
        Ok(pb::ExporterStatus::LeaseReady) => ExporterStatusValue::LeaseReady,
        Ok(pb::ExporterStatus::AfterLeaseHook) => ExporterStatusValue::AfterLeaseHook,
        Ok(pb::ExporterStatus::BeforeLeaseHookFailed) => ExporterStatusValue::BeforeLeaseHookFailed,
        Ok(pb::ExporterStatus::AfterLeaseHookFailed) => ExporterStatusValue::AfterLeaseHookFailed,
        Ok(pb::ExporterStatus::Unspecified) | Err(_) => ExporterStatusValue::Unspecified,
    }
}

/// Whole-second "now" as both a jiff [`Timestamp`] and a wrapped [`Time`],
/// matching `metav1.Time`'s second-precision RFC3339 marshaling.
fn now_seconds() -> (Timestamp, Time) {
    let secs = chrono::Utc::now().timestamp();
    let ts = Timestamp::from_second(secs).expect("current unix seconds in range");
    (ts, Time(ts))
}

/// Convert a `metav1.Time` to a protobuf `Timestamp` (used by the legacy lease
/// RPCs, `controller_service.go:927-933`).
fn to_prost_timestamp(time: &Time) -> prost_types::Timestamp {
    prost_types::Timestamp {
        seconds: time.0.as_second(),
        nanos: time.0.subsec_nanosecond(),
    }
}

/// Convert a `metav1.Time` to the proto `pb::Time` used in condition messages
/// (`controller_service.go:952-955`).
fn to_pb_time(time: &Time) -> pb::Time {
    pb::Time {
        seconds: Some(time.0.as_second()),
        nanos: Some(time.0.subsec_nanosecond()),
    }
}

/// Convert a CRD [`LabelSelector`] to the proto `pb::LabelSelector`
/// (`controller_service.go:917-925, 963`).
fn selector_to_pb(selector: &LabelSelector) -> pb::LabelSelector {
    let match_expressions = selector
        .match_expressions
        .clone()
        .unwrap_or_default()
        .into_iter()
        .map(|e| pb::LabelSelectorRequirement {
            key: e.key,
            operator: e.operator,
            values: e.values.unwrap_or_default(),
        })
        .collect();
    let match_labels = selector
        .match_labels
        .clone()
        .unwrap_or_default()
        .into_iter()
        .collect();
    pb::LabelSelector {
        match_expressions,
        match_labels,
    }
}

/// Convert a proto `pb::LabelSelector` to a CRD [`LabelSelector`]
/// (`RequestLease`, `controller_service.go:986-996`).
fn selector_from_pb(selector: Option<&pb::LabelSelector>) -> LabelSelector {
    let Some(selector) = selector else {
        return LabelSelector::default();
    };
    let match_expressions: Vec<LabelSelectorRequirement> = selector
        .match_expressions
        .iter()
        .map(|e| LabelSelectorRequirement {
            key: e.key.clone(),
            operator: e.operator.clone(),
            values: if e.values.is_empty() {
                None
            } else {
                Some(e.values.clone())
            },
        })
        .collect();
    LabelSelector {
        match_expressions: (!match_expressions.is_empty()).then_some(match_expressions),
        match_labels: (!selector.match_labels.is_empty())
            .then(|| selector.match_labels.clone().into_iter().collect()),
    }
}

/// `durationpb.New(metav1.Duration)` (`controller_service.go:970`): split the Go
/// nanosecond count into `(seconds, nanos)`.
fn go_duration_to_prost(duration: GoDuration) -> prost_types::Duration {
    let nanos = duration.nanos();
    prost_types::Duration {
        seconds: nanos / 1_000_000_000,
        nanos: (nanos % 1_000_000_000) as i32,
    }
}

#[tonic::async_trait]
impl ControllerServiceTrait for ControllerService {
    // -- Register (controller_service.go:242-301) --------------------------
    async fn register(
        &self,
        request: Request<pb::RegisterRequest>,
    ) -> Result<Response<pb::RegisterResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let exporter = self.auth.verify_exporter(&metadata).await?;
        let namespace = exporter.metadata.namespace.clone().unwrap_or_default();
        let name = exporter.metadata.name.clone().unwrap_or_default();
        tracing::info!(exporter = %format_args!("{namespace}/{name}"), "Registering exporter");
        let api = self.exporters(&namespace);

        // Merge-patch the `jumpstarter.dev/`-prefixed labels: null out the ones
        // currently present, then set the ones the exporter reports. This is the
        // net effect of Go deleting all managed labels then re-adding the
        // request's, expressed as one JSON merge patch on `metadata.labels` so
        // non-managed labels are untouched.
        let existing = exporter.metadata.labels.clone().unwrap_or_default();
        let mut label_patch = serde_json::Map::new();
        for key in existing.keys() {
            if key.starts_with(MANAGED_LABEL_PREFIX) {
                label_patch.insert(key.clone(), Value::Null);
            }
        }
        for (key, value) in &req.labels {
            if key.starts_with(MANAGED_LABEL_PREFIX) {
                label_patch.insert(key.clone(), Value::String(value.clone()));
            }
        }
        let meta_patch = json!({ "metadata": { "labels": Value::Object(label_patch) } });
        api.patch(&name, &PatchParams::default(), &Patch::Merge(&meta_patch))
            .await
            .map_err(|err| Status::internal(format!("unable to update exporter: {err}")))?;

        // Status subresource: replace the reported device list.
        let devices: Vec<Device> = req
            .reports
            .iter()
            .map(|report| Device {
                uuid: (!report.uuid.is_empty()).then(|| report.uuid.clone()),
                parent_uuid: report.parent_uuid.clone().filter(|value| !value.is_empty()),
                labels: (!report.labels.is_empty())
                    .then(|| report.labels.clone().into_iter().collect()),
            })
            .collect();
        let status_patch = json!({ "status": { "devices": devices } });
        api.patch_status(&name, &PatchParams::default(), &Patch::Merge(&status_patch))
            .await
            .map_err(|err| Status::internal(format!("unable to update exporter status: {err}")))?;

        Ok(Response::new(pb::RegisterResponse {
            uuid: exporter.metadata.uid.clone().unwrap_or_default(),
        }))
    }

    // -- Unregister (controller_service.go:303-334) ------------------------
    async fn unregister(
        &self,
        request: Request<pb::UnregisterRequest>,
    ) -> Result<Response<pb::UnregisterResponse>, Status> {
        let (metadata, _ext, _req) = request.into_parts();
        let exporter = self.auth.verify_exporter(&metadata).await?;
        let namespace = exporter.metadata.namespace.clone().unwrap_or_default();
        let name = exporter.metadata.name.clone().unwrap_or_default();

        // Go sets Status.Devices = nil then Status().Patch → devices: null.
        let patch = json!({ "status": { "devices": Value::Null } });
        self.exporters(&namespace)
            .patch_status(&name, &PatchParams::default(), &Patch::Merge(&patch))
            .await
            .map_err(|err| Status::internal(format!("unable to update exporter status: {err}")))?;

        tracing::info!(exporter = %format_args!("{namespace}/{name}"), "exporter unregistered, updated as unavailable");
        Ok(Response::new(pb::UnregisterResponse {}))
    }

    // -- ReportStatus (controller_service.go:336-390) ----------------------
    async fn report_status(
        &self,
        request: Request<pb::ReportStatusRequest>,
    ) -> Result<Response<pb::ReportStatusResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let exporter = self.auth.verify_exporter(&metadata).await?;
        let namespace = exporter.metadata.namespace.clone().unwrap_or_default();
        let name = exporter.metadata.name.clone().unwrap_or_default();

        let status_value = proto_status_to_value(req.status);
        let message = req.message.clone().unwrap_or_default();
        tracing::info!(exporter = %format_args!("{namespace}/{name}"), state = %status_value, "Exporter reporting status");

        let (now_ts, now_time) = now_seconds();

        // Sync the deprecated Online condition with the reported status
        // (`syncOnlineConditionWithStatus`, controller_service.go:484-508).
        let generation = exporter.metadata.generation.unwrap_or(0);
        let online = status_value != ExporterStatusValue::Offline
            && status_value != ExporterStatusValue::Unspecified;
        let mut conditions = exporter
            .status
            .as_ref()
            .and_then(|s| s.conditions.clone())
            .unwrap_or_default();
        let new_condition = if online {
            condition(
                EXPORTER_CONDITION_TYPE_ONLINE,
                true,
                generation,
                "StatusReported",
                &format!("Exporter reported status: {status_value}"),
            )
        } else {
            condition(
                EXPORTER_CONDITION_TYPE_ONLINE,
                false,
                generation,
                "Offline",
                &message,
            )
        };
        set_status_condition(&mut conditions, new_condition, now_ts);

        let patch = json!({
            "status": {
                "exporterStatus": status_value.as_str(),
                "statusMessage": message,
                "lastSeen": now_time,
                "conditions": conditions,
            }
        });
        self.exporters(&namespace)
            .patch_status(&name, &PatchParams::default(), &Patch::Merge(&patch))
            .await
            .map_err(|err| Status::internal(format!("unable to update exporter status: {err}")))?;

        // Optional lease release side effect — logged, never fails the RPC.
        if req.release_lease == Some(true) {
            if let Err(err) = self.handle_exporter_lease_release(&exporter).await {
                tracing::error!(error = %err, "failed to release lease for exporter");
            }
        }

        Ok(Response::new(pb::ReportStatusResponse {}))
    }

    // -- Listen (controller_service.go:521-608) ----------------------------
    type ListenStream = Pin<Box<dyn Stream<Item = Result<pb::ListenResponse, Status>> + Send>>;

    async fn listen(
        &self,
        request: Request<pb::ListenRequest>,
    ) -> Result<Response<Self::ListenStream>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let exporter = self.auth.verify_exporter(&metadata).await?;
        let namespace = exporter.metadata.namespace.clone().unwrap_or_default();
        let exporter_name = exporter.metadata.name.clone().unwrap_or_default();

        let lease_name = req.lease_name;
        if lease_name.is_empty() {
            // Go: fmt.Errorf("empty lease name") → UNKNOWN.
            return Err(errors::empty_lease_name());
        }

        let lease = self
            .leases(&namespace)
            .get(&lease_name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;
        let held = lease
            .status
            .as_ref()
            .and_then(|s| s.exporter_ref.as_ref())
            .is_some_and(|r| r.name == exporter_name);
        if !held {
            // Go: fmt.Errorf("permission denied") → UNKNOWN.
            return Err(errors::permission_denied_transferred());
        }

        // Register a fresh queue (superseding any prior listener) and drive the
        // supersede/drain loop on its own task, feeding the response stream.
        let registration = self.registry.register(&lease_name);
        let (out_tx, out_rx) = mpsc::channel(8);
        let registry = self.registry.clone();
        tokio::spawn(async move {
            drive_listen_loop(&registry, &lease_name, registration, out_tx).await;
        });

        let stream: Self::ListenStream = Box::pin(ReceiverStream::new(out_rx));
        Ok(Response::new(stream))
    }

    // -- Status (controller_service.go:610-743, ported to status_stream.rs) -
    type StatusStream = Pin<Box<dyn Stream<Item = Result<pb::StatusResponse, Status>> + Send>>;

    async fn status(
        &self,
        request: Request<pb::StatusRequest>,
    ) -> Result<Response<Self::StatusStream>, Status> {
        let (metadata, _ext, _req) = request.into_parts();
        let exporter = self.auth.verify_exporter(&metadata).await?;
        let namespace = exporter.metadata.namespace.clone().unwrap_or_default();
        let name = exporter.metadata.name.clone().unwrap_or_default();

        let exporters = self.exporters(&namespace);
        let leases = self.leases(&namespace);
        let (out_tx, out_rx) = mpsc::channel(8);
        // A clone survives only to deliver the terminal error; `outbound.closed()`
        // inside `run_status_stream` still fires on receiver drop, so client-gone
        // detection is unaffected by holding it.
        let err_tx = out_tx.clone();
        tokio::spawn(async move {
            if let Err(status) = run_status_stream(exporters, leases, &name, out_tx).await {
                let _ = err_tx.send(Err(status)).await;
            }
        });

        let stream: Self::StatusStream = Box::pin(ReceiverStream::new(out_rx));
        Ok(Response::new(stream))
    }

    // -- Dial (controller_service.go:745-895, ported to dial.rs) -----------
    async fn dial(
        &self,
        request: Request<pb::DialRequest>,
    ) -> Result<Response<pb::DialResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        // Go threads the request context into the status-gate retry so a client
        // disconnect aborts it; tonic does not surface per-call cancellation
        // here, so the gate is bounded only by its 30 s budget (documented
        // divergence).
        let now = chrono::Utc::now().timestamp();
        let response = dial::dial(
            &metadata,
            &req,
            &*self.auth,
            &*self.auth,
            &self.router,
            &self.router_key,
            &*self.registry,
            now,
            None,
        )
        .await?;
        Ok(Response::new(response))
    }

    // -- AuditStream: UNIMPLEMENTED (kept in the descriptor) ---------------
    async fn audit_stream(
        &self,
        _request: Request<Streaming<pb::AuditStreamRequest>>,
    ) -> Result<Response<()>, Status> {
        Err(Status::unimplemented("method AuditStream not implemented"))
    }

    // -- Legacy lease RPCs (controller_service.go:897-1090) ----------------
    async fn get_lease(
        &self,
        request: Request<pb::GetLeaseRequest>,
    ) -> Result<Response<pb::GetLeaseResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let client = self.auth.verify_client(&metadata).await?;
        let namespace = client.metadata.namespace.clone().unwrap_or_default();
        let client_name = client.metadata.name.clone().unwrap_or_default();

        let lease = self
            .leases(&namespace)
            .get(&req.name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        if lease.spec.client_ref.name != client_name {
            // Go: fmt.Errorf("GetLease permission denied") → UNKNOWN.
            return Err(Status::unknown("GetLease permission denied"));
        }

        let begin_time = lease
            .status
            .as_ref()
            .and_then(|s| s.begin_time.as_ref())
            .map(to_prost_timestamp);
        let end_time = lease
            .status
            .as_ref()
            .and_then(|s| s.end_time.as_ref())
            .map(to_prost_timestamp);

        let exporter_uuid = match lease.status.as_ref().and_then(|s| s.exporter_ref.as_ref()) {
            Some(reference) => {
                let exporter = self
                    .exporters(&namespace)
                    .get(&reference.name)
                    .await
                    // Go: fmt.Errorf("GetLease fetch exporter uuid failed").
                    .map_err(|_| Status::unknown("GetLease fetch exporter uuid failed"))?;
                Some(exporter.metadata.uid.clone().unwrap_or_default())
            }
            None => None,
        };

        let conditions = lease
            .status
            .as_ref()
            .map(|s| s.conditions.as_slice())
            .unwrap_or_default()
            .iter()
            .map(|c| pb::Condition {
                r#type: Some(c.type_.clone()),
                status: Some(c.status.clone()),
                observed_generation: Some(c.observed_generation.unwrap_or(0)),
                last_transition_time: Some(to_pb_time(&c.last_transition_time)),
                reason: Some(c.reason.clone()),
                message: Some(c.message.clone()),
            })
            .collect();

        let duration = lease.spec.duration.map(go_duration_to_prost);

        Ok(Response::new(pb::GetLeaseResponse {
            duration,
            selector: Some(selector_to_pb(&lease.spec.selector)),
            begin_time,
            end_time,
            exporter_uuid,
            conditions,
        }))
    }

    async fn request_lease(
        &self,
        request: Request<pb::RequestLeaseRequest>,
    ) -> Result<Response<pb::RequestLeaseResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let client = self.auth.verify_client(&metadata).await?;
        let namespace = client.metadata.namespace.clone().unwrap_or_default();
        let client_name = client.metadata.name.clone().unwrap_or_default();

        let lease_name = uuid::Uuid::now_v7().to_string();
        let mut lease = Lease::new(
            &lease_name,
            LeaseSpec {
                client_ref: LocalObjectReference { name: client_name },
                selector: selector_from_pb(req.selector.as_ref()),
                duration: req.duration.map(|d| {
                    GoDuration::from_nanos(d.seconds * 1_000_000_000 + i64::from(d.nanos))
                }),
                ..Default::default()
            },
        );
        lease.metadata.namespace = Some(namespace.clone());

        self.leases(&namespace)
            .create(&PostParams::default(), &lease)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        Ok(Response::new(pb::RequestLeaseResponse { name: lease_name }))
    }

    async fn release_lease(
        &self,
        request: Request<pb::ReleaseLeaseRequest>,
    ) -> Result<Response<pb::ReleaseLeaseResponse>, Status> {
        let (metadata, _ext, req) = request.into_parts();
        let client = self.auth.verify_client(&metadata).await?;
        let namespace = client.metadata.namespace.clone().unwrap_or_default();
        let client_name = client.metadata.name.clone().unwrap_or_default();

        let leases = self.leases(&namespace);
        let lease = leases
            .get(&req.name)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        if lease.spec.client_ref.name != client_name {
            // Go: fmt.Errorf("ReleaseLease permission denied") → UNKNOWN.
            return Err(Status::unknown("ReleaseLease permission denied"));
        }

        let patch = json!({ "spec": { "release": true } });
        leases
            .patch(&req.name, &PatchParams::default(), &Patch::Merge(&patch))
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        Ok(Response::new(pb::ReleaseLeaseResponse {}))
    }

    async fn list_leases(
        &self,
        request: Request<pb::ListLeasesRequest>,
    ) -> Result<Response<pb::ListLeasesResponse>, Status> {
        let (metadata, _ext, _req) = request.into_parts();
        let client = self.auth.verify_client(&metadata).await?;
        let namespace = client.metadata.namespace.clone().unwrap_or_default();
        let client_name = client.metadata.name.clone().unwrap_or_default();

        // `controller.MatchingActiveLeases()`: leases without the ended label.
        let params = ListParams::default().labels(&format!("!{LEASE_LABEL_ENDED}"));
        let list = self
            .leases(&namespace)
            .list(&params)
            .await
            .map_err(|err| errors::forward_apiserver_error(&err))?;

        let names = list
            .items
            .iter()
            .filter(|lease| lease.spec.client_ref.name == client_name)
            .map(|lease| lease.metadata.name.clone().unwrap_or_default())
            .collect();

        Ok(Response::new(pb::ListLeasesResponse { names }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn proto_status_maps_every_variant() {
        use ExporterStatusValue as V;
        assert_eq!(proto_status_to_value(0), V::Unspecified);
        assert_eq!(proto_status_to_value(1), V::Offline);
        assert_eq!(proto_status_to_value(2), V::Available);
        assert_eq!(proto_status_to_value(3), V::BeforeLeaseHook);
        assert_eq!(proto_status_to_value(4), V::LeaseReady);
        assert_eq!(proto_status_to_value(5), V::AfterLeaseHook);
        assert_eq!(proto_status_to_value(6), V::BeforeLeaseHookFailed);
        assert_eq!(proto_status_to_value(7), V::AfterLeaseHookFailed);
        // Unknown enum value → Unspecified (Go default arm).
        assert_eq!(proto_status_to_value(99), V::Unspecified);
    }

    #[test]
    fn go_duration_to_prost_splits_seconds_and_nanos() {
        let d = go_duration_to_prost(GoDuration::from_nanos(1_500_000_000));
        assert_eq!(d.seconds, 1);
        assert_eq!(d.nanos, 500_000_000);
    }

    #[test]
    fn selector_round_trips_labels_and_expressions() {
        let mut pb_sel = pb::LabelSelector::default();
        pb_sel.match_labels.insert("a".into(), "1".into());
        pb_sel.match_expressions.push(pb::LabelSelectorRequirement {
            key: "k".into(),
            operator: "In".into(),
            values: vec!["x".into(), "y".into()],
        });
        let crd = selector_from_pb(Some(&pb_sel));
        assert_eq!(
            crd.match_labels
                .as_ref()
                .and_then(|m| m.get("a"))
                .map(String::as_str),
            Some("1")
        );
        let back = selector_to_pb(&crd);
        assert_eq!(back.match_labels.get("a").map(String::as_str), Some("1"));
        assert_eq!(back.match_expressions.len(), 1);
        assert_eq!(back.match_expressions[0].key, "k");
        assert_eq!(back.match_expressions[0].values, vec!["x", "y"]);
    }

    #[test]
    fn empty_selector_from_none_is_default() {
        let crd = selector_from_pb(None);
        assert!(crd.match_labels.is_none());
        assert!(crd.match_expressions.is_none());
    }
}
