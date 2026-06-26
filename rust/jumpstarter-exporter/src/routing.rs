//! The hub's per-driver routing/aggregation backend.
//!
//! The exporter hub spawns one driver host per top-level `export:` entry (each serving that
//! entry's cohesive subtree) and federates them through a [`RoutingBackend`]: a single
//! [`DriverBackend`] that the per-lease [`crate::session::RoutingTable`] routes into,
//! unchanged. The hub owns the tree shape: it synthesizes the root `Composite`, re-parents each
//! child host's entry root (which the host serves directly) under it, and dispatches every
//! call/stream to the owning entry's backend by UUID. Distinct entries dispatch concurrently.

use std::collections::HashMap;
use std::sync::Arc;

use jumpstarter_protocol::v1::{DriverInstanceReport, GetReportResponse, LogStreamResponse};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tokio_stream::StreamExt as _;
use tonic::metadata::{AsciiMetadataValue, MetadataMap};
use tonic::Status;

use crate::backend::{DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen};

/// The `jumpstarter.dev/client` of the hub's synthetic root `Composite`, so the client builds a
/// `CompositeClient` root with the entries as children (the standard single-exporter tree shape).
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
            // The synthesized composite root is not a real driver — no native interface.
            descriptor_set: None,
        }];

        for entry in entries {
            let sub = entry.backend.get_report().await?;
            // Each child host serves its single entry's subtree directly — the entry node is the
            // host's root (`parent_uuid == None`), carrying its `jumpstarter.dev/name` label.
            // Re-parent that root under the hub's synthesized root; everything else is unchanged.
            for mut node in sub.reports {
                if node.parent_uuid.is_none() {
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

    /// Route an opaque **native** unary call to the owning entry by the `x-jumpstarter-driver-uuid`
    /// metadata header, then forward to that entry's backend (its `ChannelBackend`/`ForeignDriver`
    /// does the actual native dispatch). Without this override the federated hub would hit the
    /// default "not supported".
    async fn forward_unary(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: bytes::Bytes,
    ) -> Result<(MetadataMap, bytes::Bytes, MetadataMap), Status> {
        let uuid = metadata
            .get(jumpstarter_transport::demux::DRIVER_UUID_KEY)
            .and_then(|v| v.to_str().ok())
            .ok_or_else(|| {
                Status::invalid_argument(format!(
                    "native call missing `{}` metadata",
                    jumpstarter_transport::demux::DRIVER_UUID_KEY
                ))
            })?
            .to_string();
        self.route(&uuid)?.forward_unary(path, metadata, body).await
    }

    /// Route an opaque native **server-streaming** call to the owning entry by the
    /// `x-jumpstarter-driver-uuid` header (the streaming analogue of `forward_unary`), then forward
    /// to that entry's backend. Without this override the federated hub would hit the default
    /// (forward_unary-as-one-item), which truncates a genuinely streaming method to its first item.
    async fn forward_stream(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: bytes::Bytes,
    ) -> Result<(MetadataMap, ResponseStream<bytes::Bytes>), Status> {
        let uuid = metadata
            .get(jumpstarter_transport::demux::DRIVER_UUID_KEY)
            .and_then(|v| v.to_str().ok())
            .ok_or_else(|| {
                Status::invalid_argument(format!(
                    "native call missing `{}` metadata",
                    jumpstarter_transport::demux::DRIVER_UUID_KEY
                ))
            })?
            .to_string();
        self.route(&uuid)?.forward_stream(path, metadata, body).await
    }

    /// Route an opaque native **client-/bidi-streaming** call to the owning entry by the
    /// `x-jumpstarter-driver-uuid` header (the fully general analogue of `forward_unary`/
    /// `forward_stream`), then forward to that entry's backend so the client's request-frame uplink
    /// reaches a real host without truncation.
    async fn forward_bidi(
        &self,
        path: &str,
        metadata: MetadataMap,
        uplink: ResponseStream<bytes::Bytes>,
    ) -> Result<(MetadataMap, ResponseStream<bytes::Bytes>), Status> {
        let uuid = metadata
            .get(jumpstarter_transport::demux::DRIVER_UUID_KEY)
            .and_then(|v| v.to_str().ok())
            .ok_or_else(|| {
                Status::invalid_argument(format!(
                    "native call missing `{}` metadata",
                    jumpstarter_transport::demux::DRIVER_UUID_KEY
                ))
            })?
            .to_string();
        self.route(&uuid)?.forward_bidi(path, metadata, uplink).await
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
            descriptor_set: None,
        }
    }

    /// A child backend that serves a canned subtree report and records the uuids it was
    /// called with (to prove routing). Streams are unused in these tests.
    struct MockEntry {
        report: GetReportResponse,
        called: Arc<std::sync::Mutex<Vec<String>>>,
    }

    impl MockEntry {
        /// One entry served directly as the host root: the named entry node (`parent_uuid ==
        /// None`, carrying its name label) + an optional grandchild.
        fn new(entry_uuid: &str, name: &str, grandchild: Option<&str>) -> Self {
            let mut reports = vec![node(entry_uuid, None, Some(name), "pkg.client.Thing")];
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
        async fn forward_unary(
            &self,
            _path: &str,
            metadata: MetadataMap,
            _body: bytes::Bytes,
        ) -> Result<(MetadataMap, bytes::Bytes, MetadataMap), Status> {
            // Record the driver uuid the hub routed by (the demux header), proving the routing.
            let uuid = metadata
                .get(jumpstarter_transport::demux::DRIVER_UUID_KEY)
                .and_then(|v| v.to_str().ok())
                .unwrap_or_default()
                .to_string();
            self.called.lock().unwrap().push(uuid);
            Ok((MetadataMap::new(), bytes::Bytes::new(), MetadataMap::new()))
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
        let power = Arc::new(MockEntry::new("power-uuid", "power", None));
        let serial = Arc::new(MockEntry::new("serial-uuid", "serial", Some("gc-uuid")));
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

        // Exactly one root: the synthetic Composite (each host serves its entry directly, so
        // there are no per-host wrapper roots to drop).
        let roots: Vec<_> = report
            .reports
            .iter()
            .filter(|r| r.parent_uuid.is_none())
            .collect();
        assert_eq!(roots.len(), 1);
        assert_eq!(roots[0].uuid, "root-uuid");
        assert_eq!(roots[0].labels[CLIENT_LABEL], COMPOSITE_CLIENT);
        assert_eq!(roots[0].description.as_deref(), Some("the lab"));

        // Each entry is re-parented under the hub root, keeping its name label.
        assert_eq!(by_uuid["power-uuid"].parent_uuid.as_deref(), Some("root-uuid"));
        assert_eq!(by_uuid["serial-uuid"].parent_uuid.as_deref(), Some("root-uuid"));
        assert_eq!(by_uuid["power-uuid"].labels["jumpstarter.dev/name"], "power");
        // Deeper descendants keep their original parent.
        assert_eq!(by_uuid["gc-uuid"].parent_uuid.as_deref(), Some("serial-uuid"));
    }

    /// A native unary call carrying the driver uuid in the demux header.
    fn native_call(uuid: &str) -> (MetadataMap, bytes::Bytes) {
        let mut md = MetadataMap::new();
        md.insert(
            jumpstarter_transport::demux::DRIVER_UUID_KEY,
            uuid.parse().unwrap(),
        );
        (md, bytes::Bytes::new())
    }

    #[tokio::test]
    async fn routes_driver_calls_to_the_owning_entry() {
        let (backend, power, serial) = routing().await;

        let (md, body) = native_call("power-uuid");
        backend.forward_unary("/p.S/On", md, body).await.unwrap();
        let (md, body) = native_call("gc-uuid");
        backend.forward_unary("/p.S/X", md, body).await.unwrap();

        assert_eq!(*power.called.lock().unwrap(), vec!["power-uuid"]);
        // The grandchild routes to the serial entry that owns its subtree.
        assert_eq!(*serial.called.lock().unwrap(), vec!["gc-uuid"]);
    }

    #[tokio::test]
    async fn routes_native_bidi_calls_to_the_owning_entry() {
        let (backend, power, serial) = routing().await;

        // A single-frame uplink is enough to prove routing: RoutingBackend.forward_bidi routes by
        // the demux header to the owning entry's backend (whose default forward_bidi records the
        // uuid via forward_unary).
        let (md, _body) = native_call("power-uuid");
        let uplink: ResponseStream<bytes::Bytes> =
            Box::pin(tokio_stream::once(Ok(bytes::Bytes::new())));
        let (_md, _stream) = backend.forward_bidi("/p.S/Stream", md, uplink).await.unwrap();

        let (md, _body) = native_call("gc-uuid");
        let uplink: ResponseStream<bytes::Bytes> =
            Box::pin(tokio_stream::once(Ok(bytes::Bytes::new())));
        let (_md, _stream) = backend.forward_bidi("/p.S/Stream", md, uplink).await.unwrap();

        assert_eq!(*power.called.lock().unwrap(), vec!["power-uuid"]);
        assert_eq!(*serial.called.lock().unwrap(), vec!["gc-uuid"]);
    }

    #[tokio::test]
    async fn unknown_uuid_and_root_are_unknown() {
        let (backend, _p, _s) = routing().await;
        // An unknown uuid is UNKNOWN.
        let (md, body) = native_call("nope");
        let err = backend
            .forward_unary("/p.S/On", md, body)
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unknown);
        // The synthetic root is not a real driver (not in the routes) → also UNKNOWN.
        let (md, body) = native_call("root-uuid");
        let err = backend
            .forward_unary("/p.S/X", md, body)
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unknown);
    }
}
