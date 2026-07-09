//! Black-box gRPC conformance harness for the controller.
//!
//! Every case arranges CR/secret state via kube, calls a gRPC method, and
//! asserts the exact `(code, details)` plus the resulting CR state. The suite
//! is parameterized by `CONFORMANCE_ENDPOINT` / `CONFORMANCE_ROUTER_ENDPOINT` /
//! `KUBECONFIG` so it can run against BOTH the Go controller (encoding current
//! behavior as executable spec 02) and the Rust controller (the parity gate).

pub mod cases;
pub mod diff;
pub mod harness;
