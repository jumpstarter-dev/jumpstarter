//! High-level cluster orchestration (`cluster/operations.py`): type selection +
//! delete-by-name. (`create_cluster_and_install` lives in [`crate::create`].)

use crate::common::validate_cluster_name;
use crate::detection::{auto_detect_cluster_type, detect_existing_cluster_type};
use crate::error::{ClusterError, Result};
use crate::kind::{delete_kind_cluster_with_feedback, kind_cluster_exists, kind_installed};
use crate::minikube::{
    delete_minikube_cluster_with_feedback, minikube_cluster_exists, minikube_installed,
};
use crate::progress::Progress;

/// Pick the cluster type from the `--kind`/`--minikube`/`--k3s` flags, or
/// auto-detect (`validate_cluster_type_selection`). At most one may be set.
pub fn validate_cluster_type_selection(
    kind: Option<&str>,
    minikube: Option<&str>,
    k3s: Option<&str>,
) -> Result<String> {
    let selected = [kind, minikube, k3s].iter().filter(|x| x.is_some()).count();
    if selected > 1 {
        return Err(ClusterError::Validation(
            "You can only select one cluster type: \"kind\", \"minikube\", or \"k3s\"".to_string(),
        ));
    }
    if kind.is_some() {
        Ok("kind".to_string())
    } else if minikube.is_some() {
        Ok("minikube".to_string())
    } else if k3s.is_some() {
        Ok("k3s".to_string())
    } else {
        auto_detect_cluster_type()
    }
}

/// Delete a local cluster by name, auto-detecting the type when not given
/// (`delete_cluster_by_name`). Confirms unless `force`.
pub async fn delete_cluster_by_name(
    cluster_name: &str,
    cluster_type: Option<&str>,
    force: bool,
    progress: &dyn Progress,
) -> Result<()> {
    let cluster_name = validate_cluster_name(cluster_name)?;

    let cluster_type = match cluster_type {
        Some("kind") => {
            if !kind_installed("kind") {
                return Err(ClusterError::tool_not_installed("kind"));
            }
            if !kind_cluster_exists("kind", &cluster_name).await {
                return Err(ClusterError::NotFound(format!(
                    "kind cluster \"{cluster_name}\" does not exist"
                )));
            }
            "kind".to_string()
        }
        Some("minikube") => {
            if !minikube_installed("minikube") {
                return Err(ClusterError::tool_not_installed("minikube"));
            }
            if !minikube_cluster_exists("minikube", &cluster_name).await {
                return Err(ClusterError::NotFound(format!(
                    "minikube cluster \"{cluster_name}\" does not exist"
                )));
            }
            "minikube".to_string()
        }
        Some(other) => {
            return Err(ClusterError::Validation(format!(
                "Unsupported cluster type \"{other}\". Supported types: kind, minikube"
            )));
        }
        None => match detect_existing_cluster_type(&cluster_name).await? {
            Some(t) => {
                progress.progress(&format!("Auto-detected {t} cluster \"{cluster_name}\""));
                t
            }
            None => {
                return Err(ClusterError::NotFound(format!(
                    "No cluster named \"{cluster_name}\" found"
                )))
            }
        },
    };

    if !force
        && !progress.confirm(&format!(
            "This will permanently delete the \"{cluster_name}\" {cluster_type} cluster and ALL its data. Continue?"
        ))
    {
        progress.progress("Cluster deletion cancelled.");
        return Ok(());
    }

    match cluster_type.as_str() {
        "kind" => delete_kind_cluster_with_feedback("kind", &cluster_name, progress).await?,
        "minikube" => {
            delete_minikube_cluster_with_feedback("minikube", &cluster_name, progress).await?
        }
        _ => unreachable!(),
    }
    progress.success(&format!(
        "Successfully deleted {cluster_type} cluster \"{cluster_name}\""
    ));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn type_selection_rejects_multiple_and_picks_one() {
        assert_eq!(
            validate_cluster_type_selection(Some("kind"), None, None).unwrap(),
            "kind"
        );
        assert_eq!(
            validate_cluster_type_selection(None, Some("minikube"), None).unwrap(),
            "minikube"
        );
        assert_eq!(
            validate_cluster_type_selection(None, None, Some("user@h")).unwrap(),
            "k3s"
        );
        assert!(validate_cluster_type_selection(Some("kind"), Some("minikube"), None).is_err());
    }
}
