//! Flat `DriverNode`s → proto `GetReportResponse` assembly.
//!
//! The foreign host hands Rust the introspected driver tree as a flat
//! [`DriverNode`] list (mirroring Python `Driver.enumerate()`); Rust builds the
//! `GetReportResponse`/`DriverInstanceReport` the controller `RegisterRequest` and the
//! client `GetReport` consume — moving report assembly off the Python side.

use jumpstarter_protocol::v1::{DriverInstanceReport, GetReportResponse};

use jumpstarter_codec::dto::DriverNode;

/// Build the proto report from the foreign host's flat node list.
pub fn assemble_report(nodes: &[DriverNode]) -> GetReportResponse {
    let reports = nodes
        .iter()
        .map(|node| DriverInstanceReport {
            uuid: node.uuid.clone(),
            parent_uuid: node.parent_uuid.clone(),
            labels: node.labels.clone(),
            description: node.description.clone(),
            methods_description: node.methods_description.clone(),
            // Carry the native interface descriptors to the client so it can encode/decode native
            // calls on-demand (the client builds a descriptor pool from these). `None` for a driver
            // with no introspected interface (legacy dispatch only).
            descriptor_set: node.descriptor_set.clone(),
        })
        .collect();
    // Only `reports` comes from the driver tree; `uuid`/`labels`/`alternative_endpoints`
    // are exporter-level and filled by the registration/serving path, not here.
    GetReportResponse {
        reports,
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn labels(client: &str) -> HashMap<String, String> {
        HashMap::from([("jumpstarter.dev/client".to_string(), client.to_string())])
    }

    #[test]
    fn assembles_report_preserving_parent_links() {
        let nodes = vec![
            DriverNode::root(
                "root",
                labels("pkg.Root"),
                Some("root dev".into()),
                HashMap::from([("on".to_string(), "power on".to_string())]),
            ),
            DriverNode {
                uuid: "c1".into(),
                parent_uuid: Some("root".into()),
                labels: labels("pkg.Power"),
                description: None,
                methods_description: HashMap::new(),
                descriptor_set: None,
            },
            DriverNode {
                uuid: "c2".into(),
                parent_uuid: Some("c1".into()),
                labels: labels("pkg.Inner"),
                description: None,
                methods_description: HashMap::new(),
                descriptor_set: None,
            },
        ];

        let report = assemble_report(&nodes);
        let by_uuid: HashMap<_, _> = report
            .reports
            .iter()
            .map(|r| (r.uuid.as_str(), r))
            .collect();

        assert_eq!(report.reports.len(), 3);
        assert_eq!(by_uuid["root"].parent_uuid, None);
        assert_eq!(by_uuid["c1"].parent_uuid.as_deref(), Some("root"));
        assert_eq!(by_uuid["c2"].parent_uuid.as_deref(), Some("c1"));
        assert_eq!(by_uuid["root"].methods_description["on"], "power on");
        assert_eq!(by_uuid["c1"].labels["jumpstarter.dev/client"], "pkg.Power");
    }
}
