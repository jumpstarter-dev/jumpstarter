//! Cluster info/list result types — serde mirrors of `clusters.py`
//! (`V1Alpha1JumpstarterInstance` / `V1Alpha1ClusterInfo` / `V1Alpha1ClusterList`).
//!
//! Field order + aliases match the Python `JsonBaseModel.dump_json`
//! (`by_alias=True`, NO `exclude_none` — so `None` is emitted as `null`, which is
//! serde's default `Option` behavior; do not add `skip_serializing_if`).

use serde::{Deserialize, Serialize};

fn api_version() -> String {
    "jumpstarter.dev/v1alpha1".to_string()
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct JumpstarterInstance {
    #[serde(rename = "apiVersion", default = "api_version")]
    pub api_version: String,
    #[serde(default = "JumpstarterInstance::kind_default")]
    pub kind: String,
    pub installed: bool,
    pub version: Option<String>,
    pub namespace: Option<String>,
    pub status: Option<String>,
    #[serde(rename = "hasCrds", default)]
    pub has_crds: bool,
    pub error: Option<String>,
    pub basedomain: Option<String>,
    #[serde(rename = "controllerEndpoint")]
    pub controller_endpoint: Option<String>,
    #[serde(rename = "routerEndpoint")]
    pub router_endpoint: Option<String>,
}

impl JumpstarterInstance {
    fn kind_default() -> String {
        "JumpstarterInstance".to_string()
    }

    /// A not-installed instance (the common case for a bare cluster).
    pub fn not_installed() -> Self {
        Self {
            api_version: api_version(),
            kind: Self::kind_default(),
            installed: false,
            version: None,
            namespace: None,
            status: None,
            has_crds: false,
            error: None,
            basedomain: None,
            controller_endpoint: None,
            router_endpoint: None,
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ClusterInfo {
    #[serde(rename = "apiVersion", default = "api_version")]
    pub api_version: String,
    #[serde(default = "ClusterInfo::kind_default")]
    pub kind: String,
    pub name: String,
    pub cluster: String,
    pub server: String,
    pub user: String,
    pub namespace: String,
    #[serde(rename = "isCurrent")]
    pub is_current: bool,
    pub r#type: String,
    pub accessible: bool,
    pub version: Option<String>,
    pub jumpstarter: JumpstarterInstance,
    pub error: Option<String>,
}

impl ClusterInfo {
    fn kind_default() -> String {
        "ClusterInfo".to_string()
    }

    /// The `CURRENT NAME TYPE STATUS JUMPSTARTER VERSION NAMESPACE` table cells
    /// (`clusters.py:rich_add_rows`).
    pub fn row_cells(&self) -> Vec<String> {
        let current = if self.is_current { "*" } else { "" };
        let status = if self.accessible { "Running" } else { "Stopped" };
        let jumpstarter = if self.jumpstarter.error.is_some() {
            "Error"
        } else if self.jumpstarter.installed {
            "Yes"
        } else {
            "No"
        };
        let version = self.jumpstarter.version.clone().unwrap_or_else(|| "-".to_string());
        let namespace = self.jumpstarter.namespace.clone().unwrap_or_else(|| "-".to_string());
        vec![
            current.to_string(),
            self.name.clone(),
            self.r#type.clone(),
            status.to_string(),
            jumpstarter.to_string(),
            version,
            namespace,
        ]
    }

    pub fn columns() -> Vec<String> {
        ["CURRENT", "NAME", "TYPE", "STATUS", "JUMPSTARTER", "VERSION", "NAMESPACE"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ClusterList {
    #[serde(rename = "apiVersion", default = "api_version")]
    pub api_version: String,
    pub items: Vec<ClusterInfo>,
    #[serde(default = "ClusterList::kind_default")]
    pub kind: String,
}

impl ClusterList {
    fn kind_default() -> String {
        "ClusterList".to_string()
    }

    pub fn new(items: Vec<ClusterInfo>) -> Self {
        Self { api_version: api_version(), items, kind: Self::kind_default() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> ClusterInfo {
        ClusterInfo {
            api_version: api_version(),
            kind: ClusterInfo::kind_default(),
            name: "kind-test".to_string(),
            cluster: "kind-kind-test".to_string(),
            server: "https://127.0.0.1:6443".to_string(),
            user: "kind-kind-test".to_string(),
            namespace: "default".to_string(),
            is_current: true,
            r#type: "kind".to_string(),
            accessible: true,
            version: Some("1.28.0".to_string()),
            jumpstarter: JumpstarterInstance {
                installed: true,
                version: Some("0.1.0".to_string()),
                namespace: Some("jumpstarter".to_string()),
                ..JumpstarterInstance::not_installed()
            },
            error: None,
        }
    }

    #[test]
    fn row_cells_match_python() {
        assert_eq!(
            sample().row_cells(),
            vec!["*", "kind-test", "kind", "Running", "Yes", "0.1.0", "jumpstarter"]
        );
    }

    #[test]
    fn not_installed_and_stopped_render_dashes() {
        let mut c = sample();
        c.is_current = false;
        c.accessible = false;
        c.jumpstarter = JumpstarterInstance::not_installed();
        assert_eq!(c.row_cells(), vec!["", "kind-test", "kind", "Stopped", "No", "-", "-"]);
    }

    #[test]
    fn json_emits_nulls_and_aliases() {
        let json = serde_json::to_string(&JumpstarterInstance::not_installed()).unwrap();
        assert!(json.contains("\"apiVersion\":\"jumpstarter.dev/v1alpha1\""));
        assert!(json.contains("\"hasCrds\":false"));
        assert!(json.contains("\"version\":null"));
        assert!(json.contains("\"controllerEndpoint\":null"));
    }
}
