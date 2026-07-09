//! The `jumpstarter.dev/v1alpha1` CRD types, ported from
//! `controller/api/v1alpha1/*_types.go` (retained Go tree = behavioral
//! reference and golden-fixture source).
//!
//! Parity contract: `tests/crd_parity.rs` structurally diffs the CRDs
//! generated from these types against the controller-gen YAML in
//! `controller/deploy/operator/config/crd/bases/`. The deployed CRDs remain
//! the Go-generated YAML until the operator phase; generation here is a
//! verification artifact.

pub mod access_policy;
pub mod client;
pub mod conditions;
pub mod device;
pub mod exporter;
pub mod go_duration;
pub mod labels;
pub mod lease;
pub mod schema;
