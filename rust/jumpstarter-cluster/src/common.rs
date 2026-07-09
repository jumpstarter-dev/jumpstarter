//! Shared validation + tool-detection helpers (`cluster/common.py`).

use crate::error::{ClusterError, Result};

// NodePorts the operator exposes + the kind host-port mappings (`cluster/common.py`).
pub const GRPC_NODEPORT: u16 = 30010;
pub const ROUTER_NODEPORT: u16 = 30011;
pub const LOGIN_NODEPORT: u16 = 30014;
pub const KIND_GRPC_HOST_PORT: u16 = 8082;
pub const KIND_ROUTER_HOST_PORT: u16 = 8083;

/// Whether a binary resolves in `PATH` (the Rust analog of `shutil.which`).
pub fn tool_installed(bin: &str) -> bool {
    which::which(bin).is_ok()
}

/// Trim surrounding whitespace from a cluster name (`format_cluster_name`).
pub fn format_cluster_name(name: &str) -> String {
    name.trim().to_string()
}

/// Trim + reject an empty cluster name (`validate_cluster_name`).
pub fn validate_cluster_name(name: &str) -> Result<String> {
    let formatted = format_cluster_name(name);
    if formatted.is_empty() {
        return Err(ClusterError::Validation(
            "Cluster name cannot be empty".to_string(),
        ));
    }
    Ok(formatted)
}

/// Extract the host from an `[user@]host` SSH target (`extract_host_from_ssh`).
pub fn extract_host_from_ssh(ssh_host: &str) -> &str {
    ssh_host.rsplit('@').next().unwrap_or(ssh_host)
}

/// Expand a leading `~` and make a cert path absolute (`get_extra_certs_path`).
pub fn get_extra_certs_path(extra_certs: &str) -> Option<String> {
    if extra_certs.is_empty() {
        return None;
    }
    let expanded = if extra_certs == "~" {
        home::home_dir().unwrap_or_else(|| std::path::PathBuf::from(extra_certs))
    } else if let Some(rest) = extra_certs.strip_prefix("~/") {
        home::home_dir()
            .map(|h| h.join(rest))
            .unwrap_or_else(|| std::path::PathBuf::from(extra_certs))
    } else {
        std::path::PathBuf::from(extra_certs)
    };
    let abs = if expanded.is_absolute() {
        expanded
    } else {
        std::env::current_dir()
            .map(|c| c.join(&expanded))
            .unwrap_or(expanded)
    };
    Some(abs.to_string_lossy().into_owned())
}

/// At most one local cluster type may be selected (`validate_cluster_type`).
/// Returns the chosen type (`"kind"`/`"minikube"`) or `None`.
pub fn validate_cluster_type(kind: Option<&str>, minikube: Option<&str>) -> Result<Option<String>> {
    match (kind, minikube) {
        (Some(_), Some(_)) => Err(ClusterError::Validation(
            "You can only select one local cluster type (--kind or --minikube)".to_string(),
        )),
        (Some(_), None) => Ok(Some("kind".to_string())),
        (None, Some(_)) => Ok(Some("minikube".to_string())),
        (None, None) => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_and_formats_names() {
        assert_eq!(validate_cluster_name("  foo  ").unwrap(), "foo");
        assert!(validate_cluster_name("   ").is_err());
    }

    #[test]
    fn extracts_ssh_host() {
        assert_eq!(
            extract_host_from_ssh("user@host.example.com"),
            "host.example.com"
        );
        assert_eq!(
            extract_host_from_ssh("host.example.com"),
            "host.example.com"
        );
    }

    #[test]
    fn validates_single_cluster_type() {
        assert_eq!(
            validate_cluster_type(Some("kind"), None)
                .unwrap()
                .as_deref(),
            Some("kind")
        );
        assert_eq!(
            validate_cluster_type(None, Some("minikube"))
                .unwrap()
                .as_deref(),
            Some("minikube")
        );
        assert_eq!(validate_cluster_type(None, None).unwrap(), None);
        assert!(validate_cluster_type(Some("kind"), Some("minikube")).is_err());
    }
}
