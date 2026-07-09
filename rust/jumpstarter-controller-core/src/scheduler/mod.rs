//! Pure, cluster-free lease scheduling decision core (port of
//! `reconcileStatusExporterRef` / `ReconcileLeaseTimeFields` / scheduling
//! helpers from `controller/internal/controller/lease_controller.go` +
//! `controller/api/v1alpha1/lease_helpers.go`).

pub mod decide;
pub mod expiry;
pub mod selector;
pub mod time_fields;
pub mod views;
