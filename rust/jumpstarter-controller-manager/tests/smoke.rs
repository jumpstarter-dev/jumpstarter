//! Flags-to-main smoke tests: run the compiled `/manager` binary with
//! contrived environments (no cluster) and assert the Go-parity failure
//! paths — bad flags print usage and exit 2, `-h` exits 0, a missing
//! namespace hits the single-namespace fatal, and an unresolvable kubeconfig
//! fails with the manager-startup error. Everything here must fail fast
//! without any network access.

use std::path::Path;
use std::process::{Command, Output};

/// Runs the manager binary with a sanitized environment: no `NAMESPACE`, no
/// in-cluster env, and a kubeconfig path that cannot exist.
fn run_manager(args: &[&str], envs: &[(&str, &str)]) -> Output {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_jumpstarter-controller-manager"));
    cmd.args(args)
        .env_remove("NAMESPACE")
        .env_remove("KUBERNETES_SERVICE_HOST")
        .env_remove("KUBERNETES_SERVICE_PORT")
        .env_remove("RUST_LOG")
        .env("KUBECONFIG", "/nonexistent/jumpstarter-smoke/kubeconfig");
    for (key, value) in envs {
        cmd.env(key, value);
    }
    cmd.output().expect("spawn manager binary")
}

fn stderr(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

/// True when running inside a pod (the mounted namespace file would change
/// the namespace-resolution scenarios); all tests guard on it.
fn in_pod() -> bool {
    Path::new("/var/run/secrets/kubernetes.io/serviceaccount/namespace").exists()
}

#[test]
fn unknown_flag_prints_go_error_and_usage_and_exits_2() {
    let output = run_manager(&["-bogus"], &[]);
    assert_eq!(output.status.code(), Some(2), "{output:?}");
    let stderr = stderr(&output);
    assert!(
        stderr.contains("flag provided but not defined: -bogus"),
        "{stderr}"
    );
    assert!(stderr.contains("Usage of /manager:"), "{stderr}");
}

#[test]
fn missing_flag_value_exits_2() {
    let output = run_manager(&["--metrics-bind-address"], &[]);
    assert_eq!(output.status.code(), Some(2), "{output:?}");
    let stderr = stderr(&output);
    assert!(
        stderr.contains("flag needs an argument: -metrics-bind-address"),
        "{stderr}"
    );
}

#[test]
fn help_prints_usage_and_exits_0() {
    let output = run_manager(&["-h"], &[]);
    assert_eq!(output.status.code(), Some(0), "{output:?}");
    assert!(stderr(&output).contains("Usage of /manager:"));

    let output = run_manager(&["--help"], &[]);
    assert_eq!(output.status.code(), Some(0), "{output:?}");
}

#[test]
fn missing_namespace_is_the_single_namespace_fatal() {
    if in_pod() {
        return;
    }
    // The operator arg vector, but no NAMESPACE and no mounted namespace
    // file: resolution must fail before anything contacts a cluster.
    let output = run_manager(
        &[
            "--leader-elect",
            "--health-probe-bind-address=:8081",
            "-metrics-bind-address=:8080",
        ],
        &[],
    );
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    let stderr = stderr(&output);
    assert!(
        stderr.contains("single namespace since 0.8.0"),
        "expected the Go single-namespace fatal, got: {stderr}"
    );
    // The failure happens after the startup banner...
    assert!(
        stderr.contains("Jumpstarter Controller starting"),
        "{stderr}"
    );
    // ...but before any server comes up.
    assert!(
        !stderr.contains("health probe server listening"),
        "{stderr}"
    );
}

#[test]
fn unresolvable_kubeconfig_fails_manager_startup() {
    if in_pod() {
        return;
    }
    let output = run_manager(&[], &[("NAMESPACE", "jumpstarter-smoke")]);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    let stderr = stderr(&output);
    assert!(
        stderr.contains("unable to start manager"),
        "expected the kube-client fatal, got: {stderr}"
    );
}

#[test]
fn empty_namespace_env_falls_through_to_fatal() {
    if in_pod() {
        return;
    }
    // Go's os.Getenv conflates unset and empty: NAMESPACE="" must fall
    // through to the file (absent) and then the fatal.
    let output = run_manager(&[], &[("NAMESPACE", "")]);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    assert!(stderr(&output).contains("single namespace since 0.8.0"));
}
