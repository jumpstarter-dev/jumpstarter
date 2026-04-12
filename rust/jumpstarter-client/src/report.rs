//! Driver report and device tree discovery.

use std::collections::HashMap;

use crate::proto::jumpstarter::v1::{DriverInstanceReport, GetReportResponse};

/// The device tree reported by an exporter via `GetReport`.
#[derive(Debug, Clone)]
pub struct DriverReport {
    uuid: String,
    labels: HashMap<String, String>,
    instances: Vec<DriverInstance>,
}

impl DriverReport {
    pub(crate) fn from_response(resp: GetReportResponse) -> Self {
        let instances = resp
            .reports
            .into_iter()
            .map(DriverInstance::from_proto)
            .collect();
        Self {
            uuid: resp.uuid,
            labels: resp.labels,
            instances,
        }
    }

    /// The exporter's root UUID.
    pub fn uuid(&self) -> &str {
        &self.uuid
    }

    /// The exporter's labels.
    pub fn labels(&self) -> &HashMap<String, String> {
        &self.labels
    }

    /// All driver instances in the device tree.
    pub fn instances(&self) -> &[DriverInstance] {
        &self.instances
    }

    /// Find a driver instance by its `jumpstarter.dev/name` label.
    pub fn find_by_name(&self, name: &str) -> Option<&DriverInstance> {
        self.instances
            .iter()
            .find(|i| i.labels.get("jumpstarter.dev/name").map(|n| n.as_str()) == Some(name))
    }
}

/// A single driver instance in the device tree.
#[derive(Debug, Clone)]
pub struct DriverInstance {
    uuid: String,
    parent_uuid: Option<String>,
    labels: HashMap<String, String>,
    description: Option<String>,
    native_services: Vec<String>,
}

impl DriverInstance {
    fn from_proto(report: DriverInstanceReport) -> Self {
        Self {
            uuid: report.uuid,
            parent_uuid: report.parent_uuid,
            labels: report.labels,
            description: report.description,
            native_services: report.native_services,
        }
    }

    /// Unique ID of this driver instance within the exporter.
    pub fn uuid(&self) -> &str {
        &self.uuid
    }

    /// Parent driver UUID, or `None` for root-level drivers.
    pub fn parent_uuid(&self) -> Option<&str> {
        self.parent_uuid.as_deref()
    }

    /// Labels including `jumpstarter.dev/name`.
    pub fn labels(&self) -> &HashMap<String, String> {
        &self.labels
    }

    /// Human-readable description, if any.
    pub fn description(&self) -> Option<&str> {
        self.description.as_deref()
    }

    /// Fully-qualified gRPC service names supported by this driver.
    pub fn native_services(&self) -> &[String] {
        &self.native_services
    }
}
