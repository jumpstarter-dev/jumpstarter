//! Operational scaffolding shared by the `/manager` and `/router` binaries,
//! mirroring the bootstrap contract of `controller/cmd/main.go` and
//! `controller/cmd/router/main.go`: the operator deploys these binaries with
//! Go-flag-style arguments and a fixed env-var/ConfigMap surface, so this
//! crate reproduces that contract exactly.

pub mod configmap;
pub mod flags;
pub mod health;
pub mod leader;
pub mod logging;
pub mod metrics;
pub mod namespace;
pub mod tls;
