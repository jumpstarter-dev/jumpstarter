//! `DynamicBackend` — a [`DriverBackend`] that serves a driver's native per-interface
//! gRPC methods **dynamically from their protobuf descriptors**, with no generated
//! servicer anywhere.
//!
//! It is the server-side counterpart to the native demux ([`jumpstarter_transport::demux`]):
//! the demux reads the `x-jumpstarter-driver-uuid` header, looks up a backend, and relays
//! the opaque call via [`DriverBackend::forward_unary`]. This backend implements
//! `forward_unary` by parsing the gRPC method `path`, resolving the matching
//! [`DynamicMethod`] from a [`prost_reflect::DescriptorPool`], and dispatching through the
//! existing binding-agnostic [`DriverApi`] seam — decode request bytes → positional JSON
//! args → `driver_call` → encode JSON result into the response message.
//!
//! Concretely the demux flow becomes:
//!
//! ```text
//! native client ── /pkg.Service/Method + uuid header ──▶ Demux
//!                                                          │  forward_unary(path, md, body)
//!                                                          ▼
//!                                              DynamicBackend  ──▶ dispatch (descriptor codec)
//!                                                                       │
//!                                                                       ▼  DriverApi::driver_call
//!                                                                  the driver (mock/real)
//! ```
//!
//! The other [`DriverBackend`] methods (`get_report`/`driver_call`/`streaming_driver_call`/
//! `open_router_stream`/`log_stream`) are **not** part of the native surface and return
//! `Status::unimplemented` — this backend is native-only.

use std::collections::HashMap;
use std::sync::Arc;

use bytes::Bytes;
use jumpstarter_protocol::v1::{GetReportResponse, LogStreamResponse};
use jumpstarter_transport::{DriverBackend, FrameUplink, ResponseStream, RouterStreamOpen};
use prost_reflect::{DescriptorPool, MethodDescriptor};
use tokio::sync::mpsc;
use tonic::metadata::{AsciiMetadataValue, MetadataMap};
use tonic::Status;

use crate::dynamic::{encode_result, request_bytes_to_args_json, DynamicMethod};
use crate::error::DriverCallError;
use crate::host::DriverApi;

/// Map a proto method name to the driver `@export` name — re-exported from the codec so callers
/// keep importing it from `jumpstarter_core::dynamic_backend` (the transitional facade path).
pub use jumpstarter_codec::export_name_for;

/// The invocation metadata key the native demux uses to select a driver — re-exported from
/// the transport demux so callers key on one constant.
pub use jumpstarter_transport::demux::DRIVER_UUID_KEY;

/// A [`DriverBackend`] that dynamically dispatches native per-interface gRPC methods to a
/// foreign [`DriverApi`], resolving each method from a protobuf [`DescriptorPool`].
///
/// `methods` is keyed by the **gRPC method path** (`/<service-full-name>/<MethodName>`), so
/// `forward_unary` is a direct `HashMap` lookup on the inbound `path`. `fallback_uuid` is the
/// driver this backend serves when a call omits the uuid header (the common single-driver
/// case; a multi-driver host keys the demux router by uuid before reaching here).
pub struct DynamicBackend {
    methods: HashMap<String, DynamicMethod>,
    fallback_uuid: Option<String>,
    driver_api: Arc<dyn DriverApi>,
}

impl DynamicBackend {
    /// Build from a descriptor pool, dispatching to `driver_api`. Every method of every
    /// service in the pool becomes a route; the `@export` name each maps to is derived from
    /// the proto method name via [`export_name_for`] (`SetVoltage` → `set_voltage`).
    ///
    /// `fallback_uuid` is used when an inbound native call has no `x-jumpstarter-driver-uuid`
    /// header — pass the single driver's uuid for a one-driver host, or `None` to require the
    /// header.
    pub fn from_pool(
        pool: &DescriptorPool,
        fallback_uuid: Option<String>,
        driver_api: Arc<dyn DriverApi>,
    ) -> Self {
        let mut methods = HashMap::new();
        for service in pool.services() {
            for method in service.methods() {
                let path = grpc_path(&method);
                let export = export_name_for(method.name());
                methods.insert(path, DynamicMethod::from_descriptor(&method, export));
            }
        }
        Self {
            methods,
            fallback_uuid,
            driver_api,
        }
    }

    /// Build from an explicit `path → DynamicMethod` map (when the caller wants full control
    /// over the `@export` mapping rather than [`export_name_for`]'s default).
    pub fn from_methods(
        methods: HashMap<String, DynamicMethod>,
        fallback_uuid: Option<String>,
        driver_api: Arc<dyn DriverApi>,
    ) -> Self {
        Self {
            methods,
            fallback_uuid,
            driver_api,
        }
    }

    /// Resolve the target driver uuid for a call: the `x-jumpstarter-driver-uuid` header if
    /// present, else this backend's single known uuid (`None` when neither is available).
    pub(crate) fn resolve_uuid(&self, metadata: &MetadataMap) -> Option<String> {
        metadata
            .get(DRIVER_UUID_KEY)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned)
            .or_else(|| self.fallback_uuid.clone())
    }

    /// If `path` names a native **byte-channel** method (a bidi `StreamData` `@exportstream`),
    /// return the driver `@export` name it maps to; otherwise `None`. The host serves these through
    /// the byte-plane pump (`open_stream`), not the typed dispatch — so `forward_bidi` consults this
    /// to tell a console/serial `Connect` from a typed unary/server-streaming method framed as bidi.
    pub(crate) fn byte_stream_export(&self, path: &str) -> Option<&str> {
        self.methods
            .get(path)
            .filter(|m| m.is_byte_stream())
            .map(|m| m.export_name())
    }
}

/// The gRPC method path for a descriptor: `/<service-full-name>/<MethodName>`.
fn grpc_path(method: &MethodDescriptor) -> String {
    format!(
        "/{}/{}",
        method.parent_service().full_name(),
        method.name()
    )
}

/// Decode `request_bytes` against `method`'s input descriptor, dispatch through the driver
/// seam, and encode the result against the output descriptor — returning the wire bytes of the
/// native response message. The descriptor-driven encode/decode lives in the codec
/// ([`jumpstarter_codec::dynamic`]); only the `DriverApi` round-trip is here on the driver side.
async fn dispatch(
    method: &DynamicMethod,
    uuid: &str,
    request_bytes: &[u8],
    driver_api: &dyn DriverApi,
) -> Result<Vec<u8>, DriverCallError> {
    // 1+2. Decode the opaque request bytes and map its fields onto positional args JSON.
    let args_json = request_bytes_to_args_json(method.input(), request_bytes)?;

    // 3. Dispatch through the existing dynamic driver seam.
    let result_json = driver_api
        .driver_call(uuid.to_string(), method.export_name().to_string(), args_json)
        .await?;

    // 4. Encode the JSON result into the output message and serialize to bytes.
    encode_result(&result_json, method.output())
}

/// The **server-streaming** analogue of [`dispatch`]: decode the request into positional args,
/// open the driver's result stream via
/// [`DriverApi::streaming_driver_call`](crate::host::DriverApi::streaming_driver_call), and
/// return a [`ResponseStream`] that encodes each yielded JSON result into one output message —
/// the wire shape of a server-streaming gRPC method. A pump task pulls from the driver stream
/// and pushes encoded message bytes into the returned stream (mirroring `ForeignDriver`'s legacy
/// streaming pump), so the stream lives independently of this call.
async fn dispatch_streaming(
    method: &DynamicMethod,
    uuid: &str,
    request_bytes: &[u8],
    driver_api: Arc<dyn DriverApi>,
) -> Result<ResponseStream<Bytes>, DriverCallError> {
    // Decode the request → positional args (synchronously, so a bad request fails the open).
    let args_json = request_bytes_to_args_json(method.input(), request_bytes)?;

    let export_name = method.export_name().to_string();
    let output = method.output().clone();
    let uuid = uuid.to_string();
    let (tx, rx) = mpsc::channel::<Result<Bytes, Status>>(16);
    tokio::spawn(async move {
        let results = match driver_api
            .streaming_driver_call(uuid, export_name, args_json)
            .await
        {
            Ok(r) => r,
            Err(e) => {
                let _ = tx.send(Err(Status::from(e))).await;
                return;
            }
        };
        loop {
            match results.next().await {
                Ok(Some(result_json)) => {
                    let item = match encode_result(&result_json, &output) {
                        Ok(bytes) => Ok(Bytes::from(bytes)),
                        Err(e) => Err(Status::from(e)),
                    };
                    let is_err = item.is_err();
                    if tx.send(item).await.is_err() || is_err {
                        break;
                    }
                }
                Ok(None) => break,
                Err(e) => {
                    let _ = tx.send(Err(Status::from(e))).await;
                    break;
                }
            }
        }
    });
    Ok(Box::pin(tokio_stream::wrappers::ReceiverStream::new(rx)))
}

#[tonic::async_trait]
impl DriverBackend for DynamicBackend {
    // --- native unary surface (the only implemented path) --------------------------------

    async fn forward_unary(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: Bytes,
    ) -> Result<(MetadataMap, Bytes, MetadataMap), Status> {
        tracing::trace!(%path, "dynamic backend native dispatch");
        let method = self.methods.get(path).ok_or_else(|| {
            Status::unimplemented(format!("no dynamic method for native path {path}"))
        })?;
        let uuid = self.resolve_uuid(&metadata).ok_or_else(|| {
            Status::invalid_argument("missing x-jumpstarter-driver-uuid header and no fallback uuid")
        })?;
        let response_bytes = dispatch(method, &uuid, &body, &*self.driver_api)
            .await
            .map_err(Status::from)?;
        Ok((
            MetadataMap::new(),
            Bytes::from(response_bytes),
            MetadataMap::new(),
        ))
    }

    async fn forward_stream(
        &self,
        path: &str,
        metadata: MetadataMap,
        body: Bytes,
    ) -> Result<(MetadataMap, ResponseStream<Bytes>), Status> {
        tracing::trace!(%path, "dynamic backend native server-streaming dispatch");
        let method = self.methods.get(path).ok_or_else(|| {
            Status::unimplemented(format!("no dynamic method for native path {path}"))
        })?;
        let uuid = self.resolve_uuid(&metadata).ok_or_else(|| {
            Status::invalid_argument("missing x-jumpstarter-driver-uuid header and no fallback uuid")
        })?;
        if method.is_server_streaming() {
            // A server-streaming `@export` (async generator): one output message per yielded result.
            let stream = dispatch_streaming(method, &uuid, &body, self.driver_api.clone())
                .await
                .map_err(Status::from)?;
            Ok((MetadataMap::new(), stream))
        } else {
            // A unary method framed as a one-message stream (the demux frames every call as
            // server-streaming; a unary client reads the single message identically).
            let response_bytes = dispatch(method, &uuid, &body, &*self.driver_api)
                .await
                .map_err(Status::from)?;
            let stream: ResponseStream<Bytes> =
                Box::pin(tokio_stream::once(Ok(Bytes::from(response_bytes))));
            Ok((MetadataMap::new(), stream))
        }
    }

    // --- non-native surface (declined; this backend is native-only) ----------------------

    async fn get_report(&self) -> Result<GetReportResponse, Status> {
        Err(Status::unimplemented(
            "DynamicBackend serves native methods only",
        ))
    }

    async fn open_router_stream(
        &self,
        _request_meta: AsciiMetadataValue,
        _uplink: FrameUplink,
    ) -> Result<RouterStreamOpen, Status> {
        Err(Status::unimplemented(
            "DynamicBackend serves native methods only",
        ))
    }

    async fn log_stream(&self) -> Result<ResponseStream<LogStreamResponse>, Status> {
        Err(Status::unimplemented(
            "DynamicBackend serves native methods only",
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dto::DriverNode;
    use crate::error::DriverCallError;
    use crate::host::{DriverResultStream, DriverStreamOpen};
    use prost::Message as _;
    use prost_reflect::prost_types::{
        field_descriptor_proto::{Label, Type},
        DescriptorProto, FieldDescriptorProto, FileDescriptorProto, MethodDescriptorProto,
        ServiceDescriptorProto,
    };
    use prost_reflect::{DynamicMessage, MessageDescriptor};
    use std::str::FromStr;
    use std::sync::Mutex;
    use tonic::transport::Server;

    use jumpstarter_transport::demux::{BytesCodec, Demux, SingleBackend};
    use jumpstarter_transport::transport::{connect_channel, InProcessTransport, Transport};

    // ---- hand-built descriptor pool (no .proto / protoc), package jumpstarter.driver.power.v1

    fn field(name: &str, number: i32, ty: Type) -> FieldDescriptorProto {
        FieldDescriptorProto {
            name: Some(name.to_string()),
            number: Some(number),
            label: Some(Label::Optional as i32),
            r#type: Some(ty as i32),
            ..Default::default()
        }
    }
    fn message(name: &str, fields: Vec<FieldDescriptorProto>) -> DescriptorProto {
        DescriptorProto {
            name: Some(name.to_string()),
            field: fields,
            ..Default::default()
        }
    }
    fn method(name: &str, input: &str, output: &str) -> MethodDescriptorProto {
        MethodDescriptorProto {
            name: Some(name.to_string()),
            input_type: Some(input.to_string()),
            output_type: Some(output.to_string()),
            ..Default::default()
        }
    }

    const PKG: &str = "jumpstarter.driver.power.v1";

    fn power_pool() -> DescriptorPool {
        let file = FileDescriptorProto {
            name: Some("power.proto".to_string()),
            package: Some(PKG.to_string()),
            syntax: Some("proto3".to_string()),
            message_type: vec![
                message("Empty", vec![]),
                message(
                    "SetVoltageRequest",
                    vec![field("millivolts", 1, Type::Int64)],
                ),
                message(
                    "PowerReading",
                    vec![
                        field("voltage", 1, Type::Double),
                        field("current", 2, Type::Double),
                    ],
                ),
            ],
            service: vec![ServiceDescriptorProto {
                name: Some("PowerInterface".to_string()),
                method: vec![
                    method("On", ".jumpstarter.driver.power.v1.Empty", ".jumpstarter.driver.power.v1.Empty"),
                    method(
                        "SetVoltage",
                        ".jumpstarter.driver.power.v1.SetVoltageRequest",
                        ".jumpstarter.driver.power.v1.Empty",
                    ),
                    method(
                        "Read",
                        ".jumpstarter.driver.power.v1.Empty",
                        ".jumpstarter.driver.power.v1.PowerReading",
                    ),
                    // A server-streaming method (an async-generator `@export`): `subscribe` yields a
                    // stream of PowerReading.
                    MethodDescriptorProto {
                        name: Some("Subscribe".to_string()),
                        input_type: Some(".jumpstarter.driver.power.v1.Empty".to_string()),
                        output_type: Some(".jumpstarter.driver.power.v1.PowerReading".to_string()),
                        server_streaming: Some(true),
                        ..Default::default()
                    },
                ],
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut pool = DescriptorPool::new();
        pool.add_file_descriptor_proto(file)
            .expect("valid file descriptor");
        pool
    }

    fn msg(pool: &DescriptorPool, name: &str) -> MessageDescriptor {
        pool.get_message_by_name(name).expect("message present")
    }

    /// Encode a dynamic request message from a JSON object (reuses DynamicMethod's encoder via
    /// a temporary round-trip through the output-encoding path).
    fn encode_message(desc: &MessageDescriptor, json: serde_json::Value) -> Bytes {
        // Build the message by setting each field by name (the int64 millivolts case).
        let mut m = DynamicMessage::new(desc.clone());
        if let serde_json::Value::Object(fields) = json {
            for (name, value) in fields {
                if let Some(field) = desc.get_field_by_name(&name) {
                    // Only the scalar shapes used in this test (int64) are needed.
                    if let Some(i) = value.as_i64() {
                        m.set_field(&field, prost_reflect::Value::I64(i));
                    }
                }
            }
        }
        Bytes::from(m.encode_to_vec())
    }

    // ---- recording mock DriverApi -------------------------------------------------------

    struct MockDriver {
        calls: Mutex<Vec<(String, String, String)>>, // (uuid, method, args_json)
        result: String,
    }
    impl MockDriver {
        fn new(result: &str) -> Arc<Self> {
            Arc::new(Self {
                calls: Mutex::new(Vec::new()),
                result: result.to_string(),
            })
        }
        fn last(&self) -> (String, String, String) {
            self.calls.lock().unwrap().last().cloned().unwrap()
        }
    }
    #[async_trait::async_trait]
    impl DriverApi for MockDriver {
        async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError> {
            Ok(vec![])
        }
        async fn driver_call(
            &self,
            uuid: String,
            method_name: String,
            args_json: String,
        ) -> Result<String, DriverCallError> {
            self.calls.lock().unwrap().push((uuid, method_name, args_json));
            Ok(self.result.clone())
        }
        async fn streaming_driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            unreachable!()
        }
        async fn open_stream(
            &self,
            _request_json: String,
        ) -> Result<DriverStreamOpen, DriverCallError> {
            unreachable!()
        }
    }

    /// A driver whose streaming seam yields a canned list of JSON results (for server-streaming
    /// native dispatch).
    struct StreamingMockDriver {
        results: Vec<String>,
    }
    struct VecStream {
        items: Mutex<std::collections::VecDeque<String>>,
    }
    #[async_trait::async_trait]
    impl DriverResultStream for VecStream {
        async fn next(&self) -> Result<Option<String>, DriverCallError> {
            Ok(self.items.lock().unwrap().pop_front())
        }
    }
    #[async_trait::async_trait]
    impl DriverApi for StreamingMockDriver {
        async fn describe(&self) -> Result<Vec<DriverNode>, DriverCallError> {
            Ok(vec![])
        }
        async fn driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<String, DriverCallError> {
            unreachable!()
        }
        async fn streaming_driver_call(
            &self,
            _uuid: String,
            _method_name: String,
            _args_json: String,
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            Ok(Arc::new(VecStream {
                items: Mutex::new(self.results.iter().cloned().collect()),
            }))
        }
        async fn open_stream(
            &self,
            _request_json: String,
        ) -> Result<DriverStreamOpen, DriverCallError> {
            unreachable!()
        }
    }

    #[test]
    fn export_name_mapping_is_lower_snake() {
        assert_eq!(export_name_for("On"), "on");
        assert_eq!(export_name_for("SetVoltage"), "set_voltage");
        assert_eq!(export_name_for("Read"), "read");
        assert_eq!(export_name_for("GetCPUInfo"), "get_c_p_u_info");
    }

    /// A pool with a bidi `StreamData` `@exportstream` method (`Connect`) alongside the unary/
    /// server-streaming methods: `byte_stream_export` must recognise only the bidi one (the byte
    /// channel), mapping it to its `@export` name, and decline the typed methods.
    #[test]
    fn byte_stream_export_recognises_only_bidi_stream_data() {
        let file = FileDescriptorProto {
            name: Some("power.proto".to_string()),
            package: Some(PKG.to_string()),
            syntax: Some("proto3".to_string()),
            message_type: vec![
                message("Empty", vec![]),
                message("StreamData", vec![field("payload", 1, Type::Bytes)]),
            ],
            service: vec![ServiceDescriptorProto {
                name: Some("PowerInterface".to_string()),
                method: vec![
                    method(
                        "On",
                        ".jumpstarter.driver.power.v1.Empty",
                        ".jumpstarter.driver.power.v1.Empty",
                    ),
                    MethodDescriptorProto {
                        name: Some("Connect".to_string()),
                        input_type: Some(".jumpstarter.driver.power.v1.StreamData".to_string()),
                        output_type: Some(".jumpstarter.driver.power.v1.StreamData".to_string()),
                        client_streaming: Some(true),
                        server_streaming: Some(true),
                        ..Default::default()
                    },
                ],
                ..Default::default()
            }],
            ..Default::default()
        };
        let mut pool = DescriptorPool::new();
        pool.add_file_descriptor_proto(file).unwrap();
        let backend = DynamicBackend::from_pool(&pool, None, MockDriver::new("null"));

        assert_eq!(
            backend.byte_stream_export("/jumpstarter.driver.power.v1.PowerInterface/Connect"),
            Some("connect")
        );
        // A unary method is not a byte channel.
        assert_eq!(
            backend.byte_stream_export("/jumpstarter.driver.power.v1.PowerInterface/On"),
            None
        );
    }

    #[test]
    fn pool_routes_are_keyed_by_grpc_path() {
        let pool = power_pool();
        let backend = DynamicBackend::from_pool(&pool, None, MockDriver::new("null"));
        assert!(backend
            .methods
            .contains_key("/jumpstarter.driver.power.v1.PowerInterface/SetVoltage"));
        assert!(backend
            .methods
            .contains_key("/jumpstarter.driver.power.v1.PowerInterface/On"));
        assert_eq!(
            backend
                .methods
                .get("/jumpstarter.driver.power.v1.PowerInterface/SetVoltage")
                .unwrap()
                .export_name(),
            "set_voltage"
        );
    }

    /// END-TO-END native dispatch with **zero generated servicer**:
    ///
    /// raw tonic client (BytesCodec) ──/PowerInterface/SetVoltage + uuid header──▶
    ///   Demux (catch-all over InProcessTransport) ──forward_unary──▶
    ///     DynamicBackend ──DynamicMethod::dispatch──▶ mock DriverApi
    ///
    /// Asserts the mock driver was driven with method `set_voltage`, args `[12000]`, the
    /// resolved uuid from the header, and the (empty) response came back.
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn native_call_routed_by_header_dispatches_dynamically() {
        let uuid = "power-uuid-1";
        let pool = power_pool();
        let driver = MockDriver::new("null"); // SetVoltage returns Empty
        let backend: Arc<dyn DriverBackend> =
            Arc::new(DynamicBackend::from_pool(&pool, None, driver.clone()));

        // Stand up the demux server over an in-process transport.
        let transport = InProcessTransport::new();
        let demux = Demux::new(SingleBackend(backend));
        let incoming = transport.incoming();
        let server = tokio::spawn(async move {
            Server::builder()
                .add_routes(demux.into_axum_router().into())
                .serve_with_incoming(incoming)
                .await
        });

        // A raw client (no per-driver types) encodes the SetVoltage request and calls through.
        let channel = connect_channel(&transport).await.expect("dial demux");
        let mut client = tonic::client::Grpc::new(channel);
        client.ready().await.expect("client ready");

        let req_bytes = encode_message(
            &msg(&pool, "jumpstarter.driver.power.v1.SetVoltageRequest"),
            serde_json::json!({ "millivolts": 12000 }),
        );
        let mut request = tonic::Request::new(req_bytes);
        request
            .metadata_mut()
            .insert(DRIVER_UUID_KEY, uuid.parse().unwrap());
        let path = http::uri::PathAndQuery::from_str(
            "/jumpstarter.driver.power.v1.PowerInterface/SetVoltage",
        )
        .unwrap();

        let response = client
            .unary(request, path, BytesCodec)
            .await
            .expect("native SetVoltage call");

        // Empty response message → empty bytes.
        assert!(
            response.into_inner().is_empty(),
            "Empty response must be empty bytes"
        );

        // The mock driver was driven dynamically: uuid from header, method set_voltage, args [12000].
        let (got_uuid, got_method, got_args) = driver.last();
        assert_eq!(got_uuid, uuid);
        assert_eq!(got_method, "set_voltage");
        assert_eq!(got_args, "[12000]");

        server.abort();
    }

    /// END-TO-END native **server-streaming** dispatch with zero generated servicer:
    ///
    /// raw tonic client (BytesCodec, `server_streaming`) ──/PowerInterface/Subscribe + uuid header──▶
    ///   Demux (catch-all over InProcessTransport) ──forward_stream──▶
    ///     DynamicBackend ──DynamicMethod::dispatch_streaming──▶ streaming mock DriverApi
    ///
    /// The mock yields two PowerReading results; the client reads two response messages off the
    /// stream and decodes each — proving the opaque demux frames a server-streaming method correctly.
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn native_server_streaming_routed_by_header_dispatches_dynamically() {
        let uuid = "power-uuid-1";
        let pool = power_pool();
        let driver = Arc::new(StreamingMockDriver {
            results: vec![
                r#"{"voltage":1.0,"current":0.1}"#.to_string(),
                r#"{"voltage":2.0,"current":0.2}"#.to_string(),
            ],
        });
        let backend: Arc<dyn DriverBackend> =
            Arc::new(DynamicBackend::from_pool(&pool, None, driver));

        let transport = InProcessTransport::new();
        let demux = Demux::new(SingleBackend(backend));
        let incoming = transport.incoming();
        let server = tokio::spawn(async move {
            Server::builder()
                .add_routes(demux.into_axum_router().into())
                .serve_with_incoming(incoming)
                .await
        });

        let channel = connect_channel(&transport).await.expect("dial demux");
        let mut client = tonic::client::Grpc::new(channel);
        client.ready().await.expect("client ready");

        // Subscribe(Empty) — empty request body, server-streaming.
        let mut request = tonic::Request::new(Bytes::new());
        request
            .metadata_mut()
            .insert(DRIVER_UUID_KEY, uuid.parse().unwrap());
        let path = http::uri::PathAndQuery::from_str(
            "/jumpstarter.driver.power.v1.PowerInterface/Subscribe",
        )
        .unwrap();

        let response = client
            .server_streaming(request, path, BytesCodec)
            .await
            .expect("native Subscribe call");
        let mut stream = response.into_inner();

        // Read each response message off the stream and decode its PowerReading.
        let mut voltages = Vec::new();
        while let Some(item) = stream.message().await.expect("stream item") {
            let decoded = DynamicMessage::decode(
                msg(&pool, "jumpstarter.driver.power.v1.PowerReading"),
                &item[..],
            )
            .expect("decode PowerReading");
            voltages.push(decoded.get_field_by_name("voltage").unwrap().as_f64().unwrap());
        }
        assert_eq!(voltages, vec![1.0, 2.0]);

        server.abort();
    }

    /// A native call for a path with no dynamic method is declined as UNIMPLEMENTED (the
    /// backend is closed over its descriptor pool).
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn unknown_native_path_is_unimplemented() {
        let pool = power_pool();
        let driver = MockDriver::new("null");
        let backend = DynamicBackend::from_pool(&pool, Some("u".to_string()), driver);
        let err = backend
            .forward_unary(
                "/jumpstarter.driver.power.v1.PowerInterface/DoesNotExist",
                MetadataMap::new(),
                Bytes::new(),
            )
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::Unimplemented);
    }

    /// With no header and no fallback uuid, a call is rejected before dispatch.
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn missing_uuid_and_no_fallback_is_invalid_argument() {
        let pool = power_pool();
        let driver = MockDriver::new("null");
        let backend = DynamicBackend::from_pool(&pool, None, driver);
        let err = backend
            .forward_unary(
                "/jumpstarter.driver.power.v1.PowerInterface/On",
                MetadataMap::new(),
                Bytes::new(),
            )
            .await
            .unwrap_err();
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
    }

    /// The fallback uuid is used when the header is absent.
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn fallback_uuid_used_when_header_absent() {
        let pool = power_pool();
        let driver = MockDriver::new("null");
        let backend = DynamicBackend::from_pool(&pool, Some("fallback-uuid".to_string()), driver.clone());
        backend
            .forward_unary(
                "/jumpstarter.driver.power.v1.PowerInterface/On",
                MetadataMap::new(),
                Bytes::new(),
            )
            .await
            .unwrap();
        let (got_uuid, got_method, got_args) = driver.last();
        assert_eq!(got_uuid, "fallback-uuid");
        assert_eq!(got_method, "on");
        assert_eq!(got_args, "[]");
    }
}
