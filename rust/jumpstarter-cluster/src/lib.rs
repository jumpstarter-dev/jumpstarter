//! `jumpstarter-cluster` — local dev-cluster provisioning for `jmp admin
//! {create,delete,get} cluster` / `get clusters`. A clean Rust port of the
//! Python `jumpstarter_kubernetes/cluster/` orchestration: it shells out to
//! `kind`/`minikube`/`k3s`/`kubectl` and installs the Jumpstarter operator.
//!
//! The crate is the domain layer (all subprocess/orchestration logic); the `jmp`
//! CLI is the thin clap + output-rendering layer over it (mirroring how
//! `jumpstarter-admin` relates to `jmp admin`).

pub mod command;
pub mod common;
pub mod create;
pub mod detection;
pub mod endpoints;
pub mod error;
pub mod info;
pub mod kind;
pub mod kubectl;
pub mod minikube;
pub mod operations;
pub mod operator;
pub mod progress;
pub mod version;

pub use create::{create_cluster_and_install, CreateOptions};
pub use error::{ClusterError, Result};
pub use info::{ClusterInfo, ClusterList, JumpstarterInstance};
pub use kubectl::{get_cluster_info, list_clusters};
pub use operations::{delete_cluster_by_name, validate_cluster_type_selection};
pub use progress::{Progress, Silent};
pub use version::get_latest_compatible_controller_version;
