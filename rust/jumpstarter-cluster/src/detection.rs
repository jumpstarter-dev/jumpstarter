//! Cluster-type detection (`cluster/detection.py`). For the get/list path this is
//! `detect_cluster_type` (kubeconfig heuristics); the kind/minikube
//! existence/auto-detect helpers live in their own modules.

use crate::command::run_command;
use crate::common::tool_installed;
use crate::error::{ClusterError, Result};
use crate::kind::{kind_cluster_exists, kind_installed};
use crate::minikube::{minikube_cluster_exists, minikube_installed};

/// The container runtime backing kind (`detect_container_runtime`).
pub fn detect_container_runtime() -> Result<String> {
    for rt in ["docker", "podman", "nerdctl"] {
        if tool_installed(rt) {
            return Ok(rt.to_string());
        }
    }
    Err(ClusterError::ToolNotInstalled {
        tool: "container runtime".to_string(),
        info: ": No supported container runtime found in PATH. Kind requires docker, podman, or nerdctl."
            .to_string(),
    })
}

/// The (runtime, control-plane node) for a kind cluster (`detect_kind_provider`).
pub async fn detect_kind_provider(cluster_name: &str) -> Result<(String, String)> {
    let runtime = detect_container_runtime()?;
    let mut names = vec![
        format!("{cluster_name}-control-plane"),
        format!("kind-{cluster_name}-control-plane"),
        format!("{cluster_name}-worker"),
        format!("kind-{cluster_name}-worker"),
    ];
    if cluster_name == "kind" {
        names.insert(0, "kind-control-plane".to_string());
    }
    for node in &names {
        if let Ok(out) = run_command(&[runtime.as_str(), "inspect", node.as_str()]).await {
            if out.ok() {
                return Ok((runtime, node.clone()));
            }
        }
    }
    Ok((runtime, format!("{cluster_name}-control-plane")))
}

/// Which local cluster type (kind/minikube) exists by this name
/// (`detect_existing_cluster_type`); errors if both exist.
pub async fn detect_existing_cluster_type(cluster_name: &str) -> Result<Option<String>> {
    let kind_exists = kind_installed("kind") && kind_cluster_exists("kind", cluster_name).await;
    let minikube_exists =
        minikube_installed("minikube") && minikube_cluster_exists("minikube", cluster_name).await;
    match (kind_exists, minikube_exists) {
        (true, true) => Err(ClusterError::Operation(format!(
            "Both Kind and Minikube clusters named \"{cluster_name}\" exist. \
             Please specify --kind or --minikube to choose which one to delete."
        ))),
        (true, false) => Ok(Some("kind".to_string())),
        (false, true) => Ok(Some("minikube".to_string())),
        (false, false) => Ok(None),
    }
}

/// Auto-pick an installed local cluster type, preferring kind (`auto_detect_cluster_type`).
pub fn auto_detect_cluster_type() -> Result<String> {
    if kind_installed("kind") {
        Ok("kind".to_string())
    } else if minikube_installed("minikube") {
        Ok("minikube".to_string())
    } else {
        Err(ClusterError::ToolNotInstalled {
            tool: "kind, minikube, or k3s".to_string(),
            info: ": Neither Kind nor Minikube is installed. Install one (kind / minikube), \
                   or use --k3s <user@host> for a remote host."
                .to_string(),
        })
    }
}

fn looks_like_minikube_ip(server_url: &str) -> bool {
    // 192.168.x.x:(8443|443) or 172.x.x.x:(8443|443) (detection.py:131-133).
    use regex::Regex;
    let p1 = Regex::new(r"192\.168\.\d+\.\d+:(8443|443)").unwrap();
    let p2 = Regex::new(r"172\.\d+\.\d+\.\d+:(8443|443)").unwrap();
    p1.is_match(server_url) || p2.is_match(server_url)
}

/// Classify a cluster as `kind`/`minikube`/`remote` from its context name + API
/// server URL (`detect_cluster_type`).
pub async fn detect_cluster_type(context_name: &str, server_url: &str, minikube: &str) -> String {
    if context_name.contains("kind-") || context_name.starts_with("kind") {
        return "kind".to_string();
    }
    if context_name.to_lowercase().contains("minikube") {
        return "minikube".to_string();
    }
    let lower = server_url.to_lowercase();
    if ["localhost", "127.0.0.1", "0.0.0.0"]
        .iter()
        .any(|h| lower.contains(h))
    {
        return "kind".to_string();
    }
    if looks_like_minikube_ip(server_url) {
        // Verify by checking for a real minikube profile.
        if let Ok(out) = run_command(&[minikube, "profile", "list", "-o", "json"]).await {
            if out.ok() {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&out.stdout) {
                    let has_valid = v
                        .get("valid")
                        .and_then(|x| x.as_array())
                        .map(|a| !a.is_empty())
                        .unwrap_or(false);
                    if has_valid {
                        return "minikube".to_string();
                    }
                }
            }
        }
    }
    "remote".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn detects_kind_and_minikube_and_remote_by_name_and_url() {
        assert_eq!(
            detect_cluster_type("kind-dev", "https://x", "minikube").await,
            "kind"
        );
        assert_eq!(
            detect_cluster_type("kindcluster", "https://x", "minikube").await,
            "kind"
        );
        assert_eq!(
            detect_cluster_type("my-minikube", "https://x", "minikube").await,
            "minikube"
        );
        assert_eq!(
            detect_cluster_type("ctx", "https://127.0.0.1:6443", "minikube").await,
            "kind"
        );
        assert_eq!(
            detect_cluster_type("ctx", "https://example.com:6443", "minikube").await,
            "remote"
        );
    }

    #[test]
    fn minikube_ip_regex() {
        assert!(looks_like_minikube_ip("https://192.168.49.2:8443"));
        assert!(looks_like_minikube_ip("https://172.17.0.2:443"));
        assert!(!looks_like_minikube_ip("https://example.com:6443"));
    }
}
