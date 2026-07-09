//! The native (Rust) driver SDK: the author-facing [`Driver`] trait and the
//! [`NativeDriverBackend`] that serves it.
//!
//! A native driver is the Rust analogue of a Python `@export` driver: the author implements
//! [`Driver`] (its client class + `@export` methods), and the core serves it **directly** —
//! no FFI, no subprocess gRPC into another language. [`NativeDriverBackend`] assembles the
//! `DriverReport`, applies the value codec, and maps errors to `tonic::Status`, presenting the
//! same proto [`DriverBackend`] seam the hub federates. A native driver-host binary embeds this
//! to serve one driver on a UDS, exactly like the Python `jumpstarter.exporter_host`.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use jumpstarter_protocol::v1::{DriverInstanceReport, GetReportResponse, LogStreamResponse};
use jumpstarter_transport::{DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen};
use prost::Message as _;
use prost_reflect::prost_types::FileDescriptorSet;
use prost_reflect::DescriptorPool;
use serde_json::Value as Json;
use tonic::metadata::{AsciiMetadataValue, MetadataMap};
use tonic::Status;

use crate::dynamic_backend::DynamicBackend;
use crate::host::{DriverApi, DriverResultStream, DriverStreamOpen};
use jumpstarter_codec::error::DriverCallError;

const CLIENT_LABEL: &str = "jumpstarter.dev/client";
const NAME_LABEL: &str = "jumpstarter.dev/name";

/// A native Rust driver — the author-facing surface, mirroring a Python `@export` driver. The
/// core serves it directly: it assembles the report from [`Driver::client`]/[`Driver::methods`]
/// and dispatches [`Driver::call`] with the value codec applied around it.
#[async_trait]
pub trait Driver: Send + Sync {
    /// The client class (`jumpstarter.dev/client`) the client uses to drive this driver — e.g.
    /// `jumpstarter_driver_power.client.PowerClient` to be driven by the existing Python client.
    fn client(&self) -> String;

    /// Optional per-`@export`-method help text (`methods_description`).
    fn methods(&self) -> HashMap<String, String> {
        HashMap::new()
    }

    /// The driver's interface as a **self-contained** serialized [`FileDescriptorSet`] (the
    /// interface file plus its transitive well-known-type dependency files, deps-first) — exactly
    /// what the Python host emits via introspection. When present, [`NativeDriverBackend`] serves
    /// this driver's per-interface gRPC service **natively** (decode proto → [`Driver::call`] →
    /// encode proto, via [`DynamicBackend`]) and advertises the descriptor over `GetReport` so the
    /// client drives it over the native wire, identically to a Python driver. `None` (the default)
    /// means no native surface — only the legacy [`Driver::call`] dispatch.
    ///
    /// A driver whose methods all take/return no value can build this with
    /// [`empty_interface_descriptor_set`]; a driver with typed messages ships its own descriptor
    /// (e.g. a `tonic_build`/`prost_build` `file_descriptor_set` embedded via `include_bytes!`).
    fn descriptor_set(&self) -> Option<Vec<u8>> {
        None
    }

    /// Invoke an `@export` method with decoded JSON args, returning a JSON result.
    async fn call(&self, method: &str, args: Vec<Json>) -> Result<Json, DriverCallError>;
}

/// Adapts a native [`Driver`] to the binding-agnostic [`DriverApi`] seam that [`DynamicBackend`]
/// dispatches through. Only [`DriverApi::driver_call`] is exercised by the native unary path
/// (decode → dispatch → encode): it bridges the JSON-array args string to [`Driver::call`]'s typed
/// `Vec<Json>` and serializes the JSON result back. The streaming/byte-channel methods are not part
/// of the native unary surface and are unsupported here (a native Rust driver that needs streams
/// implements them through the typed servicer path, not this adapter).
struct NativeDriverApi {
    driver: Arc<dyn Driver>,
}

#[async_trait]
impl DriverApi for NativeDriverApi {
    async fn describe(&self) -> Result<Vec<jumpstarter_codec::dto::DriverNode>, DriverCallError> {
        // The report is assembled by `NativeDriverBackend` itself; `DynamicBackend` never calls this.
        Ok(Vec::new())
    }

    async fn driver_call(
        &self,
        _uuid: String,
        method_name: String,
        args_json: String,
    ) -> Result<String, DriverCallError> {
        let args: Vec<Json> = serde_json::from_str(&args_json).map_err(|e| {
            DriverCallError::InvalidArgument(format!("native args not a JSON array: {e}"))
        })?;
        let result = self.driver.call(&method_name, args).await?;
        serde_json::to_string(&result)
            .map_err(|e| DriverCallError::Unknown(format!("native result not serializable: {e}")))
    }

    async fn streaming_driver_call(
        &self,
        _uuid: String,
        _method_name: String,
        _args_json: String,
    ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
        Err(DriverCallError::Unimplemented(
            "native Rust drivers do not support streaming over the dynamic seam yet".to_string(),
        ))
    }

    async fn open_stream(
        &self,
        _request_json: String,
    ) -> Result<DriverStreamOpen, DriverCallError> {
        Err(DriverCallError::Unimplemented(
            "native Rust drivers do not support byte streams over the dynamic seam yet".to_string(),
        ))
    }
}

/// Build a self-contained, deps-first [`FileDescriptorSet`] (serialized) for a native driver
/// interface whose methods all take and return `google.protobuf.Empty` — the no-argument,
/// no-result shape (e.g. power `on`/`off`). This produces the same self-contained set the Python
/// host emits (`empty.proto` precedes the interface file that imports it), so [`NativeDriverBackend`]
/// can serve the native service and the client can decode the descriptor identically.
///
/// `package` is the proto package (e.g. `jumpstarter.interfaces.power.v1`), `service` the service
/// name (e.g. `PowerInterface`), and `methods` the UpperCamelCase RPC names (e.g. `["On", "Off"]`,
/// which map to the `@export` names `on`/`off` via the demux's `export_name_for`).
pub fn empty_interface_descriptor_set(package: &str, service: &str, methods: &[&str]) -> Vec<u8> {
    use prost_reflect::prost_types::{
        DescriptorProto, FileDescriptorProto, MethodDescriptorProto, ServiceDescriptorProto,
    };

    // The well-known empty.proto (package google.protobuf, message Empty, no fields), so the
    // interface file's `google.protobuf.Empty` references resolve from within the one set.
    let empty_file = FileDescriptorProto {
        name: Some("google/protobuf/empty.proto".into()),
        package: Some("google.protobuf".into()),
        message_type: vec![DescriptorProto {
            name: Some("Empty".into()),
            ..Default::default()
        }],
        syntax: Some("proto3".into()),
        ..Default::default()
    };

    let interface_file = FileDescriptorProto {
        name: Some(format!("{}.proto", package.replace('.', "/"))),
        package: Some(package.to_string()),
        dependency: vec!["google/protobuf/empty.proto".into()],
        service: vec![ServiceDescriptorProto {
            name: Some(service.to_string()),
            method: methods
                .iter()
                .map(|m| MethodDescriptorProto {
                    name: Some((*m).to_string()),
                    input_type: Some(".google.protobuf.Empty".into()),
                    output_type: Some(".google.protobuf.Empty".into()),
                    ..Default::default()
                })
                .collect(),
            ..Default::default()
        }],
        syntax: Some("proto3".into()),
        ..Default::default()
    };

    // deps-first: empty.proto precedes the file that imports it.
    FileDescriptorSet {
        file: vec![empty_file, interface_file],
    }
    .encode_to_vec()
}

/// Serves a single native [`Driver`] over the proto [`DriverBackend`] seam. The driver is the
/// host's root entry (`parent_uuid == None`, carrying its `jumpstarter.dev/name` label) — the
/// same shape a Python single-entry host produces — so the hub's `RoutingBackend` re-parents
/// native and Python entries uniformly under its synthesized root.
pub struct NativeDriverBackend {
    report: GetReportResponse,
    /// The native-gRPC dispatcher, built **eagerly** at construction from the driver's
    /// [`Driver::descriptor_set`] (so the native interface is instantly ready, mirroring the
    /// Python host's eager introspection). `None` when the driver advertises no descriptor or it
    /// fails to build — then native calls return `UNIMPLEMENTED`. The dispatcher owns the only
    /// reference to the [`Driver`] (via the [`NativeDriverApi`] adapter), so there is no separate
    /// driver field.
    native: Option<DynamicBackend>,
}

impl NativeDriverBackend {
    /// Serve `driver` as the top-level entry named `entry_name`.
    pub fn new(entry_name: &str, driver: Arc<dyn Driver>) -> Self {
        let driver_uuid = uuid::Uuid::new_v4().to_string();
        let descriptor_set = driver.descriptor_set();
        let reports = vec![DriverInstanceReport {
            uuid: driver_uuid.clone(),
            parent_uuid: None,
            labels: HashMap::from([
                (CLIENT_LABEL.to_string(), driver.client()),
                (NAME_LABEL.to_string(), entry_name.to_string()),
            ]),
            description: None,
            methods_description: driver.methods(),
            // Advertise the native interface (if any) so the client decodes the descriptor and
            // drives this driver over the native wire, identically to a Python driver.
            descriptor_set: descriptor_set.clone(),
        }];
        let native = descriptor_set
            .as_deref()
            .and_then(|bytes| build_native_backend(bytes, &driver_uuid, driver));
        Self {
            report: GetReportResponse {
                reports,
                ..Default::default()
            },
            native,
        }
    }
}

/// Build the on-demand [`DynamicBackend`] from a serialized [`FileDescriptorSet`], dispatching to
/// `driver` via the [`NativeDriverApi`] adapter. The driver's single uuid is the fallback, so a
/// native call without the `x-jumpstarter-driver-uuid` header still resolves (the common
/// single-driver host). Best-effort: an undecodable/unresolvable set is logged and yields `None`
/// (no native surface) rather than failing host startup — exactly like the Python host's
/// best-effort introspection.
fn build_native_backend(
    descriptor_set: &[u8],
    driver_uuid: &str,
    driver: Arc<dyn Driver>,
) -> Option<DynamicBackend> {
    let set = match FileDescriptorSet::decode(descriptor_set) {
        Ok(set) => set,
        Err(e) => {
            tracing::warn!(error = %e, "skipping undecodable native driver descriptor set");
            return None;
        }
    };
    let pool = match DescriptorPool::from_file_descriptor_set(set) {
        Ok(pool) => pool,
        Err(e) => {
            tracing::warn!(error = %e, "native interface build failed (unresolved import?); no native surface");
            return None;
        }
    };
    tracing::info!(native_files = pool.files().len(), "native interface ready");
    let api: Arc<dyn DriverApi> = Arc::new(NativeDriverApi { driver });
    Some(DynamicBackend::from_pool(
        &pool,
        Some(driver_uuid.to_string()),
        api,
    ))
}

#[tonic::async_trait]
impl DriverBackend for NativeDriverBackend {
    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        Ok(self.report.clone())
    }

    /// Serve an opaque **native** per-driver unary call in-process by dispatching it through the
    /// driver's [`DynamicBackend`] (built at construction from [`Driver::descriptor_set`]): decode
    /// the proto request against the method descriptor, invoke the `@export` method via
    /// [`Driver::call`], and encode the response — no generated servicer. This is the native Rust
    /// driver's counterpart to [`crate::foreign::ForeignDriver::forward_unary`], so a native call
    /// reaches a Rust driver through the hub exactly as it reaches a Python one.
    async fn forward_unary(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: bytes::Bytes,
    ) -> Result<(MetadataMap, bytes::Bytes, MetadataMap), Status> {
        match &self.native {
            Some(backend) => backend.forward_unary(path, metadata, body).await,
            None => Err(Status::unimplemented(
                "native interface not available for this driver (no descriptor_set)",
            )),
        }
    }

    /// The server-streaming half of the native surface, delegating to the driver's
    /// [`DynamicBackend`]. A native Rust driver that streams would have a server-streaming method in
    /// its descriptor; the `NativeDriverApi` adapter currently declines streaming, so such a method
    /// surfaces `UNIMPLEMENTED` until native Rust streaming drivers land.
    async fn forward_stream(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: bytes::Bytes,
    ) -> Result<(MetadataMap, ResponseStream<bytes::Bytes>), Status> {
        match &self.native {
            Some(backend) => backend.forward_stream(path, metadata, body).await,
            None => Err(Status::unimplemented(
                "native interface not available for this driver (no descriptor_set)",
            )),
        }
    }

    async fn open_router_stream(
        &self,
        _request_meta: AsciiMetadataValue,
        _uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        Err(Status::unimplemented(
            "byte streams are not yet supported by native Rust drivers",
        ))
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        Ok(Box::pin(tokio_stream::empty()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A minimal native driver: echoes its first arg, advertises a client class, and rejects
    /// unknown methods.
    struct Echo;

    #[async_trait]
    impl Driver for Echo {
        fn client(&self) -> String {
            "pkg.client.EchoClient".to_string()
        }
        fn methods(&self) -> HashMap<String, String> {
            HashMap::from([("echo".to_string(), "echo the first argument".to_string())])
        }
        async fn call(&self, method: &str, args: Vec<Json>) -> Result<Json, DriverCallError> {
            match method {
                "echo" => Ok(args.into_iter().next().unwrap_or(Json::Null)),
                other => Err(DriverCallError::Unimplemented(format!("no method {other}"))),
            }
        }
    }

    fn backend() -> NativeDriverBackend {
        NativeDriverBackend::new("thing", Arc::new(Echo))
    }

    #[tokio::test]
    async fn serves_driver_as_root() {
        let report = backend().get_report().await.unwrap();
        // The driver is the host root (no parent), carrying its name + client labels — no wrapper.
        assert_eq!(report.reports.len(), 1);
        let root = report
            .reports
            .iter()
            .find(|r| r.parent_uuid.is_none())
            .unwrap();
        assert_eq!(root.labels[NAME_LABEL], "thing");
        assert_eq!(root.labels[CLIENT_LABEL], "pkg.client.EchoClient");
        assert_eq!(root.methods_description["echo"], "echo the first argument");
    }

    /// A native power driver advertising a `PowerInterface` descriptor (`On`/`Off`, Empty→Empty),
    /// recording the `@export` calls it receives so the native dispatch can be asserted.
    struct Power {
        calls: std::sync::Mutex<Vec<String>>,
    }

    #[async_trait]
    impl Driver for Power {
        fn client(&self) -> String {
            "jumpstarter_driver_power.client.PowerClient".to_string()
        }
        fn descriptor_set(&self) -> Option<Vec<u8>> {
            Some(empty_interface_descriptor_set(
                "jumpstarter.interfaces.power.v1",
                "PowerInterface",
                &["On", "Off"],
            ))
        }
        async fn call(&self, method: &str, _args: Vec<Json>) -> Result<Json, DriverCallError> {
            self.calls.lock().unwrap().push(method.to_string());
            match method {
                "on" | "off" => Ok(Json::Null),
                other => Err(DriverCallError::Unimplemented(format!("no method {other}"))),
            }
        }
    }

    /// A descriptor-bearing native driver serves its interface over the native unary surface:
    /// `forward_unary("/…/PowerInterface/On")` decodes the (empty) request, dispatches `On`→`on`
    /// through `Driver::call`, and returns the empty `Empty` response — the same path the hub
    /// drives a Python driver over. The descriptor is also advertised on the report so the client
    /// can decode it.
    #[tokio::test]
    async fn native_forward_unary_dispatches_to_the_driver() {
        let power = Arc::new(Power {
            calls: std::sync::Mutex::new(Vec::new()),
        });
        let backend = NativeDriverBackend::new("power", power.clone());

        // The report advertises the native interface so the client decodes the descriptor; the
        // single driver's uuid (the demux target) comes from it, as the real client reads it.
        let report = backend.get_report().await.unwrap();
        assert!(report.reports[0].descriptor_set.is_some());
        let uuid = report.reports[0].uuid.clone();

        // A native On call: empty body, uuid carried in the demux header (as the live demux sets it).
        let mut md = MetadataMap::new();
        md.insert(
            crate::dynamic_backend::DRIVER_UUID_KEY,
            uuid.parse().unwrap(),
        );
        let (_init, body, _trailers) = backend
            .forward_unary(
                "/jumpstarter.interfaces.power.v1.PowerInterface/On",
                md,
                bytes::Bytes::new(),
            )
            .await
            .expect("native On dispatches");
        assert!(body.is_empty(), "On() returns Empty → empty bytes");

        // The driver was driven with the @export name `on` (UpperCamel `On` → snake `on`).
        assert_eq!(*power.calls.lock().unwrap(), vec!["on".to_string()]);
    }

    /// A driver without a descriptor advertises none on its report and declines native calls
    /// (UNIMPLEMENTED) — only the legacy `driver_call` path is available.
    #[tokio::test]
    async fn no_descriptor_set_declines_native_calls() {
        let backend = backend(); // Echo advertises no descriptor_set
        let report = backend.get_report().await.unwrap();
        assert!(report.reports[0].descriptor_set.is_none());

        let err = backend
            .forward_unary(
                "/whatever.Service/Method",
                MetadataMap::new(),
                bytes::Bytes::new(),
            )
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unimplemented);
    }
}
