//! `kubectl`-based cluster discovery — the read-only `get cluster` / `get clusters`
//! path (`cluster/kubectl.py`): enumerate kubeconfig contexts, probe each
//! cluster's reachability + version, and detect a Jumpstarter installation.

use crate::command::run_command;
use crate::detection::detect_cluster_type;
use crate::error::{ClusterError, Result};
use crate::info::{ClusterInfo, ClusterList, JumpstarterInstance};

/// A kubeconfig context (`KubectlContext`).
#[derive(Debug, Clone)]
pub struct KubectlContext {
    pub name: String,
    pub cluster: String,
    pub server: String,
    pub user: String,
    pub namespace: String,
    pub current: bool,
}

/// Parse `kubectl config view -o json` into the context list (`get_kubectl_contexts`).
pub async fn get_kubectl_contexts(kubectl: &str) -> Result<Vec<KubectlContext>> {
    let out = run_command(&[kubectl, "config", "view", "-o", "json"]).await?;
    if !out.ok() {
        return Err(ClusterError::Kubeconfig(format!("Failed to get kubectl config: {}", out.stderr)));
    }
    let config: serde_json::Value = serde_json::from_str(&out.stdout)
        .map_err(|e| ClusterError::Kubeconfig(format!("Failed to parse kubectl config: {e}")))?;

    let current = config.get("current-context").and_then(|v| v.as_str()).unwrap_or("");
    let empty = vec![];
    let clusters = config.get("clusters").and_then(|v| v.as_array()).unwrap_or(&empty);

    let mut contexts = Vec::new();
    for ctx in config.get("contexts").and_then(|v| v.as_array()).unwrap_or(&empty) {
        let name = ctx.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let inner = ctx.get("context");
        let cluster = inner.and_then(|c| c.get("cluster")).and_then(|v| v.as_str()).unwrap_or("").to_string();
        let user = inner.and_then(|c| c.get("user")).and_then(|v| v.as_str()).unwrap_or("").to_string();
        let namespace = inner
            .and_then(|c| c.get("namespace"))
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("default")
            .to_string();
        let server = clusters
            .iter()
            .find(|c| c.get("name").and_then(|v| v.as_str()) == Some(cluster.as_str()))
            .and_then(|c| c.get("cluster"))
            .and_then(|c| c.get("server"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let current = name == current;
        contexts.push(KubectlContext { name, cluster, server, user, namespace, current });
    }
    Ok(contexts)
}

/// Strip any non-JSON prefix before the first `{` (`_parse_json_with_prefix`).
fn parse_json_with_prefix(stdout: &str) -> serde_json::Result<serde_json::Value> {
    match stdout.find('{') {
        Some(i) => serde_json::from_str(&stdout[i..]),
        None => serde_json::from_str(stdout),
    }
}

/// Query Jumpstarter CR instances to confirm a full install
/// (`_check_cr_instances` + `_apply_cr_result`); fills `installed`/`namespace`/
/// `status` or `error` on `inst`.
async fn apply_cr_instances(inst: &mut JumpstarterInstance, kubectl: &str, context: &str, namespace: Option<&str>) {
    let cr = "jumpstarters.operator.jumpstarter.dev";
    match run_command(&[kubectl, "--context", context, "get", cr, "-A", "-o", "json"]).await {
        Ok(out) if out.ok() => match serde_json::from_str::<serde_json::Value>(&out.stdout) {
            Ok(data) => {
                let items = data.get("items").and_then(|v| v.as_array());
                if let Some(items) = items.filter(|a| !a.is_empty()) {
                    let cr_ns = items[0]
                        .get("metadata")
                        .and_then(|m| m.get("namespace"))
                        .and_then(|v| v.as_str())
                        .or(namespace)
                        .unwrap_or("unknown");
                    inst.installed = true;
                    inst.namespace = Some(cr_ns.to_string());
                    inst.status = Some("installed".to_string());
                }
            }
            Err(e) => inst.error = Some(format!("CR instance check failed: {e}")),
        },
        Ok(out) => {
            let detail = if out.stderr.is_empty() { &out.stdout } else { &out.stderr };
            inst.error = Some(format!("CR instance check failed (exit {}): {}", out.code, detail));
        }
        Err(e) => inst.error = Some(format!("CR instance check failed: {e}")),
    }
}

/// Detect a Jumpstarter installation via CRD scan + CR instance check
/// (`check_jumpstarter_installation`).
pub async fn check_jumpstarter_installation(
    context: &str,
    namespace: Option<&str>,
    kubectl: &str,
) -> JumpstarterInstance {
    let mut inst = JumpstarterInstance::not_installed();
    let out = match run_command(&[kubectl, "--context", context, "get", "crd", "-o", "json"]).await {
        Ok(o) => o,
        Err(e) => {
            inst.error = Some(format!("Command failed: {e}"));
            return inst;
        }
    };
    if !out.ok() {
        let detail = if out.stderr.is_empty() { &out.stdout } else { &out.stderr };
        inst.error = Some(format!("Command failed: {detail}"));
        return inst;
    }
    let crds = match parse_json_with_prefix(&out.stdout) {
        Ok(v) => v,
        Err(e) => {
            inst.error = Some(format!("Failed to parse output: {e}"));
            return inst;
        }
    };
    let empty = vec![];
    let names: Vec<String> = crds
        .get("items")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty)
        .iter()
        .filter_map(|item| item.get("metadata").and_then(|m| m.get("name")).and_then(|v| v.as_str()))
        .filter(|n| n.contains("jumpstarter.dev"))
        .map(String::from)
        .collect();
    if !names.is_empty() {
        inst.has_crds = true;
        if names.iter().any(|n| n == "jumpstarters.operator.jumpstarter.dev") {
            apply_cr_instances(&mut inst, kubectl, context, namespace).await;
        }
    }
    inst
}

fn unreachable_cluster(name: &str, error: String) -> ClusterInfo {
    ClusterInfo {
        api_version: "jumpstarter.dev/v1alpha1".to_string(),
        kind: "ClusterInfo".to_string(),
        name: name.to_string(),
        cluster: "unknown".to_string(),
        server: "unknown".to_string(),
        user: "unknown".to_string(),
        namespace: "unknown".to_string(),
        is_current: false,
        r#type: "remote".to_string(),
        accessible: false,
        version: None,
        jumpstarter: JumpstarterInstance::not_installed(),
        error: Some(error),
    }
}

/// Comprehensive info for one cluster context (`get_cluster_info`).
pub async fn get_cluster_info(context: &str, kubectl: &str, minikube: &str) -> ClusterInfo {
    let contexts = match get_kubectl_contexts(kubectl).await {
        Ok(c) => c,
        Err(e) => return unreachable_cluster(context, format!("Failed to get cluster info: {e}")),
    };
    let Some(info) = contexts.into_iter().find(|c| c.name == context) else {
        return unreachable_cluster(context, format!("Context '{context}' not found"));
    };

    let cluster_type = detect_cluster_type(&info.name, &info.server, minikube).await;

    let (accessible, version) =
        match run_command(&[kubectl, "--context", context, "version", "-o", "json"]).await {
            Ok(out) if out.ok() => {
                let v = serde_json::from_str::<serde_json::Value>(&out.stdout)
                    .ok()
                    .and_then(|j| j.get("serverVersion").and_then(|s| s.get("gitVersion")).and_then(|g| g.as_str()).map(String::from))
                    .unwrap_or_else(|| "unknown".to_string());
                (true, Some(v))
            }
            _ => (false, None),
        };

    let jumpstarter = if accessible {
        check_jumpstarter_installation(context, None, kubectl).await
    } else {
        let mut inst = JumpstarterInstance::not_installed();
        inst.error = Some("Cluster not accessible".to_string());
        inst
    };

    ClusterInfo {
        api_version: "jumpstarter.dev/v1alpha1".to_string(),
        kind: "ClusterInfo".to_string(),
        name: info.name,
        cluster: info.cluster,
        server: info.server,
        user: info.user,
        namespace: info.namespace,
        is_current: info.current,
        r#type: cluster_type,
        accessible,
        version,
        jumpstarter,
        error: None,
    }
}

/// List every kubeconfig cluster with Jumpstarter status, optionally filtered by
/// type (`list_clusters`).
pub async fn list_clusters(type_filter: &str, kubectl: &str, minikube: &str) -> ClusterList {
    let contexts = match get_kubectl_contexts(kubectl).await {
        Ok(c) => c,
        Err(e) => return ClusterList::new(vec![unreachable_cluster("error", format!("Failed to list clusters: {e}"))]),
    };
    let mut items = Vec::new();
    for ctx in contexts {
        let info = get_cluster_info(&ctx.name, kubectl, minikube).await;
        if type_filter != "all" && info.r#type != type_filter {
            continue;
        }
        items.push(info);
    }
    ClusterList::new(items)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_prefix_is_stripped() {
        let v = parse_json_with_prefix("warn: x\n{\"a\":1}").unwrap();
        assert_eq!(v["a"], serde_json::json!(1));
        assert!(parse_json_with_prefix("{\"b\":2}").is_ok());
    }
}
