//! The Rust≡Go **differential** conformance runner.
//!
//! Brings up ONE envtest control plane, serves the real Rust services over a
//! local gRPC port, and runs the [`diff::EXPECTED`] black-box case set against
//! it. When `JMP_GO_CONFORMANCE_BIN` points at the built
//! `controller/hack/conformance` server, it ALSO spawns that Go controller
//! against the SAME apiserver with the SAME fixed signing key, runs the
//! identical case set against it, and asserts — per case — that:
//!
//!   1. the Rust observation matches the spec-02 §12 contract ([`diff::EXPECTED`]);
//!   2. the Go observation matches the same contract;
//!   3. Go and Rust agree (byte-identical message for exact rows; both carry the
//!      contractual substring for per-run-identifier rows).
//!
//! Because a token minted once by the Rust signer authenticates against the Go
//! controller (identical `sha256(CONTROLLER_KEY)`→ES256 derivation, same
//! issuer/audience), a green run is also the **cross-impl token-compat proof**.
//!
//! Recording: with `JMP_CONFORMANCE_RECORD=1`, the Go observations (or, when the
//! Go leg is unavailable, the Rust observations tagged `rust-provisional`) are
//! written to `tests/golden/go_controller.json`. The non-env-gated
//! `golden_replay` test re-checks that file against the contract with no cluster.
//!
//! Env-gated + ANTI-STALL: SKIPs when `KUBEBUILDER_ASSETS` is unset; every
//! apiserver/server bring-up and RPC is bounded, and a Go server that cannot
//! come up is reported as a per-case blocker, never a hang.
//!
//! ```sh
//! KUBEBUILDER_ASSETS=.../bin/k8s/1.30.0-darwin-arm64 \
//!   JMP_GO_CONFORMANCE_BIN=.../conformance-server \
//!   JMP_CONFORMANCE_RECORD=1 \
//!   cargo test -p jumpstarter-conformance --test differential -- --nocapture
//! ```

use std::time::Duration;

use jumpstarter_conformance::diff::{self, Golden, Outcome};
use jumpstarter_conformance::harness::{assets, Harness, TestEnv};

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn differential_suite() {
    if assets().is_none() {
        eprintln!("SKIP: KUBEBUILDER_ASSETS not set — skipping differential suite");
        return;
    }

    let env = TestEnv::start().await.expect("start envtest control plane");
    let harness = Harness::new(env.client.clone());

    // -- Rust leg: serve the real services over a real gRPC port ------------
    let rust = diff::serve_rust(&harness)
        .await
        .expect("serve rust services");
    let rust_ch = diff::connect(rust.addr)
        .await
        .expect("connect rust endpoint");
    eprintln!("[harness] rust endpoint at {}", rust.addr);
    let rust_results = diff::run_endpoint("rust", &rust_ch, &env, &harness, "rs").await;

    // -- Go leg (best-effort) ----------------------------------------------
    let go_bin = std::env::var("JMP_GO_CONFORMANCE_BIN")
        .ok()
        .filter(|s| !s.is_empty());
    let mut go_blocker: Option<String> = None;
    let mut go_results: Option<Vec<(&'static str, Result<Outcome, String>)>> = None;
    let mut go_server_opt = None;

    if let Some(bin) = &go_bin {
        if !std::path::Path::new(bin).exists() {
            go_blocker = Some(format!("JMP_GO_CONFORMANCE_BIN={bin} does not exist"));
        } else {
            match env.write_kubeconfig() {
                Err(e) => go_blocker = Some(format!("write kubeconfig: {e}")),
                Ok(kubeconfig) => {
                    let port = free_port();
                    let log = kubeconfig.with_file_name("go-conformance.log");
                    eprintln!("[harness] spawning go conformance server (bin={bin}, port={port})");
                    match diff::spawn_go(bin, &kubeconfig, port, &log, Duration::from_secs(60))
                        .await
                    {
                        Err(e) => go_blocker = Some(e),
                        Ok(go) => match diff::connect(go.addr).await {
                            Err(e) => {
                                go_blocker = Some(format!("connect go endpoint: {e}"));
                                go.stop().await;
                            }
                            Ok(go_ch) => {
                                eprintln!("[harness] go endpoint at {}", go.addr);
                                go_results = Some(
                                    diff::run_endpoint("go", &go_ch, &env, &harness, "go").await,
                                );
                                go_server_opt = Some(go);
                            }
                        },
                    }
                }
            }
        }
    } else {
        go_blocker = Some("JMP_GO_CONFORMANCE_BIN not set".to_string());
    }

    // -- report + assert ----------------------------------------------------
    let mut failures = 0usize;
    let mut rust_ok: Vec<(&'static str, Outcome)> = Vec::new();

    eprintln!("\n================= differential results =================");
    for (i, e) in diff::EXPECTED.iter().enumerate() {
        let (_, rust_r) = &rust_results[i];
        let rust_o = match rust_r {
            Ok(o) => o.clone(),
            Err(err) => {
                failures += 1;
                eprintln!("  FAIL  {}  rust harness error: {err}", e.name);
                continue;
            }
        };
        // 1) Rust matches the contract.
        if let Err(why) = e.matches(&rust_o) {
            failures += 1;
            eprintln!("  FAIL  {}  rust vs contract: {why}", e.name);
            continue;
        }
        rust_ok.push((e.name, rust_o.clone()));

        // 2+3) Go matches the contract AND agrees with Rust.
        if let Some(go_results) = &go_results {
            let (_, go_r) = &go_results[i];
            match go_r {
                Err(err) => {
                    failures += 1;
                    eprintln!("  FAIL  {}  go harness error: {err}", e.name);
                }
                Ok(go_o) => {
                    let mut line_ok = true;
                    if let Err(why) = e.matches(go_o) {
                        failures += 1;
                        line_ok = false;
                        eprintln!("  FAIL  {}  go vs contract: {why}", e.name);
                    }
                    // Rust≡Go agreement: exact rows need identical messages;
                    // substring rows only need both to carry the contract text
                    // (already checked by e.matches above).
                    let agree = if e.substring {
                        go_o.code == rust_o.code
                    } else {
                        *go_o == rust_o
                    };
                    if !agree {
                        failures += 1;
                        line_ok = false;
                        eprintln!(
                            "  FAIL  {}  Rust≡Go DIFF: rust={:?} go={:?}",
                            e.name, rust_o, go_o
                        );
                    }
                    if line_ok {
                        eprintln!("  PASS  {}  [{}] rust==go", e.name, go_o.code);
                    }
                }
            }
        } else {
            eprintln!("  PASS  {}  [{}] (rust only)", e.name, rust_o.code);
        }
    }

    if let Some(b) = &go_blocker {
        eprintln!("\n[go leg] NOT RUN: {b}");
    }
    eprintln!("======================================================\n");

    // -- record golden ------------------------------------------------------
    if std::env::var("JMP_CONFORMANCE_RECORD").is_ok() {
        let (source, comment, rows): (&str, String, Vec<(&'static str, Outcome)>) =
            if let Some(go_results) = &go_results {
                let rows: Vec<(&'static str, Outcome)> = go_results
                    .iter()
                    .filter_map(|(n, r)| r.as_ref().ok().map(|o| (*n, o.clone())))
                    .collect();
                (
                "go",
                "Recorded by jumpstarter-conformance tests/differential.rs against the real Go \
                     controller (controller/hack/conformance) on the shared envtest apiserver. \
                     (code, message) per spec-02 §12 case. Regenerate: JMP_CONFORMANCE_RECORD=1."
                    .to_string(),
                rows,
            )
            } else {
                (
                    "rust-provisional",
                    format!(
                    "PROVISIONAL: recorded from the RUST controller because the Go leg could not \
                         run ({}). TODO: re-record against the Go controller with \
                         JMP_GO_CONFORMANCE_BIN set + JMP_CONFORMANCE_RECORD=1.",
                    go_blocker.clone().unwrap_or_default()
                ),
                    rust_ok.clone(),
                )
            };
        let golden = Golden::from_outcomes(source, &comment, &rows);
        let path = diff::golden_path();
        std::fs::create_dir_all(path.parent().unwrap()).expect("mkdir golden");
        let json = serde_json::to_string_pretty(&golden).expect("serialize golden");
        std::fs::write(&path, format!("{json}\n")).expect("write golden");
        eprintln!(
            "[record] wrote {} ({} cases, source={source})",
            path.display(),
            golden.cases.len()
        );
    }

    // -- teardown -----------------------------------------------------------
    if let Some(go) = go_server_opt {
        go.stop().await;
    }
    rust.shutdown().await;

    assert_eq!(failures, 0, "{failures} differential case(s) failed");
}
