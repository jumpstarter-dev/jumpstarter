//! Non-env-gated replay of the recorded Go-controller goldens.
//!
//! This runs in plain `cargo test` with **no cluster and no Go toolchain**: it
//! loads `tests/golden/go_controller.json` (the `(code, message)` the real Go
//! controller returned for each spec-02 §12 case, captured by the env-gated
//! `differential` suite) and re-checks every recorded row against the in-code
//! contract table [`diff::EXPECTED`].
//!
//! It is the CI drift alarm: if someone edits an expected error string in
//! [`diff::EXPECTED`] without re-recording the golden against Go, this fails —
//! forcing the recorded Go behavior and the Rust contract to stay in lockstep.
//! Every contract row must have a recorded observation and vice-versa.

use jumpstarter_conformance::diff::{self, Golden};

#[test]
fn golden_matches_contract() {
    let path = diff::golden_path();
    let raw = std::fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "cannot read golden {}: {e}. Record it with the env-gated differential suite \
             (KUBEBUILDER_ASSETS=... JMP_GO_CONFORMANCE_BIN=... JMP_CONFORMANCE_RECORD=1).",
            path.display()
        )
    });
    let golden: Golden = serde_json::from_str(&raw)
        .unwrap_or_else(|e| panic!("parse golden {}: {e}", path.display()));

    eprintln!(
        "golden source={} ({} cases) from {}",
        golden.source,
        golden.cases.len(),
        path.display()
    );
    if golden.source == "rust-provisional" {
        eprintln!(
            "WARNING: golden is PROVISIONAL (recorded from Rust, not Go). \
             Re-record against the Go controller once JMP_GO_CONFORMANCE_BIN is available."
        );
    }

    let mut failures = 0usize;

    // 1) every contract row has a matching recorded observation.
    for e in diff::EXPECTED {
        match golden.lookup(e.name) {
            None => {
                failures += 1;
                eprintln!("  FAIL  {}  missing from golden", e.name);
            }
            Some(got) => match e.matches(&got) {
                Ok(()) => eprintln!("  ok    {}  [{}] {}", e.name, got.code, got.message),
                Err(why) => {
                    failures += 1;
                    eprintln!("  FAIL  {}  golden vs contract: {why}", e.name);
                }
            },
        }
    }

    // 2) no orphan rows in the golden (contract and record stay in sync).
    for c in &golden.cases {
        if !diff::EXPECTED.iter().any(|e| e.name == c.name) {
            failures += 1;
            eprintln!(
                "  FAIL  {}  in golden but not in the contract table",
                c.name
            );
        }
    }

    assert_eq!(
        failures, 0,
        "{failures} golden/contract mismatch(es); re-record the golden or fix EXPECTED"
    );
}
