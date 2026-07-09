//! End-to-end lease-lifecycle integration tests driving the reconcilers against
//! a **real** kube-apiserver spawned by the envtest harness in [`common`].
//!
//! Env-gated: the whole suite no-ops (prints SKIP) unless `KUBEBUILDER_ASSETS`
//! points at the envtest binaries, so `cargo test` stays hermetic. Run it with:
//!
//! ```sh
//! KUBEBUILDER_ASSETS=.../bin/k8s/1.30.0-darwin-arm64 cargo test -p \
//!   jumpstarter-controller-core --test integration -- --nocapture
//! ```
//!
//! The scenarios are transliterated from the highest-value cases in
//! `controller/internal/controller/lease_controller_test.go` (+ the exporter and
//! client controller suites). The reconcilers are driven directly (one
//! `reconcile()` call per step, level-triggered — the same object is re-fetched
//! and re-reconciled between steps), and the resulting CR status
//! (conditions/exporterRef/times/secrets) is asserted against Go behavior.
//!
//! Each scenario runs in its own namespace because the lease scheduler reads
//! *all* policies and active leases in a namespace.

mod common;

use std::sync::Arc;
use std::time::Duration;

use k8s_openapi::api::core::v1::{LocalObjectReference, Secret};
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{Condition, LabelSelector, ObjectMeta, Time};
use k8s_openapi::jiff::Timestamp;
use kube::api::{Api, PostParams};
use kube::runtime::events::{Recorder, Reporter};
use kube::{Client, ResourceExt};

use jumpstarter_controller_api::access_policy::{
    ExporterAccessPolicy, ExporterAccessPolicySpec, From as PolicyFrom, Policy,
};
use jumpstarter_controller_api::client::{Client as ClientCr, ClientSpec};
use jumpstarter_controller_api::conditions::{
    EXPORTER_CONDITION_TYPE_ONLINE, EXPORTER_CONDITION_TYPE_REGISTERED,
    LEASE_CONDITION_TYPE_PENDING, LEASE_CONDITION_TYPE_READY, LEASE_CONDITION_TYPE_UNSATISFIABLE,
};
use jumpstarter_controller_api::device::Device;
use jumpstarter_controller_api::exporter::{
    Exporter, ExporterSpec, ExporterStatus, ExporterStatusValue,
};
use jumpstarter_controller_api::go_duration::{GoDuration, SECOND};
use jumpstarter_controller_api::labels::LEASE_LABEL_ENDED;
use jumpstarter_controller_api::lease::{Lease, LeaseSpec};
use jumpstarter_controller_auth::signer::Signer;
use jumpstarter_controller_core::scheduler::decide::{
    REASON_EXPORTER_NOT_FOUND, REASON_NOT_AVAILABLE, REASON_NO_ACCESS, REASON_OFFLINE,
};
use jumpstarter_controller_core::{client_reconciler, exporter_reconciler, lease_reconciler};

use common::TestEnv;

type R = Result<(), String>;

// --------------------------------------------------------------------------
// helpers
// --------------------------------------------------------------------------

fn signer() -> Arc<Signer> {
    // Mirrors the Go suite: oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy").
    Arc::new(Signer::from_seed(b"", "https://example.com", "dummy").expect("signer"))
}

fn labels(pairs: &[(&str, &str)]) -> std::collections::BTreeMap<String, String> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn selector(pairs: &[(&str, &str)]) -> LabelSelector {
    LabelSelector {
        match_labels: Some(labels(pairs)),
        match_expressions: None,
    }
}

fn true_cond(type_: &str) -> Condition {
    Condition {
        type_: type_.to_string(),
        status: "True".to_string(),
        observed_generation: None,
        last_transition_time: Time(Timestamp::now()),
        reason: "Test".to_string(),
        message: String::new(),
    }
}

fn exporter_ctx(client: &Client) -> Arc<exporter_reconciler::Context> {
    Arc::new(exporter_reconciler::Context {
        client: client.clone(),
        signer: signer(),
        recorder: Recorder::new(
            client.clone(),
            Reporter {
                controller: "exporter-controller".into(),
                instance: None,
            },
        ),
    })
}

fn client_ctx(client: &Client) -> Arc<client_reconciler::Context> {
    Arc::new(client_reconciler::Context {
        client: client.clone(),
        signer: signer(),
        recorder: Recorder::new(
            client.clone(),
            Reporter {
                controller: "client-controller".into(),
                instance: None,
            },
        ),
    })
}

fn lease_ctx(client: &Client) -> Arc<lease_reconciler::Context> {
    Arc::new(lease_reconciler::Context {
        client: client.clone(),
    })
}

async fn create_exporter(
    client: &Client,
    ns: &str,
    name: &str,
    lbls: &[(&str, &str)],
) -> Result<Exporter, String> {
    let api: Api<Exporter> = Api::namespaced(client.clone(), ns);
    let e = Exporter {
        metadata: ObjectMeta {
            name: Some(name.to_string()),
            namespace: Some(ns.to_string()),
            labels: Some(labels(lbls)),
            ..Default::default()
        },
        spec: ExporterSpec::default(),
        status: None,
    };
    api.create(&PostParams::default(), &e)
        .await
        .map_err(|e| format!("create exporter {name}: {e}"))
}

/// Create an exporter that is Online + Registered + Available so the scheduler
/// will assign it (mirrors the Go `setExporterOnlineConditions(..., True)`
/// helper: Online=True, Devices set, LastSeen=now, ExporterStatus=Available).
async fn create_online_exporter(
    client: &Client,
    ns: &str,
    name: &str,
    lbls: &[(&str, &str)],
) -> Result<Exporter, String> {
    create_exporter(client, ns, name, lbls).await?;
    let api: Api<Exporter> = Api::namespaced(client.clone(), ns);
    let mut cur = api.get(name).await.map_err(|e| e.to_string())?;
    cur.status = Some(ExporterStatus {
        conditions: Some(vec![
            true_cond(EXPORTER_CONDITION_TYPE_ONLINE),
            true_cond(EXPORTER_CONDITION_TYPE_REGISTERED),
        ]),
        devices: Some(vec![Device::default()]),
        last_seen: Some(Time(Timestamp::now())),
        exporter_status: Some(ExporterStatusValue::Available),
        ..Default::default()
    });
    api.replace_status(name, &PostParams::default(), &cur)
        .await
        .map_err(|e| format!("status exporter {name}: {e}"))
}

async fn create_client(client: &Client, ns: &str, name: &str, lbls: &[(&str, &str)]) -> R {
    let api: Api<ClientCr> = Api::namespaced(client.clone(), ns);
    let c = ClientCr {
        metadata: ObjectMeta {
            name: Some(name.to_string()),
            namespace: Some(ns.to_string()),
            labels: Some(labels(lbls)),
            ..Default::default()
        },
        spec: ClientSpec::default(),
        status: None,
    };
    api.create(&PostParams::default(), &c)
        .await
        .map(|_| ())
        .map_err(|e| format!("create client {name}: {e}"))
}

async fn create_lease(
    client: &Client,
    ns: &str,
    name: &str,
    spec: LeaseSpec,
) -> Result<Lease, String> {
    let api: Api<Lease> = Api::namespaced(client.clone(), ns);
    let mut l = Lease::new(name, spec);
    l.metadata.namespace = Some(ns.to_string());
    api.create(&PostParams::default(), &l)
        .await
        .map_err(|e| format!("create lease {name}: {e}"))
}

async fn get_lease(client: &Client, ns: &str, name: &str) -> Result<Lease, String> {
    Api::<Lease>::namespaced(client.clone(), ns)
        .get(name)
        .await
        .map_err(|e| format!("get lease {name}: {e}"))
}

async fn get_exporter(client: &Client, ns: &str, name: &str) -> Result<Exporter, String> {
    Api::<Exporter>::namespaced(client.clone(), ns)
        .get(name)
        .await
        .map_err(|e| format!("get exporter {name}: {e}"))
}

/// Re-fetch a lease and run one reconcile pass (level-triggered).
async fn reconcile_lease(client: &Client, ns: &str, name: &str) -> R {
    let obj = get_lease(client, ns, name).await?;
    lease_reconciler::reconcile(Arc::new(obj), lease_ctx(client))
        .await
        .map(|_| ())
        .map_err(|e| format!("reconcile lease {name}: {e}"))
}

async fn reconcile_exporter(client: &Client, ns: &str, name: &str) -> R {
    let obj = get_exporter(client, ns, name).await?;
    exporter_reconciler::reconcile(Arc::new(obj), exporter_ctx(client))
        .await
        .map(|_| ())
        .map_err(|e| format!("reconcile exporter {name}: {e}"))
}

fn lease_condition(lease: &Lease, type_: &str) -> Option<Condition> {
    lease
        .status
        .as_ref()?
        .conditions
        .iter()
        .find(|c| c.type_ == type_)
        .cloned()
}

fn exporter_condition(exp: &Exporter, type_: &str) -> Option<Condition> {
    exp.status
        .as_ref()?
        .conditions
        .as_ref()?
        .iter()
        .find(|c| c.type_ == type_)
        .cloned()
}

fn want(cond: bool, msg: impl Into<String>) -> R {
    if cond {
        Ok(())
    } else {
        Err(msg.into())
    }
}

async fn secret_exists(client: &Client, ns: &str, name: &str) -> bool {
    Api::<Secret>::namespaced(client.clone(), ns)
        .get_opt(name)
        .await
        .ok()
        .flatten()
        .is_some()
}

fn exporter_ref_name(lease: &Lease) -> Option<String> {
    lease
        .status
        .as_ref()
        .and_then(|s| s.exporter_ref.as_ref())
        .map(|r| r.name.clone())
}

fn is_ended(lease: &Lease) -> bool {
    lease.status.as_ref().is_some_and(|s| s.ended)
}

// --------------------------------------------------------------------------
// scenarios
// --------------------------------------------------------------------------

/// Exporter reconciler: a recently-seen, device-registered exporter becomes
/// Online=True / Registered=True, gets its credential secret, and an endpoint.
async fn scenario_exporter_online_and_secret(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    // Seed a recent LastSeen + devices so the reconciler computes Online/Registered True.
    let api: Api<Exporter> = Api::namespaced(c.clone(), ns);
    let mut cur = api.get("exp").await.map_err(|e| e.to_string())?;
    cur.status = Some(ExporterStatus {
        last_seen: Some(Time(Timestamp::now())),
        devices: Some(vec![Device::default()]),
        ..Default::default()
    });
    api.replace_status("exp", &PostParams::default(), &cur)
        .await
        .map_err(|e| e.to_string())?;

    reconcile_exporter(c, ns, "exp").await?;

    let exp = get_exporter(c, ns, "exp").await?;
    let online =
        exporter_condition(&exp, EXPORTER_CONDITION_TYPE_ONLINE).ok_or("no Online condition")?;
    want(
        online.status == "True",
        format!("Online != True: {online:?}"),
    )?;
    let reg = exporter_condition(&exp, EXPORTER_CONDITION_TYPE_REGISTERED)
        .ok_or("no Registered condition")?;
    want(reg.status == "True", format!("Registered != True: {reg:?}"))?;

    want(
        secret_exists(c, ns, "exp-exporter").await,
        "credential secret exp-exporter missing",
    )?;
    let status = exp.status.as_ref().ok_or("no status")?;
    want(
        status.credential.as_ref().map(|r| r.name.as_str()) == Some("exp-exporter"),
        "status.credential not set to exp-exporter",
    )?;
    want(status.endpoint.is_some(), "status.endpoint not set")?;
    Ok(())
}

/// Exporter reconciler: a never-seen exporter is Online=False/Seen "Never seen".
async fn scenario_exporter_never_seen_offline(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    reconcile_exporter(c, ns, "exp").await?;
    let exp = get_exporter(c, ns, "exp").await?;
    let online =
        exporter_condition(&exp, EXPORTER_CONDITION_TYPE_ONLINE).ok_or("no Online condition")?;
    want(
        online.status == "False",
        format!("Online != False: {online:?}"),
    )?;
    want(
        online.reason == "Seen",
        format!("reason != Seen: {online:?}"),
    )?;
    want(
        online.message == "Never seen",
        format!("message != 'Never seen': {online:?}"),
    )?;
    Ok(())
}

/// Exporter reconciler: LastSeen older than a minute reports ExporterStatus=Offline.
async fn scenario_exporter_stale_offline(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    let api: Api<Exporter> = Api::namespaced(c.clone(), ns);
    let mut cur = api.get("exp").await.map_err(|e| e.to_string())?;
    let stale = Timestamp::now() - k8s_openapi::jiff::SignedDuration::from_secs(120);
    cur.status = Some(ExporterStatus {
        last_seen: Some(Time(stale)),
        devices: Some(vec![Device::default()]),
        ..Default::default()
    });
    api.replace_status("exp", &PostParams::default(), &cur)
        .await
        .map_err(|e| e.to_string())?;
    reconcile_exporter(c, ns, "exp").await?;
    let exp = get_exporter(c, ns, "exp").await?;
    let online = exporter_condition(&exp, EXPORTER_CONDITION_TYPE_ONLINE).ok_or("no Online")?;
    want(
        online.status == "False",
        format!("Online != False: {online:?}"),
    )?;
    want(
        exp.status.as_ref().and_then(|s| s.exporter_status) == Some(ExporterStatusValue::Offline),
        "exporterStatus != Offline",
    )?;
    Ok(())
}

/// Client reconciler: creates the `<name>-client` credential secret and sets the
/// endpoint + credential ref.
async fn scenario_client_secret_and_endpoint(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_client(c, ns, "cli", &[("name", "cli")]).await?;
    let obj = Api::<ClientCr>::namespaced(c.clone(), ns)
        .get("cli")
        .await
        .map_err(|e| e.to_string())?;
    client_reconciler::reconcile(Arc::new(obj), client_ctx(c))
        .await
        .map_err(|e| format!("reconcile client: {e}"))?;
    want(
        secret_exists(c, ns, "cli-client").await,
        "credential secret cli-client missing",
    )?;
    let cli = Api::<ClientCr>::namespaced(c.clone(), ns)
        .get("cli")
        .await
        .map_err(|e| e.to_string())?;
    let status = cli.status.as_ref().ok_or("no client status")?;
    want(
        status.credential.as_ref().map(|r| r.name.as_str()) == Some("cli-client"),
        "client credential not set",
    )?;
    want(!status.endpoint.is_empty(), "client endpoint empty")?;
    Ok(())
}

/// Lease assignment: an online matching exporter is acquired, beginTime stamped,
/// Ready=True, and an owner reference to the exporter is written.
async fn scenario_lease_assignment(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    let exp = create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(3600 * SECOND)),
            selector: selector(&[("dut", "a")]),
            ..Default::default()
        },
    )
    .await?;
    reconcile_lease(c, ns, "lease").await?;

    let lease = get_lease(c, ns, "lease").await?;
    want(
        exporter_ref_name(&lease).as_deref() == Some("exp"),
        format!("exporterRef != exp: {:?}", exporter_ref_name(&lease)),
    )?;
    let ready = lease_condition(&lease, LEASE_CONDITION_TYPE_READY).ok_or("no Ready condition")?;
    want(ready.status == "True", format!("Ready != True: {ready:?}"))?;
    want(ready.reason == "Ready", format!("Ready reason: {ready:?}"))?;
    want(
        lease
            .status
            .as_ref()
            .and_then(|s| s.begin_time.clone())
            .is_some(),
        "beginTime not stamped",
    )?;
    // Owner reference to the exporter (controllerutil.SetControllerReference).
    let owner_ok = lease
        .metadata
        .owner_references
        .as_ref()
        .is_some_and(|refs| refs.iter().any(|r| r.uid == exp.uid().unwrap_or_default()));
    want(owner_ok, "no owner reference to exporter")?;
    Ok(())
}

/// Scheduled lease: with a far-future beginTime the reconciler must NOT assign an
/// exporter even though a matching online one exists (WaitUntilBegin).
async fn scenario_scheduled_lease_waits(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    let begin = Timestamp::now() + k8s_openapi::jiff::SignedDuration::from_hours(1);
    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(60 * SECOND)),
            selector: selector(&[("dut", "a")]),
            begin_time: Some(Time(begin)),
            ..Default::default()
        },
    )
    .await?;
    reconcile_lease(c, ns, "lease").await?;
    let lease = get_lease(c, ns, "lease").await?;
    want(
        exporter_ref_name(&lease).is_none(),
        "exporter assigned before beginTime",
    )?;
    want(!is_ended(&lease), "lease unexpectedly ended")?;
    want(
        lease_condition(&lease, LEASE_CONDITION_TYPE_READY).is_none(),
        "Ready set before beginTime",
    )?;
    Ok(())
}

/// Offline exporter: a matching-but-offline exporter yields Pending/Offline with
/// no assignment.
async fn scenario_offline_exporter_pending(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    // Exporter matches the selector but has no online status.
    create_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(3600 * SECOND)),
            selector: selector(&[("dut", "a")]),
            ..Default::default()
        },
    )
    .await?;
    reconcile_lease(c, ns, "lease").await?;
    let lease = get_lease(c, ns, "lease").await?;
    want(exporter_ref_name(&lease).is_none(), "unexpected assignment")?;
    let pending = lease_condition(&lease, LEASE_CONDITION_TYPE_PENDING).ok_or("no Pending")?;
    want(
        pending.status == "True",
        format!("Pending != True: {pending:?}"),
    )?;
    want(
        pending.reason == REASON_OFFLINE,
        format!("reason != Offline: {pending:?}"),
    )?;
    want(!is_ended(&lease), "lease ended while pending")?;
    Ok(())
}

/// Release: setting spec.release ends an assigned lease with Ready=False/Released
/// and the lease-ended label.
async fn scenario_release(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(3600 * SECOND)),
            selector: selector(&[("dut", "a")]),
            ..Default::default()
        },
    )
    .await?;
    reconcile_lease(c, ns, "lease").await?;
    want(
        exporter_ref_name(&get_lease(c, ns, "lease").await?).as_deref() == Some("exp"),
        "lease not assigned before release",
    )?;

    // Request release (spec update = main-resource PUT; status is preserved).
    let api: Api<Lease> = Api::namespaced(c.clone(), ns);
    let mut l = api.get("lease").await.map_err(|e| e.to_string())?;
    l.spec.release = true;
    api.replace("lease", &PostParams::default(), &l)
        .await
        .map_err(|e| format!("set release: {e}"))?;

    reconcile_lease(c, ns, "lease").await?;
    let lease = get_lease(c, ns, "lease").await?;
    want(is_ended(&lease), "lease not ended after release")?;
    let ready = lease_condition(&lease, LEASE_CONDITION_TYPE_READY).ok_or("no Ready")?;
    want(
        ready.status == "False",
        format!("Ready != False: {ready:?}"),
    )?;
    want(
        ready.reason == "Released",
        format!("reason != Released: {ready:?}"),
    )?;
    let labeled = lease
        .metadata
        .labels
        .as_ref()
        .and_then(|m| m.get(LEASE_LABEL_ENDED))
        .map(String::as_str)
        == Some("true");
    want(labeled, "lease-ended label not set")?;
    Ok(())
}

/// Expiry: a short-duration lease transitions to Ended/Expired once its duration
/// elapses, driven by a second reconcile after a real sleep.
async fn scenario_expiry(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(2 * SECOND)),
            selector: selector(&[("dut", "a")]),
            ..Default::default()
        },
    )
    .await?;
    // Pass 1: assign + stamp beginTime (now); not yet expired.
    reconcile_lease(c, ns, "lease").await?;
    want(
        !is_ended(&get_lease(c, ns, "lease").await?),
        "expired too early",
    )?;

    tokio::time::sleep(Duration::from_millis(2500)).await;

    // Pass 2: now past beginTime+duration -> Expired.
    reconcile_lease(c, ns, "lease").await?;
    let lease = get_lease(c, ns, "lease").await?;
    want(is_ended(&lease), "lease not ended after duration")?;
    let ready = lease_condition(&lease, LEASE_CONDITION_TYPE_READY).ok_or("no Ready")?;
    want(
        ready.reason == "Expired",
        format!("reason != Expired: {ready:?}"),
    )?;
    Ok(())
}

/// Two-lease contention: one exporter, two leases. The first wins; the second is
/// Pending/NotAvailable (all matching exporters already leased).
async fn scenario_contention(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    for name in ["lease-a", "lease-b"] {
        create_lease(
            c,
            ns,
            name,
            LeaseSpec {
                client_ref: LocalObjectReference { name: "cli".into() },
                duration: Some(GoDuration(3600 * SECOND)),
                selector: selector(&[("dut", "a")]),
                ..Default::default()
            },
        )
        .await?;
    }
    reconcile_lease(c, ns, "lease-a").await?;
    reconcile_lease(c, ns, "lease-b").await?;

    let a = get_lease(c, ns, "lease-a").await?;
    let b = get_lease(c, ns, "lease-b").await?;
    want(
        exporter_ref_name(&a).as_deref() == Some("exp"),
        "lease-a did not acquire exporter",
    )?;
    want(
        exporter_ref_name(&b).is_none(),
        "lease-b unexpectedly assigned",
    )?;
    let pending = lease_condition(&b, LEASE_CONDITION_TYPE_PENDING).ok_or("b has no Pending")?;
    want(
        pending.reason == REASON_NOT_AVAILABLE,
        format!("b reason != NotAvailable: {pending:?}"),
    )?;
    Ok(())
}

/// Handoff: after the first lease is released, the freed exporter is assigned to
/// the waiting second lease.
async fn scenario_handoff(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    for name in ["lease-a", "lease-b"] {
        create_lease(
            c,
            ns,
            name,
            LeaseSpec {
                client_ref: LocalObjectReference { name: "cli".into() },
                duration: Some(GoDuration(3600 * SECOND)),
                selector: selector(&[("dut", "a")]),
                ..Default::default()
            },
        )
        .await?;
    }
    reconcile_lease(c, ns, "lease-a").await?;
    reconcile_lease(c, ns, "lease-b").await?; // b -> NotAvailable

    // Release a.
    let api: Api<Lease> = Api::namespaced(c.clone(), ns);
    let mut a = api.get("lease-a").await.map_err(|e| e.to_string())?;
    a.spec.release = true;
    api.replace("lease-a", &PostParams::default(), &a)
        .await
        .map_err(|e| e.to_string())?;
    reconcile_lease(c, ns, "lease-a").await?;
    want(is_ended(&get_lease(c, ns, "lease-a").await?), "a not ended")?;

    // b can now be scheduled onto the freed exporter.
    reconcile_lease(c, ns, "lease-b").await?;
    let b = get_lease(c, ns, "lease-b").await?;
    want(
        exporter_ref_name(&b).as_deref() == Some("exp"),
        format!(
            "handoff failed, b.exporterRef = {:?}",
            exporter_ref_name(&b)
        ),
    )?;
    Ok(())
}

/// Policy maximumDuration: a rule whose cap is shorter than the requested
/// duration is skipped; with no other approving rule the lease is
/// Unsatisfiable/NoAccess and ends.
async fn scenario_policy_max_duration_rejection(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    create_client(c, ns, "cli", &[("team", "qa")]).await?;

    let policy = ExporterAccessPolicy::new(
        "policy",
        ExporterAccessPolicySpec {
            exporter_selector: selector(&[("dut", "a")]),
            policies: vec![Policy {
                description: "qa capped at 1s".into(),
                priority: 0,
                spot_access: false,
                maximum_duration: Some(GoDuration(SECOND)),
                from: vec![PolicyFrom {
                    client_selector: selector(&[("team", "qa")]),
                }],
            }],
        },
    );
    let mut policy = policy;
    policy.metadata.namespace = Some(ns.to_string());
    Api::<ExporterAccessPolicy>::namespaced(c.clone(), ns)
        .create(&PostParams::default(), &policy)
        .await
        .map_err(|e| format!("create policy: {e}"))?;

    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(3600 * SECOND)), // exceeds the 1s cap
            selector: selector(&[("dut", "a")]),
            ..Default::default()
        },
    )
    .await?;
    reconcile_lease(c, ns, "lease").await?;
    let lease = get_lease(c, ns, "lease").await?;
    want(
        exporter_ref_name(&lease).is_none(),
        "assigned despite policy",
    )?;
    let unsat =
        lease_condition(&lease, LEASE_CONDITION_TYPE_UNSATISFIABLE).ok_or("no Unsatisfiable")?;
    want(
        unsat.reason == REASON_NO_ACCESS,
        format!("reason != NoAccess: {unsat:?}"),
    )?;
    want(is_ended(&lease), "unsatisfiable lease not ended")?;
    Ok(())
}

/// No matching exporter: a selector that matches zero exporters is
/// Unsatisfiable/NoAccess and ends. (The empty-selector Invalid/InvalidSelector
/// path is unreachable end-to-end — the CRD's CEL rule rejects it at admission —
/// so it is covered only by the pure `scheduler::decide` unit tests.)
async fn scenario_no_matching_exporter(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    // A single online exporter that does NOT match the lease selector.
    create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(3600 * SECOND)),
            selector: selector(&[("dut", "does-not-exist")]),
            ..Default::default()
        },
    )
    .await?;
    reconcile_lease(c, ns, "lease").await?;
    let lease = get_lease(c, ns, "lease").await?;
    let unsat =
        lease_condition(&lease, LEASE_CONDITION_TYPE_UNSATISFIABLE).ok_or("no Unsatisfiable")?;
    want(
        unsat.reason == REASON_NO_ACCESS,
        format!("reason != NoAccess: {unsat:?}"),
    )?;
    want(is_ended(&lease), "unsatisfiable lease not ended")?;
    Ok(())
}

/// Pinned exporter not found: spec.exporterRef to a missing exporter is
/// Unsatisfiable/ExporterNotFound.
async fn scenario_pinned_exporter_not_found(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(3600 * SECOND)),
            selector: LabelSelector::default(),
            exporter_ref: Some(LocalObjectReference {
                name: "ghost".into(),
            }),
            ..Default::default()
        },
    )
    .await?;
    reconcile_lease(c, ns, "lease").await?;
    let lease = get_lease(c, ns, "lease").await?;
    let unsat =
        lease_condition(&lease, LEASE_CONDITION_TYPE_UNSATISFIABLE).ok_or("no Unsatisfiable")?;
    want(
        unsat.reason == REASON_EXPORTER_NOT_FOUND,
        format!("reason != ExporterNotFound: {unsat:?}"),
    )?;
    want(is_ended(&lease), "pinned-not-found lease not ended")?;
    Ok(())
}

/// Exporter <-> lease cross-refs: once a lease is assigned, the exporter
/// reconciler records status.leaseRef pointing back at that lease.
async fn scenario_exporter_lease_ref(env: &TestEnv, ns: &str) -> R {
    let c = &env.client;
    create_online_exporter(c, ns, "exp", &[("dut", "a")]).await?;
    create_lease(
        c,
        ns,
        "lease",
        LeaseSpec {
            client_ref: LocalObjectReference { name: "cli".into() },
            duration: Some(GoDuration(3600 * SECOND)),
            selector: selector(&[("dut", "a")]),
            ..Default::default()
        },
    )
    .await?;
    reconcile_lease(c, ns, "lease").await?;
    reconcile_exporter(c, ns, "exp").await?;
    let exp = get_exporter(c, ns, "exp").await?;
    want(
        exp.status
            .as_ref()
            .and_then(|s| s.lease_ref.as_ref())
            .map(|r| r.name.as_str())
            == Some("lease"),
        "exporter status.leaseRef not set to lease",
    )?;
    Ok(())
}

// --------------------------------------------------------------------------
// driver
// --------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn envtest_lease_lifecycle() {
    if common::assets().is_none() {
        eprintln!("SKIP: KUBEBUILDER_ASSETS not set — hermetic run, skipping envtest suite");
        return;
    }

    let env = TestEnv::start().await.expect("start envtest control plane");

    // (name, namespace, scenario future). Each gets an isolated namespace.
    let mut results: Vec<(&str, R)> = Vec::new();

    macro_rules! run {
        ($name:literal, $ns:literal, $f:ident) => {{
            let r = match env.create_namespace($ns).await {
                Ok(()) => $f(&env, $ns).await,
                Err(e) => Err(format!("create namespace: {e}")),
            };
            results.push(($name, r));
        }};
    }

    run!(
        "exporter_online_and_secret",
        "s01",
        scenario_exporter_online_and_secret
    );
    run!(
        "exporter_never_seen_offline",
        "s02",
        scenario_exporter_never_seen_offline
    );
    run!(
        "exporter_stale_offline",
        "s03",
        scenario_exporter_stale_offline
    );
    run!(
        "client_secret_and_endpoint",
        "s04",
        scenario_client_secret_and_endpoint
    );
    run!("lease_assignment", "s05", scenario_lease_assignment);
    run!(
        "scheduled_lease_waits",
        "s06",
        scenario_scheduled_lease_waits
    );
    run!(
        "offline_exporter_pending",
        "s07",
        scenario_offline_exporter_pending
    );
    run!("release", "s08", scenario_release);
    run!("expiry", "s09", scenario_expiry);
    run!("contention", "s10", scenario_contention);
    run!("handoff", "s11", scenario_handoff);
    run!(
        "policy_max_duration_rejection",
        "s12",
        scenario_policy_max_duration_rejection
    );
    run!("no_matching_exporter", "s13", scenario_no_matching_exporter);
    run!(
        "pinned_exporter_not_found",
        "s14",
        scenario_pinned_exporter_not_found
    );
    run!("exporter_lease_ref", "s15", scenario_exporter_lease_ref);

    eprintln!("\n================ envtest scenario results ================");
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

    assert_eq!(failures, 0, "{failures} envtest scenario(s) failed");
}
