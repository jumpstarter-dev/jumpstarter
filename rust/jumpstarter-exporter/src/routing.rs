//! The hub's per-driver routing/aggregation backend.
//!
//! The exporter hub spawns one driver host per top-level `export:` entry (each serving that
//! entry's cohesive subtree) and federates them through a [`RoutingBackend`]: a single
//! [`DriverBackend`] that the per-lease [`crate::session::RoutingTable`] routes into,
//! unchanged. The hub owns the tree shape that Python used to build — it synthesizes the root
//! `Composite` (which the Python exporter wraps every tree in), drops each child host's own
//! wrapper root, re-parents each entry under the hub root, and dispatches every call/stream to
//! the owning entry's backend by UUID. Distinct entries dispatch concurrently.

use std::collections::HashMap;
use std::sync::Arc;

use jumpstarter_protocol::v1::{
    DriverCallRequest, DriverCallResponse, DriverInstanceReport, GetReportResponse,
    LogStreamResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::AsciiMetadataValue;
use tonic::Status;

use crate::backend::{DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen};

/// The `jumpstarter.dev/client` of the synthetic root: the same `Composite` the Python
/// exporter roots every driver tree in (`exporter/host.py:294-295`), so the client builds an
/// identical tree.
const COMPOSITE_CLIENT: &str = "jumpstarter_driver_composite.client.CompositeClient";
const CLIENT_LABEL: &str = "jumpstarter.dev/client";

/// Channel buffer for the merged log-stream pump.
const LOG_BUFFER: usize = 32;

/// One top-level `export:` entry served by its own driver host.
pub struct HostedEntry {
    /// The entry name (the `export:` map key); already carried as the node's
    /// `jumpstarter.dev/name` label by the child host, kept here for diagnostics.
    pub name: String,
    pub backend: Arc<dyn DriverBackend>,
}

/// Federates one child [`DriverBackend`] per top-level `export:` entry under a synthesized
/// root `Composite`, routing each call/stream to the owning entry by UUID.
pub struct RoutingBackend {
    /// uuid → the entry backend owning that driver instance.
    routes: HashMap<String, Arc<dyn DriverBackend>>,
    /// Distinct entry backends (one per entry), for the merged `log_stream`.
    children: Vec<Arc<dyn DriverBackend>>,
    /// The merged full-tree report (synthetic root + every entry subtree).
    report: GetReportResponse,
}

impl RoutingBackend {
    /// Federate the hosted entries: query each child's subtree report, stitch them under one
    /// synthetic root, and build the uuid→backend routing table. `root_uuid` is the hub-minted
    /// id for the root `Composite`; `description` is the exporter spec description (root only).
    pub async fn build(
        root_uuid: String,
        description: Option<String>,
        entries: Vec<HostedEntry>,
    ) -> Result<Self, Status> {
        let mut routes: HashMap<String, Arc<dyn DriverBackend>> = HashMap::new();
        let mut children: Vec<Arc<dyn DriverBackend>> = Vec::new();
        let mut reports = vec![DriverInstanceReport {
            uuid: root_uuid.clone(),
            parent_uuid: None,
            labels: HashMap::from([(CLIENT_LABEL.to_string(), COMPOSITE_CLIENT.to_string())]),
            description,
            methods_description: HashMap::new(),
        }];

        for entry in entries {
            let sub = entry.backend.get_report().await?;
            // The child host wraps its single entry in its own `Composite` root (the standard
            // exporter instantiation). Find that wrapper so we can drop it and re-root.
            let host_root: Option<String> = sub
                .reports
                .iter()
                .find(|r| r.parent_uuid.is_none())
                .map(|r| r.uuid.clone());

            for mut node in sub.reports {
                if Some(&node.uuid) == host_root.as_ref() {
                    // Drop the child's wrapper root — the hub root replaces it.
                    continue;
                }
                if node.parent_uuid == host_root {
                    // Re-parent the entry's top node(s) under the hub root, preserving the
                    // `jumpstarter.dev/name` label the child host already set.
                    node.parent_uuid = Some(root_uuid.clone());
                }
                routes.insert(node.uuid.clone(), entry.backend.clone());
                reports.push(node);
            }
            children.push(entry.backend);
        }

        Ok(Self {
            routes,
            children,
            report: GetReportResponse {
                reports,
                ..Default::default()
            },
        })
    }

    /// The owning backend for a driver UUID. Unknown UUID → `UNKNOWN` (matching the
    /// single-host `RoutingTable::route`); the synthetic root has no methods, so a call to it
    /// is `UNKNOWN` too.
    #[allow(clippy::result_large_err)]
    fn route(&self, uuid: &str) -> Result<&Arc<dyn DriverBackend>, Status> {
        self.routes
            .get(uuid)
            .ok_or_else(|| Status::unknown(format!("unknown driver uuid: {uuid}")))
    }
}

/// Extract the target driver UUID from a router `Stream`'s `request` metadata (the JSON
/// `{"kind":..,"uuid":..}` the client sends), so the hub can route the stream to the owning
/// entry before forwarding any frame.
#[allow(clippy::result_large_err)]
fn uuid_from_request(request_meta: &AsciiMetadataValue) -> Result<String, Status> {
    let json = request_meta
        .to_str()
        .map_err(|_| Status::unknown("malformed `request` stream metadata"))?;
    let value: serde_json::Value = serde_json::from_str(json)
        .map_err(|e| Status::unknown(format!("malformed `request` stream metadata: {e}")))?;
    value
        .get("uuid")
        .and_then(|u| u.as_str())
        .map(str::to_string)
        .ok_or_else(|| Status::unknown("`request` stream metadata missing `uuid`"))
}

#[tonic::async_trait]
impl DriverBackend for RoutingBackend {
    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        Ok(self.report.clone())
    }

    async fn driver_call(&self, req: DriverCallRequest) -> Result<DriverCallResponse, Status> {
        self.route(&req.uuid)?.driver_call(req).await
    }

    async fn streaming_driver_call(
        &self,
        req: StreamingDriverCallRequest,
    ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status> {
        self.route(&req.uuid)?.streaming_driver_call(req).await
    }

    async fn open_router_stream(
        &self,
        request_meta: AsciiMetadataValue,
        uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        let uuid = uuid_from_request(&request_meta)?;
        self.route(&uuid)?
            .open_router_stream(request_meta, uplink)
            .await
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        // Merge every entry's log stream into one (each child pumps into a shared channel).
        let (tx, rx) = mpsc::channel::<Result<LogStreamResponse, Status>>(LOG_BUFFER);
        for backend in &self.children {
            let backend = backend.clone();
            let tx = tx.clone();
            tokio::spawn(async move {
                if let Ok(mut stream) = backend.log_stream().await {
                    while let Some(item) = stream.next().await {
                        if tx.send(item).await.is_err() {
                            break;
                        }
                    }
                }
            });
        }
        Ok(Box::pin(ReceiverStream::new(rx)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(
        uuid: &str,
        parent: Option<&str>,
        name: Option<&str>,
        client: &str,
    ) -> DriverInstanceReport {
        let mut labels = HashMap::from([(CLIENT_LABEL.to_string(), client.to_string())]);
        if let Some(n) = name {
            labels.insert("jumpstarter.dev/name".to_string(), n.to_string());
        }
        DriverInstanceReport {
            uuid: uuid.to_string(),
            parent_uuid: parent.map(str::to_string),
            labels,
            description: None,
            methods_description: HashMap::new(),
        }
    }

    /// A child backend that serves a canned subtree report and records the uuids it was
    /// called with (to prove routing). Streams are unused in these tests.
    struct MockEntry {
        report: GetReportResponse,
        called: Arc<std::sync::Mutex<Vec<String>>>,
    }

    impl MockEntry {
        /// One entry: a wrapper `Composite` root + the named entry leaf (+ optional grandchild).
        fn new(wrapper: &str, entry_uuid: &str, name: &str, grandchild: Option<&str>) -> Self {
            let mut reports = vec![
                node(wrapper, None, None, COMPOSITE_CLIENT),
                node(entry_uuid, Some(wrapper), Some(name), "pkg.client.Thing"),
            ];
            if let Some(gc) = grandchild {
                reports.push(node(gc, Some(entry_uuid), Some("inner"), "pkg.client.Inner"));
            }
            Self {
                report: GetReportResponse {
                    reports,
                    ..Default::default()
                },
                called: Arc::new(std::sync::Mutex::new(Vec::new())),
            }
        }
    }

    #[tonic::async_trait]
    impl DriverBackend for MockEntry {
        async fn get_report(&self) -> Result<GetReportResponse, Status> {
            Ok(self.report.clone())
        }
        async fn driver_call(
            &self,
            req: DriverCallRequest,
        ) -> Result<DriverCallResponse, Status> {
            self.called.lock().unwrap().push(req.uuid.clone());
            Ok(DriverCallResponse {
                uuid: req.uuid,
                result: None,
            })
        }
        async fn streaming_driver_call(
            &self,
            _req: StreamingDriverCallRequest,
        ) -> Result<ResponseStream<StreamingDriverCallResponse>, Status> {
            Ok(Box::pin(tokio_stream::empty()))
        }
        async fn open_router_stream(
            &self,
            _request_meta: AsciiMetadataValue,
            _uplink: FrameUplink,
        ) -> Result<RouterStreamOpen, Status> {
            Err(Status::unimplemented("unused"))
        }
        async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
            Ok(Box::pin(tokio_stream::empty()))
        }
    }

    async fn routing() -> (RoutingBackend, Arc<MockEntry>, Arc<MockEntry>) {
        let power = Arc::new(MockEntry::new("w1", "power-uuid", "power", None));
        let serial = Arc::new(MockEntry::new("w2", "serial-uuid", "serial", Some("gc-uuid")));
        let backend = RoutingBackend::build(
            "root-uuid".to_string(),
            Some("the lab".to_string()),
            vec![
                HostedEntry {
                    name: "power".to_string(),
                    backend: power.clone(),
                },
                HostedEntry {
                    name: "serial".to_string(),
                    backend: serial.clone(),
                },
            ],
        )
        .await
        .unwrap();
        (backend, power, serial)
    }

    #[tokio::test]
    async fn stitches_entries_under_one_synthetic_root() {
        let (backend, _p, _s) = routing().await;
        let report = backend.get_report().await.unwrap();
        let by_uuid: HashMap<_, _> =
            report.reports.iter().map(|r| (r.uuid.as_str(), r)).collect();

        // Exactly one root, the synthetic Composite — the child wrappers (w1/w2) are dropped.
        let roots: Vec<_> = report
            .reports
            .iter()
            .filter(|r| r.parent_uuid.is_none())
            .collect();
        assert_eq!(roots.len(), 1);
        assert_eq!(roots[0].uuid, "root-uuid");
        assert_eq!(roots[0].labels[CLIENT_LABEL], COMPOSITE_CLIENT);
        assert_eq!(roots[0].description.as_deref(), Some("the lab"));
        assert!(!by_uuid.contains_key("w1"));
        assert!(!by_uuid.contains_key("w2"));

        // Each entry is re-parented under the hub root, keeping its name label.
        assert_eq!(by_uuid["power-uuid"].parent_uuid.as_deref(), Some("root-uuid"));
        assert_eq!(by_uuid["serial-uuid"].parent_uuid.as_deref(), Some("root-uuid"));
        assert_eq!(by_uuid["power-uuid"].labels["jumpstarter.dev/name"], "power");
        // Deeper descendants keep their original parent.
        assert_eq!(by_uuid["gc-uuid"].parent_uuid.as_deref(), Some("serial-uuid"));
    }

    #[tokio::test]
    async fn routes_driver_calls_to_the_owning_entry() {
        let (backend, power, serial) = routing().await;

        backend
            .driver_call(DriverCallRequest {
                uuid: "power-uuid".to_string(),
                method: "on".to_string(),
                args: vec![],
            })
            .await
            .unwrap();
        backend
            .driver_call(DriverCallRequest {
                uuid: "gc-uuid".to_string(),
                method: "x".to_string(),
                args: vec![],
            })
            .await
            .unwrap();

        assert_eq!(*power.called.lock().unwrap(), vec!["power-uuid"]);
        // The grandchild routes to the serial entry that owns its subtree.
        assert_eq!(*serial.called.lock().unwrap(), vec!["gc-uuid"]);
    }

    #[tokio::test]
    async fn unknown_uuid_and_root_are_unknown() {
        let (backend, _p, _s) = routing().await;
        // An unknown uuid is UNKNOWN.
        let err = backend
            .driver_call(DriverCallRequest {
                uuid: "nope".to_string(),
                method: "on".to_string(),
                args: vec![],
            })
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unknown);
        // The synthetic root has no methods → also UNKNOWN (it isn't in the routes).
        let err = backend
            .driver_call(DriverCallRequest {
                uuid: "root-uuid".to_string(),
                method: "x".to_string(),
                args: vec![],
            })
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unknown);
    }
}
