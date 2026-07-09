//! The exporter `Status` server-stream, a port of `ControllerService.Status`
//! (`controller/internal/service/controller_service.go` ~610-743).
//!
//! The stream serves two jobs (Go doc comment): tell the exporter the current
//! lease status, and track the exporter's last-seen time (liveness). The wire
//! contract has three load-bearing pieces we reproduce exactly:
//!
//! 1. **v0.7.x back-compat on connect** — merge-patch `status.lastSeen = now`
//!    and, if the reconciler had marked the exporter `Offline` while it was
//!    briefly disconnected, clear `exporterStatus`/`statusMessage`. Old (v0.7.x)
//!    exporters never call `ReportStatus`, so they cannot clear their own
//!    `Offline` — the controller does it for them the moment the Status stream
//!    reconnects.
//! 2. **Explicit initial event, then a raw watch** — Go's controller-runtime
//!    watch replays the current object as an `Added` event, which is what emits
//!    the first `StatusResponse`. kube's `kube::runtime::watcher` would *also*
//!    silently auto-restart on disconnect, which would defeat the reconnect
//!    contract (a wedged watch must terminate the RPC so the exporter dials a
//!    fresh one). So we do an explicit [`Api::get`] to emit the initial event,
//!    then a **raw [`Api::watch`]** from that object's `resourceVersion` — no
//!    auto-restart. A watch that ends or errors ⇒ the RPC returns `Err`.
//! 3. **10 s heartbeat + dead-watch watchdog** — a 10 s ticker
//!    (`MissedTickBehavior::Delay`) re-stamps `lastSeen`; if the value the watch
//!    last reported has fallen behind the value we last wrote, the k8s watch has
//!    silently wedged and we terminate with `"last seen time mismatch"`.
//!
//! Like [`crate::listen_registry::drive_listen_loop`], the loop is driven
//! through an outbound [`mpsc::Sender`] backing the gRPC response stream; its
//! [`mpsc::Sender::closed`] future is our equivalent of Go's `<-ctx.Done()`.
//! The terminal `Err(Status)` is returned for the `Status` RPC handler to place
//! on the stream as its final status (mirroring Go's `return err`).

use std::time::Duration;

use futures::StreamExt;
use jumpstarter_controller_api::exporter::Exporter;
use jumpstarter_controller_api::lease::Lease;
use jumpstarter_protocol::v1::StatusResponse;
use k8s_openapi::apimachinery::pkg::apis::meta::v1::Time;
use k8s_openapi::jiff::Timestamp;
use kube::api::{Patch, PatchParams, WatchParams};
use kube::core::WatchEvent;
use kube::{Api, ResourceExt};
use tokio::sync::mpsc;
use tokio::time::MissedTickBehavior;

/// Heartbeat cadence. **Contractual** against the reconciler's 1-minute offline
/// threshold (Go `time.NewTicker(time.Second * 10)`): the exporter must be
/// re-stamped several times inside that window so a live exporter never flaps
/// `Offline`.
pub const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(10);

/// Run the `Status` server-stream for one exporter.
///
/// `exporter_name` is the authenticated exporter's name (the caller resolves it
/// via `authenticateExporter` before calling); `exporters`/`leases` are
/// namespaced [`Api`]s for the exporter's namespace. `outbound` feeds the gRPC
/// response stream. On a clean client disconnect this returns `Ok(())`; on a
/// watch failure or the dead-watch watchdog it returns `Err(Status)` for the
/// handler to surface as the RPC's terminal status.
pub async fn run_status_stream(
    exporters: Api<Exporter>,
    leases: Api<Lease>,
    exporter_name: &str,
    outbound: mpsc::Sender<Result<StatusResponse, tonic::Status>>,
) -> Result<(), tonic::Status> {
    // Fetch the current object: gives us the offline-clear decision, the initial
    // StatusResponse, and the resourceVersion to start the raw watch from.
    let exporter = exporters
        .get(exporter_name)
        .await
        .map_err(|e| kube_status("unable to get exporter", e))?;
    let resource_version = exporter.resource_version().unwrap_or_default();

    // (1) Connect patch: stamp lastSeen and clear a stale Offline for v0.7.x.
    let now = truncate_to_seconds(Timestamp::now());
    let clear_offline = should_clear_offline(&exporter);
    let patch = connect_patch(&Time(now), clear_offline);
    if let Err(e) = exporters
        .patch_status(
            exporter_name,
            &PatchParams::default(),
            &Patch::Merge(&patch),
        )
        .await
    {
        // Go logs and continues on the initial-connect patch failure.
        tracing::warn!(error = %e, "unable to update exporter status on initial connect");
    }

    // (2) Initial event: build from the current object and send explicitly.
    let initial = resolve_status(&exporter, &leases).await?;
    if outbound.send(Ok(initial.clone())).await.is_err() {
        return Ok(()); // client already gone
    }
    let mut last_response = Some(initial);

    // Watchdog trackers. Seed both with the lastSeen we just wrote so the
    // watchdog is armed immediately (Go gets this from the watch's replayed
    // Added event; our raw watch-from-resourceVersion instead relies on the
    // seed, and the replayed Modified for the connect patch merely refreshes it).
    let mut watched_last_seen: Option<Time> = Some(Time(now));
    let mut local_last_seen: Option<Time> = Some(Time(now));

    // (2 cont.) Raw watch from the fetched resourceVersion — NOT
    // kube::runtime::watcher; a silent restart would erase the reconnect
    // contract.
    let wp = WatchParams::default().fields(&format!("metadata.name={exporter_name}"));
    let stream = exporters
        .watch(&wp, &resource_version)
        .await
        .map_err(|e| kube_status("failed to watch exporter", e))?;
    futures::pin_mut!(stream);

    // (3) Heartbeat ticker. tokio's interval fires immediately on the first
    // tick; consume it so the first in-loop tick lands at +10s (the connect
    // patch above already served as the initial online() — Go golang/go#17601).
    let mut ticker = tokio::time::interval(HEARTBEAT_INTERVAL);
    ticker.set_missed_tick_behavior(MissedTickBehavior::Delay);
    ticker.tick().await;

    loop {
        tokio::select! {
            // Client (exporter) disconnected — Go `case <-ctx.Done(): return nil`.
            _ = outbound.closed() => return Ok(()),

            // Heartbeat: watchdog check, then re-stamp lastSeen.
            _ = ticker.tick() => {
                if watchdog_tripped(watched_last_seen.as_ref(), local_last_seen.as_ref()) {
                    tracing::info!("the exporter watcher seems to have stopped, terminating status stream");
                    return Err(watch_terminal("last seen time mismatch"));
                }
                let now = truncate_to_seconds(Timestamp::now());
                let patch = online_patch(&Time(now));
                if let Err(e) = exporters
                    .patch_status(exporter_name, &PatchParams::default(), &Patch::Merge(&patch))
                    .await
                {
                    tracing::warn!(error = %e, "unable to update exporter status.lastSeen");
                }
                local_last_seen = Some(Time(now));
            }

            // Watch event.
            event = stream.next() => {
                match event {
                    // Stream ended: exporter must reconnect. NEVER auto-restart.
                    None => {
                        tracing::info!("watch channel closed, terminating status stream");
                        return Err(watch_terminal("watch channel closed"));
                    }
                    Some(Err(e)) => {
                        return Err(kube_status("received error when watching exporter", e));
                    }
                    Some(Ok(WatchEvent::Error(e))) => {
                        tracing::error!(error = ?e, "received error when watching exporter");
                        return Err(watch_terminal("received error when watching exporter"));
                    }
                    // Bookmarks carry only a resourceVersion; ignore.
                    Some(Ok(WatchEvent::Bookmark(_))) => {}
                    Some(Ok(
                        WatchEvent::Added(ex)
                        | WatchEvent::Modified(ex)
                        | WatchEvent::Deleted(ex),
                    )) => {
                        // Track the last-seen reported by the watch, so a wedged
                        // watch is caught by the heartbeat watchdog.
                        let seen = ex
                            .status
                            .as_ref()
                            .and_then(|s| s.last_seen.clone());
                        watched_last_seen = seen.clone();
                        // Go also assigns exporter = result.Object, so local
                        // last-seen follows the watch when an event arrives.
                        if let Some(seen) = seen {
                            local_last_seen = Some(seen);
                        }

                        let response = resolve_status(&ex, &leases).await?;
                        // Dedup via prost's PartialEq (Go proto.Equal).
                        if last_response.as_ref() != Some(&response) {
                            if outbound.send(Ok(response.clone())).await.is_err() {
                                return Ok(()); // client gone mid-send
                            }
                            last_response = Some(response);
                        }
                    }
                }
            }
        }
    }
}

/// Build a `StatusResponse` for an exporter, resolving the connected client name
/// through the referenced [`Lease`]. Port of the response-build block in
/// `Status` (controller_service.go:704-726).
async fn resolve_status(
    exporter: &Exporter,
    leases: &Api<Lease>,
) -> Result<StatusResponse, tonic::Status> {
    let lease_ref = exporter.status.as_ref().and_then(|s| s.lease_ref.as_ref());

    let (leased, lease_name, client_name) = match lease_ref {
        None => (false, None, None),
        Some(lref) => {
            let lease = leases
                .get(&lref.name)
                .await
                .map_err(|e| kube_status("failed to get lease on exporter", e))?;
            (
                true,
                Some(lref.name.clone()),
                Some(lease.spec.client_ref.name.clone()),
            )
        }
    };

    Ok(StatusResponse {
        leased,
        lease_name,
        client_name,
    })
}

/// Whether the connect patch should clear a stale `Offline` status.
/// Port of the `exporter.Status.ExporterStatusValue == ExporterStatusOffline`
/// check in the connect block (controller_service.go:665).
fn should_clear_offline(exporter: &Exporter) -> bool {
    use jumpstarter_controller_api::exporter::ExporterStatusValue;
    exporter.status.as_ref().and_then(|s| s.exporter_status) == Some(ExporterStatusValue::Offline)
}

/// The connect-time status merge patch: always stamp `lastSeen`; when clearing a
/// stale `Offline`, delete `exporterStatus`/`statusMessage` (JSON merge-patch
/// `null` = delete, matching Go's `""` + `omitempty` diffing to `null`).
fn connect_patch(now: &Time, clear_offline: bool) -> serde_json::Value {
    let mut status = serde_json::Map::new();
    status.insert(
        "lastSeen".to_string(),
        serde_json::to_value(now).expect("Time serializes"),
    );
    if clear_offline {
        status.insert("exporterStatus".to_string(), serde_json::Value::Null);
        status.insert("statusMessage".to_string(), serde_json::Value::Null);
    }
    serde_json::json!({ "status": status })
}

/// The heartbeat status merge patch: just re-stamp `lastSeen`. Port of the
/// `online()` closure (controller_service.go:643-650).
fn online_patch(now: &Time) -> serde_json::Value {
    serde_json::json!({ "status": { "lastSeen": now } })
}

/// The dead-watch watchdog predicate. Port of
/// `watchedLastSeen != nil && !watchedLastSeen.Equal(&exporter.Status.LastSeen)`
/// (controller_service.go:685): the watch has wedged if it reported a value that
/// has since fallen behind the value we last wrote via `online()`.
fn watchdog_tripped(watched: Option<&Time>, local: Option<&Time>) -> bool {
    matches!(watched, Some(w) if Some(w) != local)
}

/// Truncate a [`Timestamp`] to whole seconds, matching Go `metav1.Time`'s
/// RFC3339 (second-precision) marshaling. Both the value we patch and the value
/// the watch reports are then second-precision, so the watchdog's [`Time`]
/// equality round-trips.
fn truncate_to_seconds(ts: Timestamp) -> Timestamp {
    Timestamp::from_second(ts.as_second()).expect("seconds in range")
}

/// Map a `kube` error to a gRPC status. Go returns the raw error, which gRPC
/// wraps as `codes.Unknown` with the error text; we mirror the code but the
/// exact message text is a documented divergence (kube's error strings differ
/// from client-go's).
fn kube_status(context: &str, err: kube::Error) -> tonic::Status {
    tonic::Status::unknown(format!("{context}: {err}"))
}

/// Terminal error for the three literal-message watch-loop failure paths
/// (watchdog / channel-closed / watch-error). Go returns each as a plain
/// `fmt.Errorf(...)` (controller_service.go:687,694,739); grpc-go surfaces a
/// non-`status.Status` error from a stream handler as `codes.Unknown` (2) with
/// the message text verbatim — **not** `codes.Internal` (13). We mirror both the
/// code and Go's deterministic message.
fn watch_terminal(message: &'static str) -> tonic::Status {
    tonic::Status::unknown(message)
}

#[cfg(test)]
mod tests {
    use super::*;
    use jumpstarter_controller_api::exporter::{ExporterSpec, ExporterStatus, ExporterStatusValue};
    use k8s_openapi::api::core::v1::LocalObjectReference;

    fn ts(secs: i64) -> Time {
        Time(Timestamp::from_second(secs).unwrap())
    }

    fn exporter_with(status: Option<ExporterStatus>) -> Exporter {
        let mut e = Exporter::new("ex", ExporterSpec::default());
        e.status = status;
        e
    }

    // ---- offline-clear decision (v0.7.x back-compat) ----

    // go: controller_service.go:665
    #[test]
    fn clears_offline_when_status_is_offline() {
        let e = exporter_with(Some(ExporterStatus {
            exporter_status: Some(ExporterStatusValue::Offline),
            ..Default::default()
        }));
        assert!(should_clear_offline(&e));
    }

    #[test]
    fn does_not_clear_when_online() {
        let e = exporter_with(Some(ExporterStatus {
            exporter_status: Some(ExporterStatusValue::LeaseReady),
            ..Default::default()
        }));
        assert!(!should_clear_offline(&e));
    }

    #[test]
    fn does_not_clear_when_status_absent() {
        assert!(!should_clear_offline(&exporter_with(None)));
    }

    // ---- patch shapes ----

    #[test]
    fn connect_patch_without_offline_only_stamps_last_seen() {
        let p = connect_patch(&ts(1_700_000_000), false);
        let status = &p["status"];
        assert_eq!(status["lastSeen"], "2023-11-14T22:13:20Z");
        assert!(status.get("exporterStatus").is_none());
        assert!(status.get("statusMessage").is_none());
    }

    #[test]
    fn connect_patch_with_offline_deletes_status_fields() {
        let p = connect_patch(&ts(1_700_000_000), true);
        let status = &p["status"];
        assert_eq!(status["lastSeen"], "2023-11-14T22:13:20Z");
        // JSON merge-patch null = delete.
        assert_eq!(status["exporterStatus"], serde_json::Value::Null);
        assert_eq!(status["statusMessage"], serde_json::Value::Null);
    }

    #[test]
    fn online_patch_only_stamps_last_seen() {
        let p = online_patch(&ts(1_700_000_050));
        assert_eq!(p["status"]["lastSeen"], "2023-11-14T22:14:10Z");
        assert_eq!(p["status"].as_object().unwrap().len(), 1);
    }

    // ---- watchdog predicate ----

    // go: controller_service.go:685
    #[test]
    fn watchdog_not_tripped_when_watch_never_reported() {
        // watchedLastSeen == nil ⇒ never trips.
        assert!(!watchdog_tripped(None, Some(&ts(1_700_000_000))));
    }

    #[test]
    fn watchdog_not_tripped_when_in_sync() {
        assert!(!watchdog_tripped(
            Some(&ts(1_700_000_000)),
            Some(&ts(1_700_000_000))
        ));
    }

    #[test]
    fn watchdog_tripped_when_watch_falls_behind() {
        // We wrote a newer lastSeen than the watch has reported ⇒ wedged.
        assert!(watchdog_tripped(
            Some(&ts(1_700_000_000)),
            Some(&ts(1_700_000_010))
        ));
    }

    // ---- terminal error wire code ----

    // go: controller_service.go:687,694,739 — each terminal path is a plain
    // fmt.Errorf(...), which grpc-go surfaces as codes.Unknown (2), NOT
    // codes.Internal (13). Pin the code and the deterministic message.
    #[test]
    fn watch_terminal_errors_use_unknown_code_not_internal() {
        for msg in [
            "last seen time mismatch",
            "watch channel closed",
            "received error when watching exporter",
        ] {
            let s = watch_terminal(msg);
            assert_eq!(s.code(), tonic::Code::Unknown);
            assert_ne!(s.code(), tonic::Code::Internal);
            assert_eq!(s.message(), msg);
        }
    }

    // ---- StatusResponse dedup + truncation ----

    #[test]
    fn truncate_drops_subsecond() {
        // 1_700_000_000.999s worth of nanos → floor to the whole second.
        let t = Timestamp::from_nanosecond(1_700_000_000_999_000_000).unwrap();
        assert_eq!(truncate_to_seconds(t).as_second(), 1_700_000_000);
    }

    #[test]
    fn status_response_partial_eq_dedup() {
        // The dedup relies on prost's derived PartialEq (Go proto.Equal).
        let a = StatusResponse {
            leased: true,
            lease_name: Some("l".into()),
            client_name: Some("c".into()),
        };
        let b = a.clone();
        assert_eq!(Some(&a), Some(&b));
        let c = StatusResponse {
            leased: false,
            ..a.clone()
        };
        assert_ne!(Some(&a), Some(&c));
    }

    #[test]
    fn unleased_exporter_status_fields() {
        // Pure portion of resolve_status: no leaseRef ⇒ not leased, no names.
        let e = exporter_with(Some(ExporterStatus::default()));
        assert!(e.status.as_ref().unwrap().lease_ref.is_none());
    }

    #[test]
    fn leased_exporter_carries_lease_ref() {
        let e = exporter_with(Some(ExporterStatus {
            lease_ref: Some(LocalObjectReference {
                name: "lease-1".into(),
            }),
            ..Default::default()
        }));
        assert_eq!(
            e.status
                .as_ref()
                .and_then(|s| s.lease_ref.as_ref())
                .map(|r| r.name.as_str()),
            Some("lease-1")
        );
    }
}
