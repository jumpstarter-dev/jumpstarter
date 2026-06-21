//! Shared OIDC client + local JWT decoding for Jumpstarter (the Rust equivalent of the Python
//! `jumpstarter_cli_common.oidc` module). Used by the `jmp` CLI (`auth`/`login`) and the MCP
//! server (token refresh before controller calls).

pub mod jwt;
pub mod oidc;
