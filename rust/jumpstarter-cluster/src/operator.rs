//! Operator-based Jumpstarter installation (`cluster/operator.py`): cert-manager,
//! the operator installer, the Jumpstarter CR, and readiness waits — all via
//! `kubectl`.

use std::time::Duration;

use crate::command::{run_command, run_command_stdin, run_command_streamed};
use crate::common::{GRPC_NODEPORT, LOGIN_NODEPORT, ROUTER_NODEPORT};
use crate::error::{ClusterError, Result};
use crate::progress::Progress;

const CERTMANAGER_VERSION: &str = "v1.19.2";
const OPERATOR_NAMESPACE: &str = "jumpstarter-operator-system";
const OPERATOR_DEPLOYMENT: &str = "jumpstarter-operator-controller-manager";

fn operator_installer_url(version: &str) -> String {
    format!("https://github.com/jumpstarter-dev/jumpstarter/releases/download/{version}/operator-installer.yaml")
}

/// `kubectl [--kubeconfig kc] [--context ctx]` base command.
fn kubectl_base(kubeconfig: Option<&str>, context: Option<&str>) -> Vec<String> {
    let mut cmd = vec!["kubectl".to_string()];
    if let Some(kc) = kubeconfig {
        cmd.push("--kubeconfig".to_string());
        cmd.push(kc.to_string());
    }
    if let Some(ctx) = context {
        cmd.push("--context".to_string());
        cmd.push(ctx.to_string());
    }
    cmd
}

fn with(base: &[String], extra: &[&str]) -> Vec<String> {
    let mut cmd = base.to_vec();
    cmd.extend(extra.iter().map(|s| s.to_string()));
    cmd
}

async fn install_cert_manager(kubeconfig: Option<&str>, context: Option<&str>, progress: &dyn Progress) -> Result<()> {
    let base = kubectl_base(kubeconfig, context);
    if run_command(&with(&base, &["get", "crd", "certificates.cert-manager.io"])).await?.ok() {
        progress.progress("cert-manager already installed, skipping");
        return Ok(());
    }
    progress.progress(&format!("Installing cert-manager {CERTMANAGER_VERSION}..."));
    let url = format!(
        "https://github.com/cert-manager/cert-manager/releases/download/{CERTMANAGER_VERSION}/cert-manager.yaml"
    );
    if run_command_streamed(&with(&base, &["apply", "-f", &url])).await? != 0 {
        return Err(ClusterError::Operation("Failed to install cert-manager".to_string()));
    }
    progress.progress("Waiting for cert-manager to be ready...");
    let code = run_command_streamed(&with(
        &base,
        &[
            "wait",
            "--namespace",
            "cert-manager",
            "--for=condition=available",
            "deployment/cert-manager-webhook",
            "--timeout=120s",
        ],
    ))
    .await?;
    if code != 0 {
        return Err(ClusterError::Operation("cert-manager did not become ready".to_string()));
    }
    progress.success("cert-manager installed");
    Ok(())
}

async fn install_operator(
    version: &str,
    kubeconfig: Option<&str>,
    context: Option<&str>,
    operator_installer: Option<&str>,
    progress: &dyn Progress,
) -> Result<()> {
    let base = kubectl_base(kubeconfig, context);
    let installer = operator_installer.map(String::from).unwrap_or_else(|| operator_installer_url(version));
    progress.progress(&format!("Installing Jumpstarter operator {version}..."));
    progress.progress(&format!("Installer: {installer}"));
    if run_command_streamed(&with(&base, &["apply", "-f", &installer])).await? != 0 {
        return Err(ClusterError::Operation(format!("Failed to apply operator installer from {installer}")));
    }
    // Restart the operator if it already existed, so it picks up the new image.
    if run_command(&with(&base, &["get", "deployment", OPERATOR_DEPLOYMENT, "-n", OPERATOR_NAMESPACE])).await?.ok() {
        progress.progress("Restarting operator to pick up new image...");
        let _ = run_command_streamed(&with(
            &base,
            &["rollout", "restart", &format!("deployment/{OPERATOR_DEPLOYMENT}"), "-n", OPERATOR_NAMESPACE],
        ))
        .await?;
    }
    progress.progress("Waiting for operator to be ready...");
    let code = run_command_streamed(&with(
        &base,
        &[
            "wait",
            "--namespace",
            OPERATOR_NAMESPACE,
            "--for=condition=available",
            &format!("deployment/{OPERATOR_DEPLOYMENT}"),
            "--timeout=120s",
        ],
    ))
    .await?;
    if code != 0 {
        return Err(ClusterError::Operation("Operator did not become ready".to_string()));
    }
    progress.success("Operator is ready");
    Ok(())
}

/// Build the Jumpstarter CR YAML (`_build_jumpstarter_cr`, nodeport mode).
pub fn build_jumpstarter_cr(namespace: &str, basedomain: &str, grpc_endpoint: &str, router_endpoint: &str) -> String {
    let controller_endpoint = format!(
        "        - address: \"{grpc_endpoint}\"\n          nodeport:\n            enabled: true\n            port: {GRPC_NODEPORT}"
    );
    let router_endpoint_config = format!(
        "        - address: \"{router_endpoint}\"\n          nodeport:\n            enabled: true\n            port: {ROUTER_NODEPORT}"
    );
    let login_endpoint = format!(
        "    login:\n      endpoints:\n        - address: \"login.{basedomain}:{LOGIN_NODEPORT}\"\n          nodeport:\n            enabled: true\n            port: {LOGIN_NODEPORT}"
    );
    format!(
        "apiVersion: operator.jumpstarter.dev/v1alpha1\n\
kind: Jumpstarter\n\
metadata:\n  name: jumpstarter\n  namespace: {namespace}\n\
spec:\n  baseDomain: {basedomain}\n\
  certManager:\n    enabled: true\n    server:\n      selfSigned:\n        enabled: true\n\
  authentication:\n    internal:\n      prefix: \"internal:\"\n      enabled: true\n    autoProvisioning:\n      enabled: true\n\
  controller:\n    replicas: 1\n    grpc:\n      endpoints:\n{controller_endpoint}\n{login_endpoint}\n\
  routers:\n    replicas: 1\n    resources:\n      requests:\n        cpu: 100m\n        memory: 100Mi\n    grpc:\n      endpoints:\n{router_endpoint_config}\n"
    )
}

async fn apply_jumpstarter_cr(
    namespace: &str,
    basedomain: &str,
    grpc_endpoint: &str,
    router_endpoint: &str,
    kubeconfig: Option<&str>,
    context: Option<&str>,
    progress: &dyn Progress,
) -> Result<()> {
    let base = kubectl_base(kubeconfig, context);
    // Create the namespace (idempotent, via apply of the dry-run manifest).
    let ns = run_command(&with(&base, &["create", "namespace", namespace, "--dry-run=client", "-o", "yaml"])).await?;
    if ns.ok() {
        let out = run_command_stdin(&with(&base, &["apply", "-f", "-"]), ns.stdout.as_bytes()).await?;
        if !out.ok() {
            return Err(ClusterError::Operation(format!("Failed to create namespace {namespace}: {}", out.stderr)));
        }
    }
    let cr = build_jumpstarter_cr(namespace, basedomain, grpc_endpoint, router_endpoint);
    progress.progress("Applying Jumpstarter CR...");
    let out = run_command_stdin(&with(&base, &["apply", "-f", "-"]), cr.as_bytes()).await?;
    if !out.ok() {
        return Err(ClusterError::Operation(format!("Failed to apply Jumpstarter CR: {}", out.stderr)));
    }
    progress.success("Jumpstarter CR applied");
    Ok(())
}

async fn wait_for_jumpstarter_ready(
    namespace: &str,
    kubeconfig: Option<&str>,
    context: Option<&str>,
    progress: &dyn Progress,
    timeout: u64,
) -> Result<()> {
    let base = kubectl_base(kubeconfig, context);
    progress.progress("Waiting for Jumpstarter deployments to be ready...");
    let poll_interval = 5u64;
    let max_polls = timeout / poll_interval;
    for deployment in ["jumpstarter-controller", "jumpstarter-router-0"] {
        // Wait for the deployment to be created by the operator.
        let mut created = false;
        for _ in 0..max_polls {
            if run_command(&with(&base, &["get", "deployment", deployment, "-n", namespace])).await?.ok() {
                created = true;
                break;
            }
            tokio::time::sleep(Duration::from_secs(poll_interval)).await;
        }
        if !created {
            return Err(ClusterError::Operation(format!("Timeout waiting for deployment/{deployment} to be created")));
        }
        let code = run_command_streamed(&with(
            &base,
            &[
                "wait",
                "--namespace",
                namespace,
                "--for=condition=available",
                &format!("deployment/{deployment}"),
                &format!("--timeout={timeout}s"),
            ],
        ))
        .await?;
        if code != 0 {
            return Err(ClusterError::Operation(format!("deployment/{deployment} did not become ready")));
        }
    }
    progress.success("Jumpstarter is ready");
    Ok(())
}

/// Install Jumpstarter via the operator (`install_jumpstarter_operator`):
/// cert-manager → operator → CR → wait.
#[allow(clippy::too_many_arguments)]
pub async fn install_jumpstarter_operator(
    version: &str,
    namespace: &str,
    basedomain: &str,
    grpc_endpoint: &str,
    router_endpoint: &str,
    kubeconfig: Option<&str>,
    context: Option<&str>,
    operator_installer: Option<&str>,
    progress: &dyn Progress,
) -> Result<()> {
    install_cert_manager(kubeconfig, context, progress).await?;
    install_operator(version, kubeconfig, context, operator_installer, progress).await?;
    apply_jumpstarter_cr(namespace, basedomain, grpc_endpoint, router_endpoint, kubeconfig, context, progress).await?;
    wait_for_jumpstarter_ready(namespace, kubeconfig, context, progress, 300).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cr_yaml_has_endpoints_and_basedomain() {
        let cr = build_jumpstarter_cr(
            "jumpstarter-lab",
            "jumpstarter.1.2.3.4.nip.io",
            "grpc.jumpstarter.1.2.3.4.nip.io:8082",
            "router.jumpstarter.1.2.3.4.nip.io:8083",
        );
        assert!(cr.contains("kind: Jumpstarter"));
        assert!(cr.contains("baseDomain: jumpstarter.1.2.3.4.nip.io"));
        assert!(cr.contains("namespace: jumpstarter-lab"));
        assert!(cr.contains("grpc.jumpstarter.1.2.3.4.nip.io:8082"));
        assert!(cr.contains("port: 30010"));
        assert!(cr.contains("login.jumpstarter.1.2.3.4.nip.io:30014"));
    }
}
