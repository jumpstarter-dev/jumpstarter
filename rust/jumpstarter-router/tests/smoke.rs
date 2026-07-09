//! Flags-to-main smoke tests for the `/router` binary: contrived
//! environments (no cluster), asserting the Go-parity failure paths without
//! any network access. Note the router has no namespace-resolution fallback
//! and no probes: the first cluster-dependent step is the kube client.

use std::process::{Command, Output};

/// Runs the router binary with a sanitized environment: no `NAMESPACE`, no
/// in-cluster env, and a kubeconfig path that cannot exist.
fn run_router(args: &[&str], envs: &[(&str, &str)]) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_jumpstarter-router"));
    cmd.args(args)
        .env_remove("NAMESPACE")
        .env_remove("KUBERNETES_SERVICE_HOST")
        .env_remove("KUBERNETES_SERVICE_PORT")
        .env_remove("RUST_LOG")
        .env("KUBECONFIG", "/nonexistent/jumpstarter-smoke/kubeconfig");
    for (key, value) in envs {
        cmd.env(key, value);
    }
    cmd.output().expect("spawn router binary")
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

#[test]
fn unknown_flag_prints_usage_and_exits_2() {
    let output = run_router(&["-bogus"], &[]);
    assert_eq!(output.status.code(), Some(2), "{output:?}");
    let stderr = stderr(&output);
    assert!(
        stderr.contains("flag provided but not defined: -bogus"),
        "{stderr}"
    );
    assert!(stderr.contains("Usage of /router:"), "{stderr}");
}

#[test]
fn manager_flags_are_not_defined_on_the_router() {
    // cmd/router/main.go binds ONLY the zap flags; the manager flag set is
    // "flag provided but not defined" on the router (Go flag exit code 2).
    for flag in ["-leader-elect", "--metrics-bind-address=:8080"] {
        let output = run_router(&[flag], &[]);
        assert_eq!(output.status.code(), Some(2), "flag {flag}: {output:?}");
        let stderr = stderr(&output);
        let name = flag.trim_start_matches('-').split('=').next().unwrap();
        assert!(
            stderr.contains(&format!("flag provided but not defined: -{name}")),
            "flag {flag}: {stderr}"
        );
        assert!(stderr.contains("Usage of /router:"), "{stderr}");
    }
}

#[test]
fn help_prints_usage_and_exits_0() {
    let output = run_router(&["-h"], &[]);
    assert_eq!(output.status.code(), Some(0), "{output:?}");
    assert!(stderr(&output).contains("Usage of /router:"));
}

#[test]
fn unresolvable_kubeconfig_fails_with_go_message() {
    // The Go router's first fatal is `kclient.New` — "failed to create k8s
    // client" — regardless of NAMESPACE (which it reads later, raw).
    let output = run_router(&[], &[]);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    let stderr = stderr(&output);
    assert!(
        stderr.contains("failed to create k8s client"),
        "expected the kube-client fatal, got: {stderr}"
    );
    // The router must not start probes or metrics (it has none).
    assert!(stderr.contains("Jumpstarter Router starting"), "{stderr}");
    assert!(!stderr.contains("health probe server"), "{stderr}");
}

#[test]
fn zap_flags_are_accepted() {
    // The Go router binds exactly the zap flag surface; a zap flag must not
    // be rejected at parse time (the process then fails at the kube client,
    // proving parsing succeeded).
    let output = run_router(&["--zap-log-level=debug", "-zap-devel"], &[]);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    assert!(stderr(&output).contains("failed to create k8s client"));
}
