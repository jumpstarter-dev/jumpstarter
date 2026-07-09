//! Controller version selection (`controller.py`
//! `get_latest_compatible_controller_version`): query the quay tag API and pick
//! the newest tag matching the client's `major.minor` (else the newest overall).

use std::time::Duration;

use semver::Version;

use crate::error::{ClusterError, Result};

const QUAY_TAGS_URL: &str =
    "https://quay.io/api/v1/repository/jumpstarter-dev/jumpstarter-operator/tag/";

fn parse_lenient(v: &str) -> Option<Version> {
    let s = v.strip_prefix('v').unwrap_or(v);
    Version::parse(s).ok()
}

pub async fn get_latest_compatible_controller_version(
    client_version: Option<&str>,
) -> Result<String> {
    let client_parsed = match client_version {
        None => None,
        Some(v) => Some(
            parse_lenient(v)
                .ok_or_else(|| ClusterError::Version(format!("Invalid client version '{v}'")))?,
        ),
    };

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| ClusterError::Version(e.to_string()))?;
    tracing::debug!(url = QUAY_TAGS_URL, client_version = ?client_version, "fetching controller version tags from quay");
    let resp = client
        .get(QUAY_TAGS_URL)
        .send()
        .await
        .and_then(|r| r.error_for_status())
        .map_err(|e| {
            tracing::warn!(error = %e, "failed to fetch controller versions");
            ClusterError::Version(format!("Failed to fetch controller versions: {e}"))
        })?;
    let data: serde_json::Value = resp.json().await.map_err(|e| {
        tracing::warn!(error = %e, "failed to parse controller versions response");
        ClusterError::Version(format!("Failed to fetch controller versions: {e}"))
    })?;

    let tags = data.get("tags").and_then(|v| v.as_array()).ok_or_else(|| {
        ClusterError::Version("Unexpected response fetching controller version".to_string())
    })?;

    let mut compatible: Vec<(Version, String)> = Vec::new();
    let mut fallback: Vec<(Version, String)> = Vec::new();
    for tag in tags {
        let Some(name) = tag.get("name").and_then(|v| v.as_str()) else {
            continue;
        };
        let Some(version) = parse_lenient(name) else {
            continue;
        };
        match &client_parsed {
            Some(c) if version.major == c.major && version.minor == c.minor => {
                compatible.push((version, name.to_string()))
            }
            _ => fallback.push((version, name.to_string())),
        }
    }

    tracing::debug!(
        compatible = compatible.len(),
        fallback = fallback.len(),
        "selecting newest controller version"
    );
    let newest = |mut v: Vec<(Version, String)>| {
        v.sort();
        v.pop().map(|(_, tag)| tag)
    };
    match newest(compatible).or_else(|| newest(fallback)) {
        Some(tag) => {
            tracing::info!(version = %tag, "selected controller version");
            Ok(tag)
        }
        None => {
            tracing::warn!("no valid controller versions found in the repository");
            Err(ClusterError::Version(
                "No valid controller versions found in the repository".to_string(),
            ))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_v_prefixed_versions() {
        assert_eq!(parse_lenient("v1.2.3"), Some(Version::new(1, 2, 3)));
        assert_eq!(parse_lenient("0.7.0"), Some(Version::new(0, 7, 0)));
        assert_eq!(parse_lenient("latest"), None);
    }
}
