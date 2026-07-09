//! The spec-02 conformance case suite. Every case arranges CR/secret state via
//! kube, calls a gRPC method through the real service, and asserts the exact
//! `(code, details)` plus resulting CR state. Organized by module:
//!
//! - [`error_strings`] — the consolidated error-string contract (the single most
//!   field-critical parity surface; spec 02 §12).
//! - [`register`] / [`report_status`] — exporter registration + status reporting.
//! - [`listen_dial`] — the Listen/Dial rendezvous, retry gate, and the
//!   [`ListenRegistry`](jumpstarter_controller_service::listen_registry) buffer /
//!   supersession invariants.
//! - [`status_stream`] — the exporter Status server-stream.
//! - [`client_service`] — the `jumpstarter.client.v1` surface.
//! - [`auth`] — bearer + metadata edge cases.
//!
//! [`run_all`] executes every case under one envtest bring-up (a fresh namespace
//! each) and returns the `(name, result)` table for the test runner to assert.

#![allow(clippy::result_large_err)]

use std::time::Duration;

use futures::StreamExt;
use k8s_openapi::api::core::v1::LocalObjectReference;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::ObjectMeta;
use kube::api::{Api, PostParams};
use kube::ResourceExt;
use tonic::Code;

use jumpstarter_controller_api::lease::{Lease, LeaseSpec};

use jumpstarter_protocol::client_v1 as cpb;
use jumpstarter_protocol::client_v1::client_service_server::ClientService as _;
use jumpstarter_protocol::v1 as pb;
use jumpstarter_protocol::v1::controller_service_server::ControllerService as _;

use crate::harness::{
    deadline, deadline_for, labels, match_labels_selector, request, request_with, want, Harness,
    TestEnv, R, ROUTER_ENDPOINT,
};

// ===========================================================================
// error_strings
// ===========================================================================
mod error_strings {
    use super::*;

    /// Dial with an empty `lease_name` → `UNKNOWN "empty lease name"`.
    pub async fn empty_lease_name(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        let req = request(
            pb::DialRequest {
                lease_name: String::new(),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("dial", h.controller.dial(req))
            .await?
            .err()
            .ok_or("Dial with empty lease_name unexpectedly succeeded")?;
        want(
            err.code() == Code::Unknown,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "empty lease name",
            format!("message: {:?}", err.message()),
        )
    }

    /// Dial a lease owned by another client → `UNKNOWN "permission denied"`
    /// (clients read this exact string as "lease transferred").
    pub async fn permission_denied(h: &Harness, ns: &str) -> R {
        let (_e, _et) = h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
        let (_c, token) = h.make_client(ns, "cli").await?;
        h.set_exporter_status(ns, "exp", "LeaseReady").await?;
        h.create_assigned_lease(ns, "lease", "other-client", "exp", &[("dut", "a")])
            .await?;
        let req = request(
            pb::DialRequest {
                lease_name: "lease".to_string(),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("dial", h.controller.dial(req))
            .await?
            .err()
            .ok_or("Dial on a lease owned by another client unexpectedly succeeded")?;
        want(
            err.code() == Code::Unknown,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "permission denied",
            format!("message: {:?}", err.message()),
        )
    }

    /// Dial a lease with no `status.exporterRef` → `UNKNOWN "lease not active"`.
    pub async fn lease_not_active(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        h.create_unassigned_lease(ns, "lease", "cli").await?;
        let req = request(
            pb::DialRequest {
                lease_name: "lease".to_string(),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("dial", h.controller.dial(req))
            .await?
            .err()
            .ok_or("Dial on an unassigned lease unexpectedly succeeded")?;
        want(
            err.code() == Code::Unknown,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "lease not active",
            format!("message: {:?}", err.message()),
        )
    }

    /// Dial with an empty router config → `UNKNOWN "no router available"`.
    pub async fn no_router_available(h: &Harness, ns: &str) -> R {
        let (_e, exp_token) = h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
        let (_c, cli_token) = h.make_client(ns, "cli").await?;
        h.set_exporter_status(ns, "exp", "LeaseReady").await?;
        h.create_assigned_lease(ns, "lease", "cli", "exp", &[("dut", "a")])
            .await?;

        // Register a listener so Dial reaches the (empty) router selection step.
        let listen_req = request(
            pb::ListenRequest {
                lease_name: "lease".to_string(),
            },
            &exp_token,
            ns,
            "Exporter",
            "exp",
        );
        let _listen = deadline("listen", h.controller.listen(listen_req))
            .await?
            .map_err(|s| format!("listen rpc: {s}"))?
            .into_inner();

        let no_router = h.controller_with_router(std::collections::BTreeMap::new());
        let req = request(
            pb::DialRequest {
                lease_name: "lease".to_string(),
            },
            &cli_token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("dial", no_router.dial(req))
            .await?
            .err()
            .ok_or("Dial with no routers unexpectedly succeeded")?;
        want(
            err.code() == Code::Unknown,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "no router available",
            format!("message: {:?}", err.message()),
        )
    }

    /// Dial a ready lease with no active `Listen` →
    /// `UNAVAILABLE "exporter is not listening on lease {name}"`.
    pub async fn exporter_not_listening(h: &Harness, ns: &str) -> R {
        let (_e, _et) = h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
        let (_c, cli_token) = h.make_client(ns, "cli").await?;
        h.set_exporter_status(ns, "exp", "LeaseReady").await?;
        h.create_assigned_lease(ns, "lease", "cli", "exp", &[("dut", "a")])
            .await?;
        let req = request(
            pb::DialRequest {
                lease_name: "lease".to_string(),
            },
            &cli_token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("dial", h.controller.dial(req))
            .await?
            .err()
            .ok_or("Dial with no listener unexpectedly succeeded")?;
        want(
            err.code() == Code::Unavailable,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "exporter is not listening on lease lease",
            format!("message: {:?}", err.message()),
        )
    }

    /// The listener's 8-slot buffer, filled, then overflowed →
    /// `RESOURCE_EXHAUSTED "listener buffer full on lease {name}"`. Driven at the
    /// registry contract level (the exact object the service uses).
    pub async fn listener_buffer_full(h: &Harness, _ns: &str) -> R {
        let lease = "buf-lease";
        // Hold the receiver so the queue stays open; never drain it.
        let _reg = h.registry.register(lease);
        for i in 0..8 {
            h.registry
                .send_to_listener(
                    lease,
                    pb::ListenResponse {
                        router_endpoint: ROUTER_ENDPOINT.to_string(),
                        router_token: format!("t{i}"),
                    },
                )
                .map_err(|s| format!("send {i} should fit in the buffer: {s}"))?;
        }
        let err = h
            .registry
            .send_to_listener(
                lease,
                pb::ListenResponse {
                    router_endpoint: ROUTER_ENDPOINT.to_string(),
                    router_token: "overflow".to_string(),
                },
            )
            .err()
            .ok_or("9th send unexpectedly fit in an 8-slot buffer")?;
        want(
            err.code() == Code::ResourceExhausted,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == format!("listener buffer full on lease {lease}"),
            format!("message: {:?}", err.message()),
        )
    }

    /// CreateLease with neither selector nor exporter_name →
    /// `INVALID_ARGUMENT "one of selector or exporter_name is required"`.
    pub async fn invalid_argument_create_lease(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        let req = request(
            cpb::CreateLeaseRequest {
                parent: format!("namespaces/{ns}"),
                lease_id: String::new(),
                lease: Some(cpb::Lease {
                    selector: String::new(),
                    exporter_name: None,
                    ..Default::default()
                }),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("create_lease", h.client_svc.create_lease(req))
            .await?
            .err()
            .ok_or("CreateLease with no target unexpectedly succeeded")?;
        want(
            err.code() == Code::InvalidArgument,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "one of selector or exporter_name is required",
            format!("message: {:?}", err.message()),
        )
    }

    /// No authorization header → `UNAUTHENTICATED "missing authorization header"`.
    pub async fn missing_authorization_header(h: &Harness, ns: &str) -> R {
        let req = request_with(pb::RegisterRequest::default(), |md| {
            md.insert("jumpstarter-namespace", ns.parse().unwrap());
            md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
            md.insert("jumpstarter-name", "exp".parse().unwrap());
        });
        let err = deadline("register", h.controller.register(req))
            .await?
            .err()
            .ok_or("Register with no bearer unexpectedly succeeded")?;
        want(
            err.code() == Code::Unauthenticated,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "missing authorization header",
            format!("message: {:?}", err.message()),
        )
    }

    /// An expired internal token → `UNKNOWN` whose message contains
    /// `"token is expired"` (the re-auth trigger substring), NOT remapped to a
    /// "cleaner" code.
    pub async fn token_expired(h: &Harness, ns: &str) -> R {
        let (exp, _valid) = h.make_exporter(ns, "exp", &[]).await?;
        let expired = h.expired_token(&exp.internal_subject())?;
        let req = request(
            pb::RegisterRequest::default(),
            &expired,
            ns,
            "Exporter",
            "exp",
        );
        let err = deadline("register", h.controller.register(req))
            .await?
            .err()
            .ok_or("Register with an expired token unexpectedly succeeded")?;
        want(
            err.code() == Code::Unknown,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message().contains("token is expired"),
            format!("message lacks 'token is expired': {:?}", err.message()),
        )
    }

    /// A nonexistent lease → `UNKNOWN` with the verbatim apiserver "not found"
    /// text; CRITICAL: never remapped to `NOT_FOUND`.
    pub async fn apiserver_notfound_verbatim(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        let req = request(
            pb::DialRequest {
                lease_name: "ghost".to_string(),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("dial", h.controller.dial(req))
            .await?
            .err()
            .ok_or("Dial on a nonexistent lease unexpectedly succeeded")?;
        want(
            err.code() == Code::Unknown,
            format!("code must be UNKNOWN, not NOT_FOUND: {:?}", err.code()),
        )?;
        want(
            err.code() != Code::NotFound,
            "code was mapped to NOT_FOUND (spec 02 §12.2 violation)",
        )?;
        // §12.2 wart #2: the apiserver text is passed through byte-verbatim. The
        // lease name "ghost" is a fixed literal (not a per-run identifier), so the
        // whole message is deterministic — pin it exactly, not by substring.
        want(
            err.message() == "leases.jumpstarter.dev \"ghost\" not found",
            format!("message not byte-verbatim: {:?}", err.message()),
        )
    }
}

// ===========================================================================
// register
// ===========================================================================
mod register {
    use super::*;

    /// Register writes only `jumpstarter.dev/`-prefixed labels (removing stale
    /// managed labels, leaving non-managed labels untouched), records the device
    /// tree on status, and returns the exporter UID.
    pub async fn label_prefix_and_devices(h: &Harness, ns: &str) -> R {
        // Pre-existing: one stale managed label + one non-managed label.
        let (exp, token) = h
            .make_exporter(
                ns,
                "exp",
                &[("jumpstarter.dev/stale", "old"), ("team", "qa")],
            )
            .await?;
        let uid = exp.uid().unwrap_or_default();

        let mut req_labels = std::collections::HashMap::new();
        req_labels.insert("jumpstarter.dev/board".to_string(), "rpi4".to_string());
        // A non-managed label in the request must be ignored.
        req_labels.insert("unmanaged".to_string(), "x".to_string());

        let req = request(
            pb::RegisterRequest {
                labels: req_labels,
                reports: vec![
                    pb::DriverInstanceReport {
                        uuid: "dev-a".to_string(),
                        ..Default::default()
                    },
                    pb::DriverInstanceReport {
                        uuid: "dev-b".to_string(),
                        parent_uuid: Some("dev-a".to_string()),
                        ..Default::default()
                    },
                ],
            },
            &token,
            ns,
            "Exporter",
            "exp",
        );
        let resp = deadline("register", h.controller.register(req))
            .await?
            .map_err(|s| format!("register rpc: {s}"))?
            .into_inner();
        want(resp.uuid == uid, format!("uuid {} != {uid}", resp.uuid))?;

        let after = h
            .exporters(ns)
            .get("exp")
            .await
            .map_err(|e| e.to_string())?;
        let lbls = after.metadata.labels.clone().unwrap_or_default();
        want(
            lbls.get("jumpstarter.dev/board").map(String::as_str) == Some("rpi4"),
            "managed label jumpstarter.dev/board not written",
        )?;
        want(
            !lbls.contains_key("jumpstarter.dev/stale"),
            "stale managed label was not removed",
        )?;
        want(
            lbls.get("team").map(String::as_str) == Some("qa"),
            "non-managed pre-existing label was clobbered",
        )?;
        want(
            !lbls.contains_key("unmanaged"),
            "non-managed request label was written (must be ignored)",
        )?;
        let device_count = after
            .status
            .as_ref()
            .and_then(|s| s.devices.as_ref())
            .map(|d| d.len());
        want(
            device_count == Some(2),
            format!("device tree len != 2: {device_count:?}"),
        )
    }
}

// ===========================================================================
// report_status
// ===========================================================================
mod report_status {
    use super::*;

    /// ReportStatus maps the proto enum to the CRD value and stamps lastSeen.
    pub async fn enum_and_lastseen(h: &Harness, ns: &str) -> R {
        let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
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
        deadline("report_status", h.controller.report_status(req))
            .await?
            .map_err(|s| format!("report_status rpc: {s}"))?;

        let after = h
            .exporters(ns)
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
        want(status.last_seen.is_some(), "lastSeen not stamped")
    }

    /// ReportStatus with `release_lease` marks the held lease for release.
    pub async fn release_side_effect(h: &Harness, ns: &str) -> R {
        let (_e, token) = h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
        h.set_exporter_status(ns, "exp", "LeaseReady").await?;
        h.create_assigned_lease(ns, "lease", "cli", "exp", &[("dut", "a")])
            .await?;
        h.set_exporter_lease_ref(ns, "exp", "lease").await?;

        let req = request(
            pb::ReportStatusRequest {
                status: pb::ExporterStatus::LeaseReady as i32,
                message: None,
                release_lease: Some(true),
            },
            &token,
            ns,
            "Exporter",
            "exp",
        );
        deadline("report_status", h.controller.report_status(req))
            .await?
            .map_err(|s| format!("report_status rpc: {s}"))?;

        let lease = h.leases(ns).get("lease").await.map_err(|e| e.to_string())?;
        want(
            lease.spec.release,
            "spec.release not set by ReportStatus release_lease side effect",
        )
    }

    /// ReportStatus with `release_lease` but no active lease still returns OK —
    /// the side effect is logged, never fails the RPC.
    pub async fn release_never_fails(h: &Harness, ns: &str) -> R {
        let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
        let req = request(
            pb::ReportStatusRequest {
                status: pb::ExporterStatus::Available as i32,
                message: None,
                release_lease: Some(true),
            },
            &token,
            ns,
            "Exporter",
            "exp",
        );
        deadline("report_status", h.controller.report_status(req))
            .await?
            .map_err(|s| format!("report_status with no lease must not fail the RPC: {s}"))?;
        Ok(())
    }
}

// ===========================================================================
// listen_dial
// ===========================================================================
mod listen_dial {
    use super::*;

    /// Listen + Dial: the router `(endpoint, token)` pushed to the Listen stream
    /// is byte-identical to the one returned to Dial.
    pub async fn handshake(h: &Harness, ns: &str) -> R {
        let (_e, exp_token) = h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
        let (_c, cli_token) = h.make_client(ns, "cli").await?;
        h.set_exporter_status(ns, "exp", "LeaseReady").await?;
        h.create_assigned_lease(ns, "lease", "cli", "exp", &[("dut", "a")])
            .await?;

        let listen_req = request(
            pb::ListenRequest {
                lease_name: "lease".to_string(),
            },
            &exp_token,
            ns,
            "Exporter",
            "exp",
        );
        let mut listen = deadline("listen", h.controller.listen(listen_req))
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
        let dial = deadline("dial", h.controller.dial(dial_req))
            .await?
            .map_err(|s| format!("dial rpc: {s}"))?
            .into_inner();

        want(!dial.router_token.is_empty(), "dial returned empty token")?;
        want(
            dial.router_endpoint == ROUTER_ENDPOINT,
            format!("dial endpoint mismatch: {}", dial.router_endpoint),
        )?;

        let delivered = deadline("listen-recv", listen.next())
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
        )
    }

    /// The status gate retries while `Available` and succeeds when the exporter
    /// flips to `LeaseReady` mid-retry.
    pub async fn retry_gate_success(h: &Harness, ns: &str) -> R {
        let (_e, exp_token) = h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
        let (_c, cli_token) = h.make_client(ns, "cli").await?;
        h.set_exporter_status(ns, "exp", "Available").await?;
        h.create_assigned_lease(ns, "lease", "cli", "exp", &[("dut", "a")])
            .await?;

        let listen_req = request(
            pb::ListenRequest {
                lease_name: "lease".to_string(),
            },
            &exp_token,
            ns,
            "Exporter",
            "exp",
        );
        let mut listen = deadline("listen", h.controller.listen(listen_req))
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
        let controller = h.controller.clone();
        let flip = async {
            tokio::time::sleep(Duration::from_millis(150)).await;
            h.set_exporter_status(ns, "exp", "LeaseReady").await
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

        let delivered = deadline("listen-recv", listen.next())
            .await?
            .ok_or("Listen stream closed before delivering a token (retry gate)")?
            .map_err(|s| format!("listen stream error: {s}"))?;
        want(
            delivered.router_token == dial.router_token,
            "retry-gate: token delivered != returned",
        )
    }

    /// The status gate does NOT retry on a terminal non-`Available` status:
    /// `Offline` fails immediately with `FAILED_PRECONDITION "exporter is
    /// offline"`. (The 30 s Available-past-deadline path is unit-tested with
    /// paused time in `dial.rs`; a live 30 s wait would violate anti-stall.)
    pub async fn gate_non_retryable_offline(h: &Harness, ns: &str) -> R {
        let (_e, _et) = h.make_exporter(ns, "exp", &[("dut", "a")]).await?;
        let (_c, cli_token) = h.make_client(ns, "cli").await?;
        h.set_exporter_status(ns, "exp", "Offline").await?;
        h.create_assigned_lease(ns, "lease", "cli", "exp", &[("dut", "a")])
            .await?;
        let req = request(
            pb::DialRequest {
                lease_name: "lease".to_string(),
            },
            &cli_token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("dial-offline", h.controller.dial(req))
            .await?
            .err()
            .ok_or("Dial on an Offline exporter unexpectedly succeeded")?;
        want(
            err.code() == Code::FailedPrecondition,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "exporter is offline",
            format!("message: {:?}", err.message()),
        )
    }

    /// Supersession on reconnect: a second `register` for the same lease cancels
    /// the prior listener (epoch-guarded) and becomes the live target.
    pub async fn supersession(h: &Harness, _ns: &str) -> R {
        let lease = "supersede-lease";
        let reg1 = h.registry.register(lease);
        let reg2 = h.registry.register(lease);
        want(
            reg1.done.is_cancelled(),
            "first listener not cancelled on reconnect",
        )?;
        want(
            !reg2.done.is_cancelled(),
            "second (live) listener was cancelled",
        )?;
        want(reg1.epoch != reg2.epoch, "epoch not advanced on reconnect")?;
        // A send now targets the live (second) queue, not the superseded one.
        h.registry
            .send_to_listener(
                lease,
                pb::ListenResponse {
                    router_endpoint: ROUTER_ENDPOINT.to_string(),
                    router_token: "t".to_string(),
                },
            )
            .map_err(|s| format!("send to live listener after supersession failed: {s}"))?;
        drop(reg2);
        Ok(())
    }
}

// ===========================================================================
// status_stream
// ===========================================================================
mod status_stream {
    use super::*;

    /// The stream emits an explicit initial event and the connect patch stamps
    /// `lastSeen`.
    pub async fn initial_event(h: &Harness, ns: &str) -> R {
        let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
        let req = request(pb::StatusRequest {}, &token, ns, "Exporter", "exp");
        let mut stream = deadline("status", h.controller.status(req))
            .await?
            .map_err(|s| format!("status rpc: {s}"))?
            .into_inner();

        let first = deadline("status-initial", stream.next())
            .await?
            .ok_or("status stream closed before the initial event")?
            .map_err(|s| format!("status stream error: {s}"))?;
        want(!first.leased, "unleased exporter reported leased=true")?;

        let after = h
            .exporters(ns)
            .get("exp")
            .await
            .map_err(|e| e.to_string())?;
        want(
            after
                .status
                .as_ref()
                .and_then(|s| s.last_seen.clone())
                .is_some(),
            "connect patch did not stamp lastSeen",
        )
    }

    /// No duplicate event without a CR change: after the initial event, a second
    /// recv times out (the connect-patch Modified event dedups to the same
    /// StatusResponse).
    pub async fn dedup(h: &Harness, ns: &str) -> R {
        let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
        let req = request(pb::StatusRequest {}, &token, ns, "Exporter", "exp");
        let mut stream = deadline("status", h.controller.status(req))
            .await?
            .map_err(|s| format!("status rpc: {s}"))?
            .into_inner();
        let _first = deadline("status-initial", stream.next())
            .await?
            .ok_or("status stream closed before the initial event")?
            .map_err(|s| format!("status stream error: {s}"))?;

        // Within 2s (< the 10s heartbeat) no further event should arrive.
        match tokio::time::timeout(Duration::from_secs(2), stream.next()).await {
            Err(_elapsed) => Ok(()),
            Ok(Some(Ok(resp))) => Err(format!("unexpected duplicate event: {resp:?}")),
            Ok(Some(Err(s))) => Err(format!("stream errored during dedup window: {s}")),
            Ok(None) => Err("stream closed during dedup window".to_string()),
        }
    }

    /// The 10 s heartbeat re-stamps `lastSeen` (sampled before/after a ~11 s
    /// window). Budgeted at 14 s (overrides the 10 s RPC deadline).
    pub async fn heartbeat(h: &Harness, ns: &str) -> R {
        let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
        let req = request(pb::StatusRequest {}, &token, ns, "Exporter", "exp");
        let mut stream = deadline("status", h.controller.status(req))
            .await?
            .map_err(|s| format!("status rpc: {s}"))?
            .into_inner();
        let _first = deadline("status-initial", stream.next())
            .await?
            .ok_or("status stream closed before initial event")?
            .map_err(|s| format!("status stream error: {s}"))?;

        let before = h
            .exporters(ns)
            .get("exp")
            .await
            .map_err(|e| e.to_string())?
            .status
            .and_then(|s| s.last_seen);
        // Wait past one heartbeat tick (+10s). Hold `stream` so the spawned
        // status task keeps stamping.
        deadline_for(
            "heartbeat-wait",
            Duration::from_secs(14),
            tokio::time::sleep(Duration::from_secs(11)),
        )
        .await?;
        let after = h
            .exporters(ns)
            .get("exp")
            .await
            .map_err(|e| e.to_string())?
            .status
            .and_then(|s| s.last_seen);
        drop(stream);

        let b = before.ok_or("no lastSeen before")?;
        let a = after.ok_or("no lastSeen after")?;
        want(
            a.0.as_second() > b.0.as_second(),
            format!("lastSeen not advanced by heartbeat: {} -> {}", b.0, a.0),
        )
    }
}

// ===========================================================================
// client_service
// ===========================================================================
mod client_service {
    use super::*;

    /// CreateLease → GetLease → DeleteLease: UUIDv7 name, AIP round-trip,
    /// soft-delete (spec.release), clientRef from the caller.
    pub async fn lease_lifecycle(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
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
            &token,
            ns,
            "Client",
            "cli",
        );
        let created = deadline("create_lease", h.client_svc.create_lease(create_req))
            .await?
            .map_err(|s| format!("create_lease rpc: {s}"))?
            .into_inner();

        let prefix = format!("namespaces/{ns}/leases/");
        want(
            created.name.starts_with(&prefix),
            format!("lease name {:?} lacks AIP prefix", created.name),
        )?;
        let lease_id = created.name.trim_start_matches(&prefix).to_string();
        let parsed =
            uuid::Uuid::parse_str(&lease_id).map_err(|e| format!("lease id not a UUID: {e}"))?;
        want(
            parsed.get_version_num() == 7,
            format!("lease id not UUIDv7 (v{})", parsed.get_version_num()),
        )?;
        let raw = h
            .leases(ns)
            .get(&lease_id)
            .await
            .map_err(|e| e.to_string())?;
        want(
            raw.spec.client_ref.name == "cli",
            format!("clientRef != cli: {}", raw.spec.client_ref.name),
        )?;

        let get_req = request(
            cpb::GetLeaseRequest {
                name: created.name.clone(),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        let got = deadline("get_lease", h.client_svc.get_lease(get_req))
            .await?
            .map_err(|s| format!("get_lease rpc: {s}"))?
            .into_inner();
        want(got.name == created.name, "GetLease name mismatch")?;
        want(
            got.selector == "dut=a",
            format!("selector: {:?}", got.selector),
        )?;

        let del_req = request(
            cpb::DeleteLeaseRequest {
                name: created.name.clone(),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        deadline("delete_lease", h.client_svc.delete_lease(del_req))
            .await?
            .map_err(|s| format!("delete_lease rpc: {s}"))?;
        let after = h
            .leases(ns)
            .get(&lease_id)
            .await
            .map_err(|e| e.to_string())?;
        want(after.spec.release, "spec.release not set by DeleteLease")
    }

    /// AIP name parsing: the three identifier errors, verbatim.
    pub async fn aip_name_parsing(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        let cases: &[(&str, &str)] = &[
            (
                "namespaces/ns/leases",
                "invalid number of segments in identifier \"namespaces/ns/leases\", expecting 4, got 3",
            ),
            (
                "nope/ns/leases/l",
                "invalid first segment in identifier \"nope/ns/leases/l\", expecting \"namespaces\", got \"nope\"",
            ),
            (
                "namespaces/ns/pods/l",
                "invalid third segment in identifier \"namespaces/ns/pods/l\", expecting \"leases\", got \"pods\"",
            ),
        ];
        for (name, expected) in cases {
            let req = request(
                cpb::GetLeaseRequest {
                    name: (*name).to_string(),
                },
                &token,
                ns,
                "Client",
                "cli",
            );
            let err = deadline("get_lease", h.client_svc.get_lease(req))
                .await?
                .err()
                .ok_or_else(|| format!("GetLease {name:?} unexpectedly succeeded"))?;
            want(
                err.code() == Code::InvalidArgument,
                format!("{name:?} code: {:?}", err.code()),
            )?;
            want(
                err.message() == *expected,
                format!("{name:?} message: {:?}", err.message()),
            )?;
        }
        Ok(())
    }

    /// only_active defaults to true (nil-or-true): an ended lease is filtered by
    /// default, and returned when only_active=false.
    pub async fn only_active(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        // One ended lease (carries the lease-ended label) and one active.
        create_labeled_lease(
            h,
            ns,
            "ended-lease",
            "cli",
            &[("jumpstarter.dev/lease-ended", "true")],
        )
        .await?;
        create_labeled_lease(h, ns, "active-lease", "cli", &[]).await?;

        // only_active nil → defaults true → ended filtered out.
        let list_default = deadline(
            "list_leases",
            h.client_svc.list_leases(request(
                cpb::ListLeasesRequest {
                    parent: format!("namespaces/{ns}"),
                    only_active: None,
                    ..Default::default()
                },
                &token,
                ns,
                "Client",
                "cli",
            )),
        )
        .await?
        .map_err(|s| format!("list_leases (default) rpc: {s}"))?
        .into_inner();
        let names: Vec<&str> = list_default
            .leases
            .iter()
            .filter_map(|l| l.name.rsplit('/').next())
            .collect();
        want(
            names.contains(&"active-lease") && !names.contains(&"ended-lease"),
            format!("only_active nil should hide ended lease; got {names:?}"),
        )?;

        // only_active false → both returned.
        let list_all = deadline(
            "list_leases",
            h.client_svc.list_leases(request(
                cpb::ListLeasesRequest {
                    parent: format!("namespaces/{ns}"),
                    only_active: Some(false),
                    ..Default::default()
                },
                &token,
                ns,
                "Client",
                "cli",
            )),
        )
        .await?
        .map_err(|s| format!("list_leases (all) rpc: {s}"))?
        .into_inner();
        let all_names: Vec<&str> = list_all
            .leases
            .iter()
            .filter_map(|l| l.name.rsplit('/').next())
            .collect();
        want(
            all_names.contains(&"active-lease") && all_names.contains(&"ended-lease"),
            format!("only_active=false should return both; got {all_names:?}"),
        )
    }

    /// CreateLease tags are split into a prefixed ObjectMeta label
    /// (`metadata.jumpstarter.dev/<k>`) and unprefixed `spec.tags`.
    pub async fn tag_prefixing(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        let mut tags = std::collections::HashMap::new();
        tags.insert("env".to_string(), "prod".to_string());
        let req = request(
            cpb::CreateLeaseRequest {
                parent: format!("namespaces/{ns}"),
                lease_id: String::new(),
                lease: Some(cpb::Lease {
                    selector: "dut=a".to_string(),
                    tags,
                    duration: Some(prost_types::Duration {
                        seconds: 60,
                        nanos: 0,
                    }),
                    ..Default::default()
                }),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        let created = deadline("create_lease", h.client_svc.create_lease(req))
            .await?
            .map_err(|s| format!("create_lease rpc: {s}"))?
            .into_inner();
        let lease_id = created
            .name
            .rsplit('/')
            .next()
            .unwrap_or_default()
            .to_string();
        let raw = h
            .leases(ns)
            .get(&lease_id)
            .await
            .map_err(|e| e.to_string())?;
        let lbls = raw.metadata.labels.clone().unwrap_or_default();
        want(
            lbls.get("metadata.jumpstarter.dev/env").map(String::as_str) == Some("prod"),
            format!("prefixed tag label missing: {lbls:?}"),
        )?;
        let spec_tags = raw.spec.tags.clone().unwrap_or_default();
        want(
            spec_tags.get("env").map(String::as_str) == Some("prod"),
            format!("spec.tags env != prod: {spec_tags:?}"),
        )
    }

    /// Soft-delete is idempotent: a re-release →
    /// `FAILED_PRECONDITION "lease %q has already been released"`.
    pub async fn soft_delete_idempotent(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        let created = deadline(
            "create_lease",
            h.client_svc.create_lease(request(
                cpb::CreateLeaseRequest {
                    parent: format!("namespaces/{ns}"),
                    lease_id: String::new(),
                    lease: Some(cpb::Lease {
                        selector: "dut=a".to_string(),
                        duration: Some(prost_types::Duration {
                            seconds: 60,
                            nanos: 0,
                        }),
                        ..Default::default()
                    }),
                },
                &token,
                ns,
                "Client",
                "cli",
            )),
        )
        .await?
        .map_err(|s| format!("create_lease rpc: {s}"))?
        .into_inner();

        deadline(
            "delete_lease",
            h.client_svc.delete_lease(request(
                cpb::DeleteLeaseRequest {
                    name: created.name.clone(),
                },
                &token,
                ns,
                "Client",
                "cli",
            )),
        )
        .await?
        .map_err(|s| format!("first delete_lease rpc: {s}"))?;

        let err = deadline(
            "delete_lease",
            h.client_svc.delete_lease(request(
                cpb::DeleteLeaseRequest {
                    name: created.name.clone(),
                },
                &token,
                ns,
                "Client",
                "cli",
            )),
        )
        .await?
        .err()
        .ok_or("second DeleteLease unexpectedly succeeded")?;
        want(
            err.code() == Code::FailedPrecondition,
            format!("code: {:?}", err.code()),
        )?;
        let expected = format!("lease {:?} has already been released", created.name);
        want(
            err.message() == expected,
            format!("message: {:?} (want {expected:?})", err.message()),
        )
    }

    /// RotateToken mints a fresh token and patches the credential secret's
    /// `token` key.
    pub async fn rotate_token(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        h.make_client_secret(ns, "cli").await?;

        let resp = deadline(
            "rotate_token",
            h.client_svc.rotate_token(request(
                cpb::RotateTokenRequest {
                    parent: format!("namespaces/{ns}"),
                },
                &token,
                ns,
                "Client",
                "cli",
            )),
        )
        .await?
        .map_err(|s| format!("rotate_token rpc: {s}"))?
        .into_inner();
        want(!resp.token.is_empty(), "RotateToken returned empty token")?;

        let secret = h
            .secrets(ns)
            .get("cli-client")
            .await
            .map_err(|e| e.to_string())?;
        let stored = secret
            .data
            .as_ref()
            .and_then(|d| d.get("token"))
            .map(|b| String::from_utf8_lossy(&b.0).into_owned())
            .unwrap_or_default();
        want(
            stored == resp.token,
            "secret token not updated to the rotated token",
        )?;
        want(stored != "old-token", "secret token was not rotated")
    }

    async fn create_labeled_lease(
        h: &Harness,
        ns: &str,
        name: &str,
        client_name: &str,
        lbls: &[(&str, &str)],
    ) -> R {
        let api: Api<Lease> = Api::namespaced(h.client.clone(), ns);
        // Non-empty selector to satisfy the Lease CEL rule; the ended/active
        // distinction is carried by the ObjectMeta label, not the selector.
        let mut lease = Lease::new(
            name,
            LeaseSpec {
                client_ref: LocalObjectReference {
                    name: client_name.to_string(),
                },
                selector: match_labels_selector(&[("dut", "a")]),
                ..Default::default()
            },
        );
        lease.metadata = ObjectMeta {
            name: Some(name.to_string()),
            namespace: Some(ns.to_string()),
            labels: Some(labels(lbls)),
            ..Default::default()
        };
        api.create(&PostParams::default(), &lease)
            .await
            .map(|_| ())
            .map_err(|e| format!("create labeled lease {name}: {e}"))
    }
}

// ===========================================================================
// auth
// ===========================================================================
mod auth {
    use super::*;

    /// A non-`Bearer` authorization header → `INVALID_ARGUMENT "malformed
    /// authorization header"`.
    pub async fn malformed_header(h: &Harness, ns: &str) -> R {
        let req = request_with(pb::RegisterRequest::default(), |md| {
            md.insert("authorization", "Basic Zm9v".parse().unwrap());
            md.insert("jumpstarter-namespace", ns.parse().unwrap());
            md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
            md.insert("jumpstarter-name", "exp".parse().unwrap());
        });
        let err = deadline("register", h.controller.register(req))
            .await?
            .err()
            .ok_or("malformed header unexpectedly accepted")?;
        want(
            err.code() == Code::InvalidArgument,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "malformed authorization header",
            format!("message: {:?}", err.message()),
        )
    }

    /// Two authorization headers → `INVALID_ARGUMENT "multiple authorization
    /// headers"`.
    pub async fn multiple_headers(h: &Harness, ns: &str) -> R {
        let req = request_with(pb::RegisterRequest::default(), |md| {
            md.append("authorization", "Bearer a".parse().unwrap());
            md.append("authorization", "Bearer b".parse().unwrap());
            md.insert("jumpstarter-namespace", ns.parse().unwrap());
            md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
            md.insert("jumpstarter-name", "exp".parse().unwrap());
        });
        let err = deadline("register", h.controller.register(req))
            .await?
            .err()
            .ok_or("multiple auth headers unexpectedly accepted")?;
        want(
            err.code() == Code::InvalidArgument,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "multiple authorization headers",
            format!("message: {:?}", err.message()),
        )
    }

    /// A valid Client token used on an Exporter RPC (metadata kind=Client) →
    /// `INVALID_ARGUMENT "object kind mismatch"`.
    pub async fn object_kind_mismatch(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        // Register (an Exporter RPC) but declare kind=Client in metadata.
        let req = request(pb::RegisterRequest::default(), &token, ns, "Client", "cli");
        let err = deadline("register", h.controller.register(req))
            .await?
            .err()
            .ok_or("kind mismatch unexpectedly accepted")?;
        want(
            err.code() == Code::InvalidArgument,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "object kind mismatch",
            format!("message: {:?}", err.message()),
        )
    }

    /// A missing required attribute metadata key → `INVALID_ARGUMENT "missing
    /// metadata: jumpstarter-namespace"`.
    pub async fn missing_namespace_metadata(h: &Harness, ns: &str) -> R {
        let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
        let req = request_with(pb::RegisterRequest::default(), |md| {
            md.insert("authorization", format!("Bearer {token}").parse().unwrap());
            md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
            md.insert("jumpstarter-name", "exp".parse().unwrap());
        });
        let err = deadline("register", h.controller.register(req))
            .await?
            .err()
            .ok_or("missing namespace metadata unexpectedly accepted")?;
        want(
            err.code() == Code::InvalidArgument,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "missing metadata: jumpstarter-namespace",
            format!("message: {:?}", err.message()),
        )
    }

    /// An internal token without a provided resource name → `INVALID_ARGUMENT
    /// "resource name required for pre-existing authentication"`.
    pub async fn resource_name_required(h: &Harness, ns: &str) -> R {
        let (_e, token) = h.make_exporter(ns, "exp", &[]).await?;
        let req = request_with(pb::RegisterRequest::default(), |md| {
            md.insert("authorization", format!("Bearer {token}").parse().unwrap());
            md.insert("jumpstarter-namespace", ns.parse().unwrap());
            md.insert("jumpstarter-kind", "Exporter".parse().unwrap());
            // no jumpstarter-name
        });
        let err = deadline("register", h.controller.register(req))
            .await?
            .err()
            .ok_or("missing resource name unexpectedly accepted")?;
        want(
            err.code() == Code::InvalidArgument,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "resource name required for pre-existing authentication",
            format!("message: {:?}", err.message()),
        )
    }

    /// A ClientService call whose `parent` namespace disagrees with the
    /// authenticated client's namespace → `PERMISSION_DENIED "namespace
    /// mismatch"`.
    pub async fn namespace_mismatch(h: &Harness, ns: &str) -> R {
        let (_c, token) = h.make_client(ns, "cli").await?;
        // Authenticate against the client's real namespace (metadata ns), but ask
        // for a lease in a different parent namespace.
        let req = request(
            cpb::CreateLeaseRequest {
                parent: "namespaces/other-namespace".to_string(),
                lease_id: String::new(),
                lease: Some(cpb::Lease {
                    selector: "dut=a".to_string(),
                    ..Default::default()
                }),
            },
            &token,
            ns,
            "Client",
            "cli",
        );
        let err = deadline("create_lease", h.client_svc.create_lease(req))
            .await?
            .err()
            .ok_or("namespace mismatch unexpectedly accepted")?;
        want(
            err.code() == Code::PermissionDenied,
            format!("code: {:?}", err.code()),
        )?;
        want(
            err.message() == "namespace mismatch",
            format!("message: {:?}", err.message()),
        )
    }
}

// ===========================================================================
// runner
// ===========================================================================

/// Run every conformance case (one fresh namespace each) and return the results
/// table. Cases are ordered by module; the heartbeat case (~11 s) runs last.
pub async fn run_all(env: &TestEnv, h: &Harness) -> Vec<(&'static str, R)> {
    let mut results: Vec<(&'static str, R)> = Vec::new();

    macro_rules! run {
        ($name:literal, $ns:literal, $f:expr) => {{
            let r = match env.create_namespace($ns).await {
                Ok(()) => $f(h, $ns).await,
                Err(e) => Err(format!("create namespace {}: {e}", $ns)),
            };
            results.push(($name, r));
        }};
    }

    // -- error_strings ------------------------------------------------------
    run!(
        "error_strings/empty_lease_name",
        "c01",
        error_strings::empty_lease_name
    );
    run!(
        "error_strings/permission_denied",
        "c02",
        error_strings::permission_denied
    );
    run!(
        "error_strings/lease_not_active",
        "c03",
        error_strings::lease_not_active
    );
    run!(
        "error_strings/no_router_available",
        "c04",
        error_strings::no_router_available
    );
    run!(
        "error_strings/exporter_not_listening",
        "c05",
        error_strings::exporter_not_listening
    );
    run!(
        "error_strings/listener_buffer_full",
        "c06",
        error_strings::listener_buffer_full
    );
    run!(
        "error_strings/invalid_argument_create_lease",
        "c07",
        error_strings::invalid_argument_create_lease
    );
    run!(
        "error_strings/missing_authorization_header",
        "c08",
        error_strings::missing_authorization_header
    );
    run!(
        "error_strings/token_expired",
        "c09",
        error_strings::token_expired
    );
    run!(
        "error_strings/apiserver_notfound_verbatim",
        "c10",
        error_strings::apiserver_notfound_verbatim
    );

    // -- register / report_status ------------------------------------------
    run!(
        "register/label_prefix_and_devices",
        "c11",
        register::label_prefix_and_devices
    );
    run!(
        "report_status/enum_and_lastseen",
        "c12",
        report_status::enum_and_lastseen
    );
    run!(
        "report_status/release_side_effect",
        "c13",
        report_status::release_side_effect
    );
    run!(
        "report_status/release_never_fails",
        "c14",
        report_status::release_never_fails
    );

    // -- listen_dial --------------------------------------------------------
    run!("listen_dial/handshake", "c15", listen_dial::handshake);
    run!(
        "listen_dial/retry_gate_success",
        "c16",
        listen_dial::retry_gate_success
    );
    run!(
        "listen_dial/gate_non_retryable_offline",
        "c17",
        listen_dial::gate_non_retryable_offline
    );
    run!("listen_dial/supersession", "c18", listen_dial::supersession);

    // -- client_service -----------------------------------------------------
    run!(
        "client_service/lease_lifecycle",
        "c19",
        client_service::lease_lifecycle
    );
    run!(
        "client_service/aip_name_parsing",
        "c20",
        client_service::aip_name_parsing
    );
    run!(
        "client_service/only_active",
        "c21",
        client_service::only_active
    );
    run!(
        "client_service/tag_prefixing",
        "c22",
        client_service::tag_prefixing
    );
    run!(
        "client_service/soft_delete_idempotent",
        "c23",
        client_service::soft_delete_idempotent
    );
    run!(
        "client_service/rotate_token",
        "c24",
        client_service::rotate_token
    );

    // -- auth ---------------------------------------------------------------
    run!("auth/malformed_header", "c25", auth::malformed_header);
    run!("auth/multiple_headers", "c26", auth::multiple_headers);
    run!(
        "auth/object_kind_mismatch",
        "c27",
        auth::object_kind_mismatch
    );
    run!(
        "auth/missing_namespace_metadata",
        "c28",
        auth::missing_namespace_metadata
    );
    run!(
        "auth/resource_name_required",
        "c29",
        auth::resource_name_required
    );
    run!("auth/namespace_mismatch", "c30", auth::namespace_mismatch);

    // -- status_stream (initial/dedup fast, heartbeat ~11s last) -----------
    run!(
        "status_stream/initial_event",
        "c31",
        status_stream::initial_event
    );
    run!("status_stream/dedup", "c32", status_stream::dedup);
    run!("status_stream/heartbeat", "c33", status_stream::heartbeat);

    results
}
