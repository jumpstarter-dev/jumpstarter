//! `kind` cluster management (`cluster/kind.py`).

use crate::command::{run_command, run_command_streamed};
use crate::common::{get_extra_certs_path, tool_installed};
use crate::detection::detect_kind_provider;
use crate::error::{ClusterError, Result};
use crate::progress::Progress;

/// The kind cluster config (verbatim from `kind.py:72-103`): service-node-port
/// range + ingress-ready label + the grpc/router/dex/https port mappings.
const KIND_CONFIG: &str = r#"kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
kubeadmConfigPatches:
- |
  kind: ClusterConfiguration
  apiServer:
    extraArgs:
      "service-node-port-range": "3000-32767"
- |
  kind: InitConfiguration
  nodeRegistration:
    kubeletExtraArgs:
      node-labels: "ingress-ready=true"
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 80 # ingress controller
    hostPort: 5080
    protocol: TCP
  - containerPort: 30010 # grpc nodeport
    hostPort: 8082
    protocol: TCP
  - containerPort: 30011 # grpc router nodeport
    hostPort: 8083
    protocol: TCP
  - containerPort: 32000 # dex nodeport
    hostPort: 5556
    protocol: TCP
  - containerPort: 443
    hostPort: 5443
    protocol: TCP
"#;

pub fn kind_installed(kind: &str) -> bool {
    tool_installed(kind)
}

pub async fn kind_cluster_exists(kind: &str, cluster_name: &str) -> bool {
    if !kind_installed(kind) {
        return false;
    }
    run_command(&[kind, "get", "kubeconfig", "--name", cluster_name])
        .await
        .map(|o| o.ok())
        .unwrap_or(false)
}

pub async fn delete_kind_cluster(kind: &str, cluster_name: &str) -> Result<()> {
    if !kind_installed(kind) {
        return Err(ClusterError::tool_not_installed("kind"));
    }
    if !kind_cluster_exists(kind, cluster_name).await {
        return Ok(()); // already gone
    }
    let code = run_command_streamed(&[kind, "delete", "cluster", "--name", cluster_name]).await?;
    if code == 0 {
        Ok(())
    } else {
        Err(ClusterError::Operation(format!(
            "Failed to delete Kind cluster '{cluster_name}'"
        )))
    }
}

pub async fn create_kind_cluster(
    kind: &str,
    cluster_name: &str,
    extra_args: &[String],
    force_recreate: bool,
) -> Result<()> {
    if !kind_installed(kind) {
        return Err(ClusterError::tool_not_installed("kind"));
    }
    if kind_cluster_exists(kind, cluster_name).await {
        if !force_recreate {
            return Err(ClusterError::AlreadyExists(format!(
                "kind cluster \"{cluster_name}\" already exists"
            )));
        }
        delete_kind_cluster(kind, cluster_name).await?;
    }

    // Write the cluster config to a temp file (auto-removed on drop, matching the
    // Python `finally: os.unlink`).
    let mut tf = tempfile::Builder::new()
        .suffix(".yaml")
        .tempfile()
        .map_err(|e| ClusterError::Operation(format!("Failed to write kind config: {e}")))?;
    use std::io::Write;
    tf.write_all(KIND_CONFIG.as_bytes())
        .map_err(|e| ClusterError::Operation(format!("Failed to write kind config: {e}")))?;
    let config_path = tf.path().to_string_lossy().into_owned();

    let mut cmd = vec![
        kind.to_string(),
        "create".to_string(),
        "cluster".to_string(),
        "--name".to_string(),
        cluster_name.to_string(),
        "--config".to_string(),
        config_path,
    ];
    cmd.extend(extra_args.iter().cloned());
    let code = run_command_streamed(&cmd).await?;
    if code == 0 {
        Ok(())
    } else {
        Err(ClusterError::Operation(format!(
            "Failed to create Kind cluster '{cluster_name}'"
        )))
    }
}

pub async fn list_kind_clusters(kind: &str) -> Vec<String> {
    if !kind_installed(kind) {
        return Vec::new();
    }
    match run_command(&[kind, "get", "clusters"]).await {
        Ok(out) if out.ok() => out
            .stdout
            .lines()
            .map(|l| l.trim().to_string())
            .filter(|l| !l.is_empty())
            .collect(),
        _ => Vec::new(),
    }
}

/// Copy + trust custom CA certs in the kind node (`inject_certificates`).
pub async fn inject_certificates(
    extra_certs: &str,
    cluster_name: &str,
    progress: &dyn Progress,
) -> Result<()> {
    let path = get_extra_certs_path(extra_certs)
        .ok_or_else(|| ClusterError::Certificate("Extra certificates path is empty".to_string()))?;
    if !std::path::Path::new(&path).exists() {
        return Err(ClusterError::Certificate(format!(
            "Extra certificates file not found: {path}"
        )));
    }
    let (runtime, node) = detect_kind_provider(cluster_name).await?;
    progress.progress(&format!(
        "Injecting certificates from {path} into Kind cluster..."
    ));
    let dest = format!("{node}:/usr/local/share/ca-certificates/extra-certs.crt");
    if run_command_streamed(&[runtime.as_str(), "cp", path.as_str(), dest.as_str()]).await? != 0 {
        return Err(ClusterError::Certificate(format!(
            "Failed to copy certificates to Kind node: {node}"
        )));
    }
    if run_command_streamed(&[
        runtime.as_str(),
        "exec",
        node.as_str(),
        "update-ca-certificates",
    ])
    .await?
        != 0
    {
        return Err(ClusterError::Certificate(
            "Failed to update certificates in Kind node".to_string(),
        ));
    }
    progress.success("Successfully injected custom certificates into Kind cluster");
    Ok(())
}

/// Create a kind cluster (parsing `kind_extra_args`) + optional cert injection
/// (`create_kind_cluster_with_options`).
pub async fn create_kind_cluster_with_options(
    kind: &str,
    cluster_name: &str,
    kind_extra_args: &str,
    force_recreate: bool,
    extra_certs: Option<&str>,
    progress: &dyn Progress,
) -> Result<()> {
    if !kind_installed(kind) {
        return Err(ClusterError::tool_not_installed("kind"));
    }
    let action = if force_recreate {
        "Recreating"
    } else {
        "Creating"
    };
    progress.progress(&format!("{action} Kind cluster \"{cluster_name}\"..."));
    let extra: Vec<String> = if kind_extra_args.trim().is_empty() {
        Vec::new()
    } else {
        shlex::split(kind_extra_args).unwrap_or_default()
    };
    match create_kind_cluster(kind, cluster_name, &extra, force_recreate).await {
        Ok(()) => {
            if let Some(certs) = extra_certs {
                inject_certificates(certs, cluster_name, progress).await?;
            }
            Ok(())
        }
        Err(ClusterError::AlreadyExists(_)) if !force_recreate => {
            progress.progress(&format!(
                "Kind cluster \"{cluster_name}\" already exists, continuing..."
            ));
            if let Some(certs) = extra_certs {
                inject_certificates(certs, cluster_name, progress).await?;
            }
            Ok(())
        }
        Err(e) => {
            let action = if force_recreate { "recreate" } else { "create" };
            Err(ClusterError::Operation(format!(
                "Failed to {action} kind cluster \"{cluster_name}\": {e}"
            )))
        }
    }
}

pub async fn delete_kind_cluster_with_feedback(
    kind: &str,
    cluster_name: &str,
    _progress: &dyn Progress,
) -> Result<()> {
    if !kind_installed(kind) {
        return Err(ClusterError::tool_not_installed("kind"));
    }
    delete_kind_cluster(kind, cluster_name).await.map_err(|e| {
        ClusterError::Operation(format!(
            "Failed to delete kind cluster \"{cluster_name}\": {e}"
        ))
    })
}
