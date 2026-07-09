//! The conformance test runner: bring up one envtest control plane, wire the
//! real `ControllerService` + `ClientService` over it, run the entire spec-02
//! case suite, print a per-case pass/fail table, and assert zero failures.
//!
//! Env-gated: no-ops (prints SKIP) unless `KUBEBUILDER_ASSETS` points at the
//! envtest binaries. Run it with:
//!
//! ```sh
//! KUBEBUILDER_ASSETS=.../bin/k8s/1.30.0-darwin-arm64 \
//!   cargo test -p jumpstarter-conformance --test conformance -- --nocapture
//! ```

use jumpstarter_conformance::cases;
use jumpstarter_conformance::harness::{assets, Harness, TestEnv};

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn conformance_suite() {
    if assets().is_none() {
        eprintln!("SKIP: KUBEBUILDER_ASSETS not set — hermetic run, skipping conformance suite");
        return;
    }

    let env = TestEnv::start().await.expect("start envtest control plane");
    let harness = Harness::new(env.client.clone());

    let results = cases::run_all(&env, &harness).await;

    eprintln!("\n================= conformance suite results =================");
    let mut failures = 0;
    for (name, r) in &results {
        match r {
            Ok(()) => eprintln!("  PASS  {name}"),
            Err(e) => {
                failures += 1;
                eprintln!("  FAIL  {name}: {e}");
            }
        }
    }
    let total = results.len();
    eprintln!(
        "============ {}/{} passed ============\n",
        total - failures,
        total
    );

    assert_eq!(failures, 0, "{failures}/{total} conformance case(s) failed");
}
