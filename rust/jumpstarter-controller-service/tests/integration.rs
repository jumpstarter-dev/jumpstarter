//! End-to-end integration tests driving the **real** `ControllerService` and
//! `ClientService` gRPC implementations against a real kube-apiserver spawned by
//! the envtest harness in [`common`] (the same harness Phase 4 uses for the
//! reconcilers).
//!
//! Env-gated: the whole suite no-ops (prints SKIP) unless `KUBEBUILDER_ASSETS`
//! points at the envtest binaries, so `cargo test` stays hermetic. Run it with:
//!
//! ```sh
//! KUBEBUILDER_ASSETS=.../bin/k8s/1.30.0-darwin-arm64 cargo test -p \
//!   jumpstarter-controller-service --test integration -- --nocapture
//! ```
//!
//! The services are constructed over the envtest client with a test
//! `Signer`/`TokenValidator`/`ListenRegistry`, and driven **in-process** through
//! their generated tonic trait methods (`ControllerServiceTrait` /
//! `ClientServiceTrait`) — a `tonic::Request` carrying real bearer + attribute
//! metadata, exactly what the transport layer would deliver. This exercises the
//! full per-call authenticate → authorize → CR read/write pipeline against the
//! apiserver, minus only the TLS/HTTP2 framing. Every RPC is bounded by a
//! deadline so a wedged apiserver surfaces as a diagnosable failure, never a
//! hang.

mod common;

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use futures::StreamExt;
use k8s_openapi::api::core::v1::LocalObjectReference;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{LabelSelector, ObjectMeta};
use kube::api::{Api, Patch, PatchParams, PostParams};
use kube::{Client, ResourceExt};
use serde_json::json;
use tonic::metadata::MetadataMap;
use tonic::{Code, Request};

use jumpstarter_controller_api::client::{Client as ClientCr, ClientSpec};
use jumpstarter_controller_api::exporter::{Exporter, ExporterSpec};
use jumpstarter_controller_api::lease::{Lease, LeaseSpec};
use jumpstarter_controller_auth::signer::{Signer, INTERNAL_AUDIENCE, INTERNAL_ISSUER};
use jumpstarter_controller_auth::validator::TokenValidator;
use jumpstarter_controller_config::router::{Router, RouterEntry};
use jumpstarter_controller_config::types::Authentication;

use jumpstarter_controller_service::client_service::ClientService;
use jumpstarter_controller_service::controller_service::{ControllerAuth, ControllerService};
use jumpstarter_controller_service::listen_registry::ListenRegistry;

use jumpstarter_protocol::client_v1 as cpb;
use jumpstarter_protocol::client_v1::client_service_server::ClientService as ClientServiceTrait;
use jumpstarter_protocol::v1 as pb;
use jumpstarter_protocol::v1::controller_service_server::ControllerService as ControllerServiceTrait;

use common::TestEnv;

type R = Result<(), String>;

/// Every RPC is wrapped in this deadline (ANTI-STALL). The Dial retry gate has a
/// 30 s internal budget, so the retry scenario overrides it explicitly.
const RPC_DEADLINE: Duration = Duration::from_secs(10);

// ---------------------------------------------------------------------------
// Service wiring
// ---------------------------------------------------------------------------

/// The constructed service surface shared by every scenario. `controller` and
/// `client_svc` are behind `Arc` so a scenario can `tokio::join!` a Dial with a
/// concurrent status patch (both take `&self`).
struct Services {
    client: Client,
    signer: Arc<Signer>,
    controller: Arc<ControllerService>,
    client_svc: Arc<ClientService<Arc<ControllerAuth>>>,
}

impl Services {
    fn new(kube: Client) -> Self {
        // Internal signer whose issuer/audience match the internal-token
        // validator, so minted tokens route to the in-process authenticator.
        let signer = Arc::new(
            Signer::from_seed(b"integration-test-key", INTERNAL_ISSUER, INTERNAL_AUDIENCE)
                .expect("signer"),
        );
        // Default Authentication → internal prefix "internal:", no external
        // issuers, provisioning off (Clients/Exporters must already exist).
        let validator = Arc::new(
            TokenValidator::load(&Authentication::default(), signer.clone()).expect("validator"),
        );
        let auth = Arc::new(ControllerAuth::new(
            kube.clone(),
            validator.clone(),
            validator.internal_prefix().to_string(),
            false,
        ));
        let registry = Arc::new(ListenRegistry::new());

        // One router with no labels → matches every exporter (score 0).
        let mut router: Router = BTreeMap::new();
        router.insert(
            "router-0".to_string(),
            RouterEntry {
                endpoint: "grpc://router-0.jumpstarter.example:443".to_string(),
                labels: BTreeMap::new(),
            },
        );

        let controller = Arc::new(ControllerService::new(
            kube.clone(),
            auth.clone(),
            registry.clone(),
            router.clone(),
            b"integration-router-key".to_vec(),
        ));
        let client_svc = Arc::new(ClientService::new(
            kube.clone(),
            auth.clone(),
            10,
            signer.clone(),
        ));

        Self {
            client: kube,
            signer,
            controller,
            client_svc,
        }
    }
}

// ---------------------------------------------------------------------------
// CR + token helpers
// ---------------------------------------------------------------------------

fn labels(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn match_labels_selector(pairs: &[(&str, &str)]) -> LabelSelector {
    LabelSelector {
        match_labels: Some(labels(pairs)),
        match_expressions: None,
    }
}

/// Attach bearer + jumpstarter attribute metadata to a request.
fn attach_metadata(md: &mut MetadataMap, token: &str, ns: &str, kind: &str, name: &str) {
    md.insert("authorization", format!("Bearer {token}").parse().unwrap());
    md.insert("jumpstarter-namespace", ns.parse().unwrap());
    md.insert("jumpstarter-kind", kind.parse().unwrap());
    md.insert("jumpstarter-name", name.parse().unwrap());
}

fn request<T>(payload: T, token: &str, ns: &str, kind: &str, name: &str) -> Request<T> {
    let mut req = Request::new(payload);
    attach_metadata(req.metadata_mut(), token, ns, kind, name);
    req
}

/// Create an Exporter CR and mint a valid internal token for it (the token's
/// `sub` is the exporter's `internal_subject()`, which the authorizer matches
/// against `exporter.usernames("internal:")`).
async fn make_exporter(
    svc: &Services,
    ns: &str,
    name: &str,
    lbls: &[(&str, &str)],
) -> Result<(Exporter, String), String> {
    let api: Api<Exporter> = Api::namespaced(svc.client.clone(), ns);
    let exp = Exporter {
        metadata: ObjectMeta {
            name: Some(name.to_string()),
            namespace: Some(ns.to_string()),
            labels: Some(labels(lbls)),
            ..Default::default()
        },
        spec: ExporterSpec::default(),
        status: None,
    };
    let created = api
        .create(&PostParams::default(), &exp)
        .await
        .map_err(|e| format!("create exporter {name}: {e}"))?;
    let token = svc
        .signer
        .token(&created.internal_subject())
        .map_err(|e| format!("mint exporter token: {e}"))?;
    Ok((created, token))
}

/// Create a Client CR and mint a valid internal token for it.
async fn make_client(svc: &Services, ns: &str, name: &str) -> Result<(ClientCr, String), String> {
    let api: Api<ClientCr> = Api::namespaced(svc.client.clone(), ns);
    let c = ClientCr {
        metadata: ObjectMeta {
            name: Some(name.to_string()),
            namespace: Some(ns.to_string()),
            ..Default::default()
        },
        spec: ClientSpec::default(),
        status: None,
    };
    let created = api
        .create(&PostParams::default(), &c)
        .await
        .map_err(|e| format!("create client {name}: {e}"))?;
    let token = svc
        .signer
        .token(&created.internal_subject())
        .map_err(|e| format!("mint client token: {e}"))?;
    Ok((created, token))
}

/// Set an exporter's `status.exporterStatus` (JSON merge patch on the status
/// subresource, so unrelated status is preserved).
async fn set_exporter_status(svc: &Services, ns: &str, name: &str, status: &str) -> R {
    Api::<Exporter>::namespaced(svc.client.clone(), ns)
        .patch_status(
            name,
            &PatchParams::default(),
            &Patch::Merge(json!({ "status": { "exporterStatus": status } })),
        )
        .await
        .map(|_| ())
        .map_err(|e| format!("set exporter status {name}={status}: {e}"))
}

/// Create a Lease CR (main resource) then stamp `status.exporterRef.name` on the
/// status subresource so it reads as assigned to `exporter_name`.
async fn create_assigned_lease(
    svc: &Services,
    ns: &str,
    name: &str,
    client_name: &str,
    exporter_name: &str,
    selector: &[(&str, &str)],
) -> R {
    let api: Api<Lease> = Api::namespaced(svc.client.clone(), ns);
    let mut lease = Lease::new(
        name,
        LeaseSpec {
            client_ref: LocalObjectReference {
                name: client_name.to_string(),
            },
            selector: match_labels_selector(selector),
            ..Default::default()
        },
    );
    lease.metadata.namespace = Some(ns.to_string());
    api.create(&PostParams::default(), &lease)
        .await
        .map_err(|e| format!("create lease {name}: {e}"))?;
    // `status.ended` is a required field on the Lease status subresource, so a
    // partial merge that omits it is rejected (422); include it explicitly.
    api.patch_status(
        name,
        &PatchParams::default(),
        &Patch::Merge(json!({
            "status": { "ended": false, "exporterRef": { "name": exporter_name } }
        })),
    )
    .await
    .map(|_| ())
    .map_err(|e| format!("stamp lease {name} exporterRef: {e}"))
}

fn want(cond: bool, msg: impl Into<String>) -> R {
    if cond {
        Ok(())
    } else {
        Err(msg.into())
    }
}

async fn deadline<F, T>(what: &str, fut: F) -> Result<T, String>
where
    F: std::future::Future<Output = T>,
{
    tokio::time::timeout(RPC_DEADLINE, fut)
        .await
        .map_err(|_| format!("{what}: timed out after {RPC_DEADLINE:?}"))
}

// ---------------------------------------------------------------------------
// scenarios: ControllerService
// ---------------------------------------------------------------------------

/// Register creates/updates the Exporter CR (labels + device reports) and
/// returns the exporter's UID.
async fn scenario_register(svc: &Services, ns: &str) -> R {
    let (exp, token) = make_exporter(svc, ns, "exp", &[]).await?;
    let uid = exp.uid().unwrap_or_default();

    let mut labels = std::collections::HashMap::new();
    labels.insert("jumpstarter.dev/board".to_string(), "rpi4".to_string());
    let req = request(
        pb::RegisterRequest {
            labels,
            reports: vec![pb::DriverInstanceReport {
                uuid: "dev-uuid-1".to_string(),
                ..Default::default()
            }],
        },
        &token,
        ns,
        "Exporter",
        "exp",
    );
    let resp = deadline("register", svc.controller.register(req))
        .await?
        .map_err(|s| format!("register rpc: {s}"))?
        .into_inner();
    want(
        resp.uuid == uid,
        format!("register uuid {} != exporter uid {}", resp.uuid, uid),
    )?;

    // The managed label was written and the device report landed on status.
    let after = Api::<Exporter>::namespaced(svc.client.clone(), ns)
        .get("exp")
        .await
        .map_err(|e| e.to_string())?;
    want(
        after
            .metadata
            .labels
            .as_ref()
            .and_then(|m| m.get("jumpstarter.dev/board"))
            .map(String::as_str)
            == Some("rpi4"),
        "managed label jumpstarter.dev/board not written",
    )?;
    want(
        after
            .status
            .as_ref()
            .and_then(|s| s.devices.as_ref())
            .map(|d| d.len())
            == Some(1),
        "device report not recorded on status",
    )?;
    Ok(())
}

/// ReportStatus updates exporterStatus + statusMessage + lastSeen.
async fn scenario_report_status(svc: &Services, ns: &str) -> R {
    let (_exp, token) = make_exporter(svc, ns, "exp", &[]).await?;
    let req = request(
        pb::ReportStatusRequest {
            status: pb::ExporterStatus::Available as i32,
            message: Some("all good".to_string()),
            release_lease: None,
        },
        &token,
        ns,
        "Exporter",
        "exp",
    );
    deadline("report_status", svc.controller.report_status(req))
        .await?
        .map_err(|s| format!("report_status rpc: {s}"))?;

    let after = Api::<Exporter>::namespaced(svc.client.clone(), ns)
        .get("exp")
        .await
        .map_err(|e| e.to_string())?;
    let status = after
        .status
        .as_ref()
        .ok_or("no status after ReportStatus")?;
    want(
        status.exporter_status.map(|v| v.as_str()) == Some("Available"),
        format!("exporterStatus != Available: {:?}", status.exporter_status),
    )?;
    want(status.last_seen.is_some(), "lastSeen not stamped")?;
    Ok(())
}

/// Listen + Dial handshake: the exporter opens Listen, the client Dials, and the
/// router `(endpoint, token)` pair pushed to the Listen stream is byte-identical
/// to the one returned to Dial.
async fn scenario_listen_dial_handshake(svc: &Services, ns: &str) -> R {
    let (_exp, exp_token) = make_exporter(svc, ns, "exp", &[("dut", "a")]).await?;
    let (_cli, cli_token) = make_client(svc, ns, "cli").await?;
    set_exporter_status(svc, ns, "exp", "LeaseReady").await?;
    create_assigned_lease(svc, ns, "lease", "cli", "exp", &[("dut", "a")]).await?;

    // Exporter opens Listen; the queue is registered synchronously before the
    // stream is returned.
    let listen_req = request(
        pb::ListenRequest {
            lease_name: "lease".to_string(),
        },
        &exp_token,
        ns,
        "Exporter",
        "exp",
    );
    let mut listen_stream = deadline("listen", svc.controller.listen(listen_req))
        .await?
        .map_err(|s| format!("listen rpc: {s}"))?
        .into_inner();

    // Client Dials.
    let dial_req = request(
        pb::DialRequest {
            lease_name: "lease".to_string(),
        },
        &cli_token,
        ns,
        "Client",
        "cli",
    );
    let dial = deadline("dial", svc.controller.dial(dial_req))
        .await?
        .map_err(|s| format!("dial rpc: {s}"))?
        .into_inner();

    want(!dial.router_token.is_empty(), "dial returned empty token")?;
    want(
        dial.router_endpoint == "grpc://router-0.jumpstarter.example:443",
        format!("dial endpoint mismatch: {}", dial.router_endpoint),
    )?;

    // The same pair must arrive on the Listen stream.
    let delivered = deadline("listen-recv", listen_stream.next())
        .await?
        .ok_or("Listen stream closed before delivering a token")?
        .map_err(|s| format!("listen stream error: {s}"))?;
    want(
        delivered.router_token == dial.router_token,
        "token delivered to Listen != token returned to Dial",
    )?;
    want(
        delivered.router_endpoint == dial.router_endpoint,
        "endpoint delivered to Listen != endpoint returned to Dial",
    )?;
    Ok(())
}

/// Dial retry gate: the exporter starts `Available` (transient) and flips to
/// `LeaseReady` mid-retry; Dial must poll through and succeed.
async fn scenario_dial_retry_gate(svc: &Services, ns: &str) -> R {
    let (_exp, exp_token) = make_exporter(svc, ns, "exp", &[("dut", "a")]).await?;
    let (_cli, cli_token) = make_client(svc, ns, "cli").await?;
    set_exporter_status(svc, ns, "exp", "Available").await?;
    create_assigned_lease(svc, ns, "lease", "cli", "exp", &[("dut", "a")]).await?;

    let listen_req = request(
        pb::ListenRequest {
            lease_name: "lease".to_string(),
        },
        &exp_token,
        ns,
        "Exporter",
        "exp",
    );
    let mut listen_stream = deadline("listen", svc.controller.listen(listen_req))
        .await?
        .map_err(|s| format!("listen rpc: {s}"))?
        .into_inner();

    let dial_req = request(
        pb::DialRequest {
            lease_name: "lease".to_string(),
        },
        &cli_token,
        ns,
        "Client",
        "cli",
    );

    // Drive the Dial concurrently with a delayed status flip. The gate's first
    // check sees Available and sleeps 500ms; the refetch after that sleep sees
    // LeaseReady and proceeds. `join!` keeps both futures on one task, so the
    // Dial's internal sleep yields to let the patch run. Whole thing is bounded.
    let controller = svc.controller.clone();
    let flip = async {
        tokio::time::sleep(Duration::from_millis(150)).await;
        set_exporter_status(svc, ns, "exp", "LeaseReady").await
    };
    let (dial_res, flip_res) = deadline("dial-retry", async move {
        tokio::join!(controller.dial(dial_req), flip)
    })
    .await?;
    flip_res?;
    let dial = dial_res
        .map_err(|s| format!("dial (retry gate) rpc: {s}"))?
        .into_inner();
    want(!dial.router_token.is_empty(), "retry-gate dial empty token")?;

    let delivered = deadline("listen-recv", listen_stream.next())
        .await?
        .ok_or("Listen stream closed before delivering a token (retry gate)")?
        .map_err(|s| format!("listen stream error: {s}"))?;
    want(
        delivered.router_token == dial.router_token,
        "retry-gate: token delivered to Listen != returned to Dial",
    )?;
    Ok(())
}

/// Error contract: Dial on a lease the caller does not own → UNKNOWN
/// "permission denied".
async fn scenario_dial_permission_denied(svc: &Services, ns: &str) -> R {
    let (_exp, _exp_token) = make_exporter(svc, ns, "exp", &[("dut", "a")]).await?;
    let (_cli, cli_token) = make_client(svc, ns, "cli").await?;
    set_exporter_status(svc, ns, "exp", "LeaseReady").await?;
    // Lease is owned by "other-client", not our caller.
    create_assigned_lease(svc, ns, "lease", "other-client", "exp", &[("dut", "a")]).await?;

    let dial_req = request(
        pb::DialRequest {
            lease_name: "lease".to_string(),
        },
        &cli_token,
        ns,
        "Client",
        "cli",
    );
    let err = deadline("dial-denied", svc.controller.dial(dial_req))
        .await?
        .err()
        .ok_or("Dial unexpectedly succeeded on a lease owned by another client")?;
    want(
        err.code() == Code::Unknown,
        format!("wrong code: {:?}", err.code()),
    )?;
    want(
        err.message() == "permission denied",
        format!("wrong message: {:?}", err.message()),
    )?;
    Ok(())
}

/// Error contract: Dial on a nonexistent lease → UNKNOWN with the verbatim
/// apiserver not-found text (never remapped to NOT_FOUND).
async fn scenario_dial_not_found(svc: &Services, ns: &str) -> R {
    let (_cli, cli_token) = make_client(svc, ns, "cli").await?;
    let dial_req = request(
        pb::DialRequest {
            lease_name: "ghost".to_string(),
        },
        &cli_token,
        ns,
        "Client",
        "cli",
    );
    let err = deadline("dial-notfound", svc.controller.dial(dial_req))
        .await?
        .err()
        .ok_or("Dial unexpectedly succeeded on a nonexistent lease")?;
    want(
        err.code() == Code::Unknown,
        format!(
            "not-found wrong code: {:?} (must not be NOT_FOUND)",
            err.code()
        ),
    )?;
    want(
        err.message().contains("not found"),
        format!(
            "not-found message not forwarded verbatim: {:?}",
            err.message()
        ),
    )?;
    Ok(())
}

// ---------------------------------------------------------------------------
// scenarios: ClientService
// ---------------------------------------------------------------------------

/// ClientService CreateLease → GetLease → DeleteLease against real CRs: the name
/// is a UUIDv7, GetLease round-trips it, and DeleteLease soft-deletes
/// (spec.release = true).
async fn scenario_client_service_lease_lifecycle(svc: &Services, ns: &str) -> R {
    let (_cli, cli_token) = make_client(svc, ns, "cli").await?;

    // -- CreateLease --------------------------------------------------------
    let create_req = request(
        cpb::CreateLeaseRequest {
            parent: format!("namespaces/{ns}"),
            lease_id: String::new(),
            lease: Some(cpb::Lease {
                selector: "dut=a".to_string(),
                duration: Some(prost_types::Duration {
                    seconds: 3600,
                    nanos: 0,
                }),
                ..Default::default()
            }),
        },
        &cli_token,
        ns,
        "Client",
        "cli",
    );
    let created = deadline("create_lease", svc.client_svc.create_lease(create_req))
        .await?
        .map_err(|s| format!("create_lease rpc: {s}"))?
        .into_inner();

    // name = namespaces/{ns}/leases/{uuidv7}
    let prefix = format!("namespaces/{ns}/leases/");
    want(
        created.name.starts_with(&prefix),
        format!("created lease name {:?} lacks AIP prefix", created.name),
    )?;
    let lease_id = created.name.trim_start_matches(&prefix).to_string();
    let parsed =
        uuid::Uuid::parse_str(&lease_id).map_err(|e| format!("lease id not a UUID: {e}"))?;
    want(
        parsed.get_version_num() == 7,
        format!(
            "lease id is not UUIDv7 (version {})",
            parsed.get_version_num()
        ),
    )?;
    // The client_ref was populated from the authenticated caller.
    let raw = Api::<Lease>::namespaced(svc.client.clone(), ns)
        .get(&lease_id)
        .await
        .map_err(|e| e.to_string())?;
    want(
        raw.spec.client_ref.name == "cli",
        format!("clientRef != cli: {}", raw.spec.client_ref.name),
    )?;

    // -- GetLease -----------------------------------------------------------
    let get_req = request(
        cpb::GetLeaseRequest {
            name: created.name.clone(),
        },
        &cli_token,
        ns,
        "Client",
        "cli",
    );
    let got = deadline("get_lease", svc.client_svc.get_lease(get_req))
        .await?
        .map_err(|s| format!("get_lease rpc: {s}"))?
        .into_inner();
    want(
        got.name == created.name,
        format!("GetLease name mismatch: {} != {}", got.name, created.name),
    )?;
    want(
        got.selector == "dut=a",
        format!("GetLease selector mismatch: {:?}", got.selector),
    )?;

    // -- DeleteLease (soft delete) -----------------------------------------
    let del_req = request(
        cpb::DeleteLeaseRequest {
            name: created.name.clone(),
        },
        &cli_token,
        ns,
        "Client",
        "cli",
    );
    deadline("delete_lease", svc.client_svc.delete_lease(del_req))
        .await?
        .map_err(|s| format!("delete_lease rpc: {s}"))?;

    // The CR still exists; spec.release is set (soft delete, not a hard delete).
    let after = Api::<Lease>::namespaced(svc.client.clone(), ns)
        .get(&lease_id)
        .await
        .map_err(|e| format!("lease should still exist after soft delete: {e}"))?;
    want(after.spec.release, "spec.release not set by DeleteLease")?;
    Ok(())
}

// ---------------------------------------------------------------------------
// driver
// ---------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn envtest_grpc_services() {
    if common::assets().is_none() {
        eprintln!("SKIP: KUBEBUILDER_ASSETS not set — hermetic run, skipping envtest suite");
        return;
    }

    let env = TestEnv::start().await.expect("start envtest control plane");
    let svc = Services::new(env.client.clone());

    let mut results: Vec<(&str, R)> = Vec::new();

    macro_rules! run {
        ($name:literal, $ns:literal, $f:ident) => {{
            let r = match env.create_namespace($ns).await {
                Ok(()) => $f(&svc, $ns).await,
                Err(e) => Err(format!("create namespace: {e}")),
            };
            results.push(($name, r));
        }};
    }

    run!("register", "g01", scenario_register);
    run!("report_status", "g02", scenario_report_status);
    run!(
        "listen_dial_handshake",
        "g03",
        scenario_listen_dial_handshake
    );
    run!("dial_retry_gate", "g04", scenario_dial_retry_gate);
    run!(
        "dial_permission_denied",
        "g05",
        scenario_dial_permission_denied
    );
    run!("dial_not_found", "g06", scenario_dial_not_found);
    run!(
        "client_service_lease_lifecycle",
        "g07",
        scenario_client_service_lease_lifecycle
    );

    eprintln!("\n============= envtest gRPC-service scenario results =============");
    let mut failures = 0;
    for (name, r) in &results {
        match r {
            Ok(()) => eprintln!("  PASS  {name}"),
            Err(e) => {
                failures += 1;
                eprintln!("  FAIL  {name}: {e}");
            }
        }
    }
    eprintln!(
        "========== {}/{} passed ==========\n",
        results.len() - failures,
        results.len()
    );

    assert_eq!(
        failures, 0,
        "{failures} envtest gRPC-service scenario(s) failed"
    );
}
