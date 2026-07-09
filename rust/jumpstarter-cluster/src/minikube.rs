//! `minikube` cluster management (`cluster/minikube.py`).

use crate::command::{run_command, run_command_streamed};
use crate::common::{get_extra_certs_path, tool_installed};
use crate::error::{ClusterError, Result};
use crate::progress::Progress;

pub fn minikube_installed(minikube: &str) -> bool {
    tool_installed(minikube)
}

/// Whether a minikube profile exists (running OR stopped). Primary: `profile
/// list -o json` → `valid[].Name`; fallback: `status -p` with the
/// `"profile"+"not found"` heuristic (`minikube_cluster_exists`).
pub async fn minikube_cluster_exists(minikube: &str, cluster_name: &str) -> bool {
    if !minikube_installed(minikube) {
        return false;
    }
    if let Ok(out) = run_command(&[minikube, "profile", "list", "-o", "json"]).await {
        if out.ok() {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&out.stdout) {
                let found = v
                    .get("valid")
                    .and_then(|x| x.as_array())
                    .map(|a| {
                        a.iter()
                            .any(|p| p.get("Name").and_then(|n| n.as_str()) == Some(cluster_name))
                    })
                    .unwrap_or(false);
                if found {
                    return true;
                }
            }
        }
    }
    // Fallback: status output.
    match run_command(&[minikube, "status", "-p", cluster_name]).await {
        Ok(out) if out.ok() => true,
        Ok(out) => {
            let combined = format!("{}{}", out.stdout, out.stderr).to_lowercase();
            !(combined.contains("profile") && combined.contains("not found"))
        }
        Err(_) => true,
    }
}

pub async fn delete_minikube_cluster(
    minikube: &str,
    cluster_name: &str,
    progress: &dyn Progress,
) -> Result<()> {
    if !minikube_installed(minikube) {
        return Err(ClusterError::tool_not_installed("minikube"));
    }
    if !minikube_cluster_exists(minikube, cluster_name).await {
        return Ok(());
    }
    progress.progress(&format!("Deleting Minikube cluster \"{cluster_name}\"..."));
    if run_command_streamed(&[minikube, "delete", "-p", cluster_name]).await? == 0 {
        progress.success(&format!(
            "Successfully deleted Minikube cluster \"{cluster_name}\""
        ));
        Ok(())
    } else {
        Err(ClusterError::Operation(format!(
            "Failed to delete Minikube cluster '{cluster_name}'"
        )))
    }
}

pub async fn create_minikube_cluster(
    minikube: &str,
    cluster_name: &str,
    extra_args: &mut Vec<String>,
    force_recreate: bool,
    progress: &dyn Progress,
) -> Result<()> {
    if !minikube_installed(minikube) {
        return Err(ClusterError::tool_not_installed("minikube"));
    }
    if minikube_cluster_exists(minikube, cluster_name).await {
        if !force_recreate {
            progress.progress(&format!(
                "Minikube cluster \"{cluster_name}\" already exists, continuing..."
            ));
            return Ok(());
        }
        delete_minikube_cluster(minikube, cluster_name, progress).await?;
    }

    // Default to --cpus=4 unless the caller set it or `minikube config get cpus`
    // is a positive int (`minikube.py:127-136`).
    let has_cpus = extra_args
        .iter()
        .any(|a| a == "--cpus" || a.starts_with("--cpus="));
    if !has_cpus {
        let config_cpus = run_command(&[minikube, "config", "get", "cpus"])
            .await
            .ok()
            .filter(|o| o.ok())
            .and_then(|o| o.stdout.trim().parse::<i64>().ok())
            .map(|n| n > 0)
            .unwrap_or(false);
        if !config_cpus {
            extra_args.push("--cpus=4".to_string());
        }
    }

    let mut cmd = vec![
        minikube.to_string(),
        "start".to_string(),
        "--profile".to_string(),
        cluster_name.to_string(),
        "--extra-config=apiserver.service-node-port-range=30000-32767".to_string(),
    ];
    cmd.extend(extra_args.iter().cloned());
    if run_command_streamed(&cmd).await? == 0 {
        let past = if force_recreate {
            "recreated"
        } else {
            "created"
        };
        progress.success(&format!(
            "Successfully {past} Minikube cluster \"{cluster_name}\""
        ));
        Ok(())
    } else {
        let action = if force_recreate { "recreate" } else { "create" };
        Err(ClusterError::Operation(format!(
            "Failed to {action} Minikube cluster '{cluster_name}'"
        )))
    }
}

pub async fn list_minikube_clusters(minikube: &str) -> Vec<String> {
    if !minikube_installed(minikube) {
        return Vec::new();
    }
    match run_command(&[minikube, "profile", "list", "-o", "json"]).await {
        Ok(out) if out.ok() => serde_json::from_str::<serde_json::Value>(&out.stdout)
            .ok()
            .and_then(|v| v.get("valid").and_then(|x| x.as_array()).cloned())
            .map(|a| {
                a.iter()
                    .filter_map(|p| p.get("Name").and_then(|n| n.as_str()).map(String::from))
                    .collect()
            })
            .unwrap_or_default(),
        _ => Vec::new(),
    }
}

/// Copy/append the custom CA into `~/.minikube/certs/ca.crt` (`prepare_certificates`).
pub async fn prepare_certificates(extra_certs: &str, progress: &dyn Progress) -> Result<()> {
    let path = get_extra_certs_path(extra_certs)
        .ok_or_else(|| ClusterError::Certificate("Extra certificates path is empty".to_string()))?;
    if !std::path::Path::new(&path).exists() {
        return Err(ClusterError::Certificate(format!(
            "Extra certificates file not found: {path}"
        )));
    }
    let certs_dir = home::home_dir()
        .ok_or_else(|| ClusterError::Certificate("Cannot resolve home dir".to_string()))?
        .join(".minikube")
        .join("certs");
    std::fs::create_dir_all(&certs_dir).map_err(|e| ClusterError::Certificate(e.to_string()))?;
    let dest = certs_dir.join("ca.crt");
    let src =
        std::fs::read_to_string(&path).map_err(|e| ClusterError::Certificate(e.to_string()))?;
    if dest.exists() {
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new()
            .append(true)
            .open(&dest)
            .map_err(|e| ClusterError::Certificate(e.to_string()))?;
        write!(f, "\n{src}").map_err(|e| ClusterError::Certificate(e.to_string()))?;
    } else {
        std::fs::write(&dest, src).map_err(|e| ClusterError::Certificate(e.to_string()))?;
    }
    progress.success(&format!(
        "Prepared custom certificates for Minikube: {}",
        dest.display()
    ));
    Ok(())
}

pub async fn create_minikube_cluster_with_options(
    minikube: &str,
    cluster_name: &str,
    minikube_extra_args: &str,
    force_recreate: bool,
    extra_certs: Option<&str>,
    progress: &dyn Progress,
) -> Result<()> {
    if !minikube_installed(minikube) {
        return Err(ClusterError::tool_not_installed("minikube"));
    }
    let action = if force_recreate {
        "Recreating"
    } else {
        "Creating"
    };
    progress.progress(&format!("{action} Minikube cluster \"{cluster_name}\"..."));
    let mut extra: Vec<String> = if minikube_extra_args.trim().is_empty() {
        Vec::new()
    } else {
        shlex::split(minikube_extra_args).unwrap_or_default()
    };
    if let Some(certs) = extra_certs {
        prepare_certificates(certs, progress).await?;
        if !extra.iter().any(|a| a == "--embed-certs") {
            extra.push("--embed-certs".to_string());
        }
    }
    create_minikube_cluster(minikube, cluster_name, &mut extra, force_recreate, progress)
        .await
        .map_err(|e| {
            let action = if force_recreate { "recreate" } else { "create" };
            ClusterError::Operation(format!(
                "Failed to {action} minikube cluster \"{cluster_name}\": {e}"
            ))
        })
}

pub async fn delete_minikube_cluster_with_feedback(
    minikube: &str,
    cluster_name: &str,
    progress: &dyn Progress,
) -> Result<()> {
    if !minikube_installed(minikube) {
        return Err(ClusterError::tool_not_installed("minikube"));
    }
    delete_minikube_cluster(minikube, cluster_name, progress)
        .await
        .map_err(|e| {
            ClusterError::Operation(format!(
                "Failed to delete minikube cluster \"{cluster_name}\": {e}"
            ))
        })
}
