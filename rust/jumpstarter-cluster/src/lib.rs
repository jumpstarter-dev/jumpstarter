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
pub mod detection;
pub mod error;
pub mod info;
pub mod kind;
pub mod kubectl;
pub mod minikube;
pub mod progress;

pub use error::{ClusterError, Result};
pub use info::{ClusterInfo, ClusterList, JumpstarterInstance};
pub use kubectl::{get_cluster_info, list_clusters};
pub use progress::{Progress, Silent};
