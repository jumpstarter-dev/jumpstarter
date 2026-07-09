//! Controller reconcilers for the `jumpstarter.dev/v1alpha1` resources,
//! mirroring `controller/internal/controller/*_controller.go`.
//!
//! State-machine discipline without a resident typestate machine (approved
//! design): the lease lifecycle is level-triggered — the authoritative state
//! lives in etcd and every reconcile re-derives it — so the scheduler is a
//! **pure decision core** (`scheduler`), an exhaustively-matched classify +
//! `decide()` returning status mutations and requeues, kept free of any kube
//! I/O so it is table-testable without a cluster. The kube-facing reconcilers
//! wrap that core.

pub mod client_reconciler;
pub mod conditions;
pub mod exporter_reconciler;
pub mod lease_reconciler;
pub mod scheduler;
pub mod secret;

use std::sync::Arc;

use jumpstarter_controller_auth::signer::Signer;
use kube::Client;

/// Run all three reconcilers (Exporter, Client, Lease) concurrently against the
/// same kube client, returning only when every controller stream ends. This is
/// the entry point the phase-5 manager bin wires into the leader-gated runnable
/// set (Go: each `*Reconciler.SetupWithManager` registered on the shared
/// manager).
///
/// The Exporter and Client reconcilers mint credential secrets and so need the
/// internal `Signer`; the Lease reconciler needs only the client.
///
/// `namespace` is the resolved single watch namespace (Go: the manager is
/// scoped to one namespace since 0.8.0, cache + RBAC are namespaced). All three
/// controllers watch namespace-scoped `Api`s so a namespaced `Role` (not a
/// `ClusterRole`) is sufficient — a cluster-scoped watch would 403 against the
/// operator-provisioned Role.
pub async fn run(client: Client, signer: Arc<Signer>, namespace: String) {
    tokio::join!(
        exporter_reconciler::run(client.clone(), signer.clone(), namespace.clone()),
        client_reconciler::run(client.clone(), signer, namespace.clone()),
        lease_reconciler::run(client, namespace),
    );
}
