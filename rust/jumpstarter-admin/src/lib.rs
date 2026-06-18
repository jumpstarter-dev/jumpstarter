//! Jumpstarter admin: Kubernetes CRUD for the `jumpstarter.dev/v1alpha1`
//! Client/Exporter/Lease custom resources, backing `jmp admin`
//! (`python/.../jumpstarter-kubernetes`). Uses kube-rs `DynamicObject` against the
//! CRDs (group `jumpstarter.dev`, version `v1alpha1`) plus the core `Secret` API to
//! read issued credentials.

mod error;
mod k8s;

pub use error::AdminError;
pub use k8s::{JumpstarterAdmin, Kind};
pub use kube::api::DynamicObject;
