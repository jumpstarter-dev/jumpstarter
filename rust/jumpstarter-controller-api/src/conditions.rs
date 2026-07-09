//! Shared condition-type constants for the `jumpstarter.dev/v1alpha1` API
//! group, ported from `controller/api/v1alpha1/exporter_types.go` and
//! `controller/api/v1alpha1/lease_types.go`.
//!
//! Note: the Go api package defines **no** condition `Reason` constants —
//! reasons are string literals in `controller/internal/controller/*` and are
//! ported alongside the reconcilers (phase 4).

// Exporter condition types.
// go: exporter_types.go:51-56 (ExporterConditionType)

/// Condition type set once the exporter's credential secret has been created.
// go: exporter_types.go:54 (ExporterConditionTypeRegistered)
pub const EXPORTER_CONDITION_TYPE_REGISTERED: &str = "Registered";

/// Condition type tracking whether the exporter is currently connected.
// go: exporter_types.go:55 (ExporterConditionTypeOnline)
pub const EXPORTER_CONDITION_TYPE_ONLINE: &str = "Online";

// Lease condition types.
// go: lease_types.go:71-78 (LeaseConditionType)

/// Condition type set while the lease is waiting for an exporter.
// go: lease_types.go:74 (LeaseConditionTypePending)
pub const LEASE_CONDITION_TYPE_PENDING: &str = "Pending";

/// Condition type set once an exporter has been acquired for the lease.
// go: lease_types.go:75 (LeaseConditionTypeReady)
pub const LEASE_CONDITION_TYPE_READY: &str = "Ready";

/// Condition type set when no exporter can ever satisfy the lease.
// go: lease_types.go:76 (LeaseConditionTypeUnsatisfiable)
pub const LEASE_CONDITION_TYPE_UNSATISFIABLE: &str = "Unsatisfiable";

/// Condition type set when the lease spec itself is invalid.
// go: lease_types.go:77 (LeaseConditionTypeInvalid)
pub const LEASE_CONDITION_TYPE_INVALID: &str = "Invalid";
