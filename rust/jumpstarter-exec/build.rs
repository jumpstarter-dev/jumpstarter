//! Embeds the monorepo's Git-derived version into the binary.
//!
//! Jumpstarter has no crates.io-published Rust crates yet, so
//! `Cargo.toml`'s `version` field is a static placeholder shared by the
//! workspace. The actual, human-meaningful version comes from
//! `git describe` (see `controller/Makefile`'s `GIT_VERSION` and
//! `.github/workflows/build-images.yaml`'s `PEP440_VERSION`), the same
//! way the Go controller binaries and Python packages (via `hatch-vcs`)
//! derive their versions.
//!
//! The `python/Containerfile` rust-builder stage forwards `GIT_VERSION`
//! as a build arg / env var so this is picked up automatically when the
//! exporter image is built. Local `cargo build` runs without it fall
//! back to a `-dev` placeholder.

fn main() {
    let version = std::env::var("GIT_VERSION").unwrap_or_else(|_| "0.0.0-dev".to_string());
    println!("cargo:rustc-env=GIT_VERSION={version}");
    println!("cargo:rerun-if-env-changed=GIT_VERSION");
}
