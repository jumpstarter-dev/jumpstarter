//! On-demand **dynamic** gRPC service dispatch — serve a driver's native
//! per-interface gRPC service straight from its protobuf descriptor, with **no**
//! per-language/per-driver generated servicer.
//!
//! A driver exposes a native gRPC interface (e.g. `PowerInterface`) whose request
//! and response messages are described only by a [`prost_reflect::MethodDescriptor`].
//! Rather than generating a typed servicer per interface (the prototype's
//! `power/v1/servicer.py`), this module decodes the opaque request bytes against the
//! method's *input* descriptor, dispatches through the existing binding-agnostic
//! [`DriverApi`] seam (`driver_call(uuid, method, args_json)`), then encodes the JSON
//! result against the method's *output* descriptor and reserializes to bytes. The
//! exporter can therefore serve any interface dynamically from the descriptor it
//! already has, without a generated adapter on either side.
//!
//! ## The mapping (mirrors the prototype servicer)
//!
//! A generated servicer maps a request message's fields, **in declared order**, onto
//! the positional args of the `@export` method, and maps the method's return onto the
//! response message:
//!
//! - `On(Empty)` → args `[]`            → `Empty` response → empty bytes.
//! - `SetVoltage(SetVoltageRequest{millivolts})` → args `[millivolts]` → `Empty`.
//! - `Read(Empty)` → args `[]` → result `{voltage, current}` → `PowerReading` message.
//!
//! The request-field → arg and result → response-field conversions go through the
//! same JSON value model the [`crate::codec`]/[`jumpstarter_protocol::value`] codec
//! uses for `DriverCall`, so the JSON crossing the [`DriverApi`] seam is byte-for-byte
//! the shape a Python driver already produces/consumes (notably: numbers are JSON
//! numbers; the `int`→`f64` wire quirk only applies to the `google.protobuf.Value`
//! codec, not here — a proto `int64` field stays an integer in the args JSON).
//!
//! ## What is handled vs. deferred
//!
//! Handled: scalars (bool/int/uint/float/string/bytes-as-utf8), nested messages,
//! repeated fields, maps, enums (by number), `google.protobuf.Value`/`Struct`-as-JSON,
//! `Empty`/`None` results, and the single-field-wrap shape (a bare scalar result wraps
//! into a one-field output message). See the module-end note for the cases that need
//! care when this is wired to real drivers.

use std::sync::Arc;

use bytes::Bytes;
use jumpstarter_transport::ResponseStream;
use prost::Message as _;
use prost_reflect::{
    DynamicMessage, Kind, MapKey, MessageDescriptor, MethodDescriptor, ReflectMessage as _, Value,
};
use serde_json::Value as Json;
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::Status;

use crate::error::DriverCallError;
use crate::host::DriverApi;

/// A method's dispatch shape: its input/output message descriptors plus the driver
/// `@export` name the request is dispatched to.
///
/// Built once per interface method (the exporter holds these alongside the descriptor
/// pool); `dispatch` is then a pure decode → call → encode over the raw bytes.
#[derive(Clone)]
pub struct DynamicMethod {
    /// The `@export` method name on the driver (the prototype's lower_snake mapping,
    /// e.g. proto `SetVoltage` → `set_voltage`). Supplied by the caller's interface map;
    /// this module does not invent it.
    export_name: String,
    input: MessageDescriptor,
    output: MessageDescriptor,
    /// Whether the proto method is server-streaming (its `@export` is an async generator). A
    /// server-streaming method dispatches through [`dispatch_streaming`](Self::dispatch_streaming)
    /// (one output message per yielded result); a unary method through [`dispatch`](Self::dispatch).
    server_streaming: bool,
    /// Whether the proto method is client-streaming. A method that is **both** client- and
    /// server-streaming is a bidi byte channel (`@exportstream` carries `StreamData` both ways);
    /// see [`is_byte_stream`](Self::is_byte_stream). Such a method is served by the host's byte-plane
    /// pump (`forward_bidi`), not the typed dispatch here.
    client_streaming: bool,
}

impl DynamicMethod {
    /// Build from a resolved [`MethodDescriptor`] and the driver `@export` name it maps to.
    pub fn from_descriptor(method: &MethodDescriptor, export_name: impl Into<String>) -> Self {
        Self {
            export_name: export_name.into(),
            input: method.input(),
            output: method.output(),
            server_streaming: method.is_server_streaming(),
            client_streaming: method.is_client_streaming(),
        }
    }

    /// Build directly from input/output descriptors (e.g. tests, or a caller that
    /// resolved the two messages itself). Defaults to unary; use [`new_streaming`](Self::new_streaming)
    /// for a server-streaming method.
    pub fn new(
        input: MessageDescriptor,
        output: MessageDescriptor,
        export_name: impl Into<String>,
    ) -> Self {
        Self {
            export_name: export_name.into(),
            input,
            output,
            server_streaming: false,
            client_streaming: false,
        }
    }

    /// Like [`new`](Self::new) but for a server-streaming method.
    pub fn new_streaming(
        input: MessageDescriptor,
        output: MessageDescriptor,
        export_name: impl Into<String>,
    ) -> Self {
        Self {
            export_name: export_name.into(),
            input,
            output,
            server_streaming: true,
            client_streaming: false,
        }
    }

    /// The driver `@export` name this method dispatches to.
    pub fn export_name(&self) -> &str {
        &self.export_name
    }

    /// Whether this method is server-streaming (selects [`dispatch_streaming`](Self::dispatch_streaming)).
    pub fn is_server_streaming(&self) -> bool {
        self.server_streaming
    }

    /// Whether this method is a **bidi byte channel** — both client- and server-streaming, the shape
    /// the descriptor builder emits for an `@exportstream` (`StreamData` in and out). The host serves
    /// these through the byte-plane pump (`forward_bidi` → `open_stream`), not the typed dispatch.
    pub fn is_byte_stream(&self) -> bool {
        self.client_streaming && self.server_streaming
    }

    /// Decode `request_bytes` against the input descriptor, dispatch through the driver
    /// seam, and encode the result against the output descriptor — returning the wire
    /// bytes of the native response message.
    pub async fn dispatch(
        &self,
        uuid: &str,
        request_bytes: &[u8],
        driver_api: &dyn DriverApi,
    ) -> Result<Vec<u8>, DriverCallError> {
        // 1. Decode the opaque request bytes against the method's input descriptor.
        let request = DynamicMessage::decode(self.input.clone(), request_bytes)
            .map_err(|e| DriverCallError::InvalidArgument(format!("decode request: {e}")))?;

        // 2. Request fields, in declared order, become the positional args array.
        let args = request_to_args(&request, &self.input);
        let args_json = serde_json::to_string(&Json::Array(args))
            .map_err(|e| DriverCallError::Unknown(format!("encode args: {e}")))?;

        // 3. Dispatch through the existing dynamic driver seam.
        let result_json = driver_api
            .driver_call(uuid.to_string(), self.export_name.clone(), args_json)
            .await?;

        // 4. Encode the JSON result into the output message and serialize to bytes.
        encode_result(&result_json, &self.output)
    }

    /// The **server-streaming** analogue of [`dispatch`](Self::dispatch): decode the request into
    /// positional args, open the driver's result stream via
    /// [`DriverApi::streaming_driver_call`](crate::host::DriverApi::streaming_driver_call), and
    /// return a [`ResponseStream`] that encodes each yielded JSON result into one output message —
    /// the wire shape of a server-streaming gRPC method. A pump task pulls from the driver stream
    /// and pushes encoded message bytes into the returned stream (mirroring `ForeignDriver`'s
    /// legacy streaming pump), so the stream lives independently of this call.
    pub async fn dispatch_streaming(
        &self,
        uuid: &str,
        request_bytes: &[u8],
        driver_api: Arc<dyn DriverApi>,
    ) -> Result<ResponseStream<Bytes>, DriverCallError> {
        // Decode the request → positional args (synchronously, so a bad request fails the open).
        let request = DynamicMessage::decode(self.input.clone(), request_bytes)
            .map_err(|e| DriverCallError::InvalidArgument(format!("decode request: {e}")))?;
        let args = request_to_args(&request, &self.input);
        let args_json = serde_json::to_string(&Json::Array(args))
            .map_err(|e| DriverCallError::Unknown(format!("encode args: {e}")))?;

        let export_name = self.export_name.clone();
        let output = self.output.clone();
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
        Ok(Box::pin(ReceiverStream::new(rx)))
    }
}

/// Encode a driver-call JSON result string into the wire bytes of `output` — the encode tail
/// shared by the unary [`dispatch`](DynamicMethod::dispatch) and per-item streaming dispatch.
fn encode_result(result_json: &str, output: &MessageDescriptor) -> Result<Vec<u8>, DriverCallError> {
    let result: Json = serde_json::from_str(result_json)
        .map_err(|e| DriverCallError::Unknown(format!("decode result json: {e}")))?;
    let response = result_to_message(&result, output)?;
    Ok(response.encode_to_vec())
}

/// Map a decoded request message's fields onto the positional args list of the
/// `@export` method — fields in **declared order** (`fields()` yields by declaration),
/// mirroring how the prototype servicer forwards `request.field` positionally.
///
/// An absent (proto3-default) field still contributes its default so arg positions stay
/// aligned with the method signature.
fn request_to_args(message: &DynamicMessage, input: &MessageDescriptor) -> Vec<Json> {
    input
        .fields()
        .map(|field| value_to_json(message.get_field(&field).as_ref()))
        .collect()
}

/// Encode a driver-call JSON result into a [`DynamicMessage`] of `output`.
///
/// Shapes handled (mirroring the prototype, where `Empty`/`None` → empty message and a
/// `BaseModel` return → its fields by name):
/// - `null`/`Empty` output → an empty message.
/// - object → set each output field by name (missing fields stay default; extra keys ignored).
/// - bare scalar/array, and the output has exactly one field → wrap it into that field
///   (the `SetVoltage`-style single-field response, and the symmetric single-return case).
fn result_to_message(
    result: &Json,
    output: &MessageDescriptor,
) -> Result<DynamicMessage, DriverCallError> {
    let mut message = DynamicMessage::new(output.clone());

    match result {
        // Empty/None → empty message (covers `Empty` outputs and void returns).
        Json::Null => {}
        Json::Object(fields) => {
            for (name, value) in fields {
                if let Some(field) = output.get_field_by_name(name) {
                    let v = json_to_value(value, &field.kind(), field.is_list(), field.is_map())?;
                    message.set_field(&field, v);
                }
                // Unknown keys are ignored (a driver may return extra metadata).
            }
        }
        // A bare scalar/array result: wrap into the sole field if the message has one.
        other => {
            let mut fields = output.fields();
            match (fields.next(), fields.next()) {
                (Some(field), None) => {
                    let v = json_to_value(other, &field.kind(), field.is_list(), field.is_map())?;
                    message.set_field(&field, v);
                }
                (None, _) => {
                    // Non-null result but an empty output message: nothing to set. This is a
                    // benign mismatch (e.g. a driver returning a value for an `Empty`-typed rpc).
                }
                (Some(_), Some(_)) => {
                    return Err(DriverCallError::Unknown(format!(
                        "cannot map scalar result into multi-field message {}",
                        output.full_name()
                    )));
                }
            }
        }
    }

    Ok(message)
}

// ---- client side: the INVERSE of `DynamicMethod::dispatch` --------------------------
//
// `DynamicMethod` (server) does: request bytes → args JSON → driver_call → result JSON →
// response bytes. The client does the mirror: args JSON → request bytes (here), then
// `native_unary`, then response bytes → result JSON (here). The two share the exact same
// field↔args + result↔message logic so a value round-trips identically across the seam.

/// Encode a positional JSON args array into the wire bytes of `input` — the client-side
/// inverse of [`request_to_args`].
///
/// The args array maps onto `input`'s fields **in declared order** (`args[0]` → first
/// field, `args[1]` → second, …), exactly mirroring how the server reads request fields
/// back out positionally. Extra args (beyond the message's field count) are ignored —
/// e.g. a method whose request is `Empty` ignores all args — and a `null` arg leaves the
/// corresponding field at its proto3 default (so the server reads the default back).
pub fn encode_request(
    input: &MessageDescriptor,
    args_json: &str,
) -> Result<Vec<u8>, DriverCallError> {
    let parsed: Json = serde_json::from_str(args_json)
        .map_err(|e| DriverCallError::Unknown(format!("decode args json: {e}")))?;
    let args = match parsed {
        Json::Array(items) => items,
        // A non-array crossing is treated as a single positional arg (defensive, matching
        // `codec::json_args_to_values`).
        other => vec![other],
    };

    let mut message = DynamicMessage::new(input.clone());
    for (field, arg) in input.fields().zip(args.iter()) {
        // `null` → leave the field at its default (proto3 has no explicit null on the wire).
        if arg.is_null() {
            continue;
        }
        let v = json_to_value(arg, &field.kind(), field.is_list(), field.is_map())?;
        message.set_field(&field, v);
    }
    Ok(message.encode_to_vec())
}

/// Decode the wire bytes of an `output` message into the JSON result string the Python
/// client expects — the client-side inverse of [`result_to_message`].
///
/// The single-field-unwrap mirrors how the server *wraps*: a one-field output message
/// decodes back to that field's bare value (so a `set_voltage`-style single-return
/// round-trips as a scalar), an empty message decodes to `null` (the `Empty`/void return),
/// and a multi-field message decodes to a JSON object keyed by field name (a
/// `PowerReading`-style struct return). This is what `call_async` then `json.loads`es and
/// hands back to the driver client.
pub fn decode_response(output: &MessageDescriptor, bytes: &[u8]) -> Result<String, DriverCallError> {
    let message = DynamicMessage::decode(output.clone(), bytes)
        .map_err(|e| DriverCallError::Unknown(format!("decode response: {e}")))?;

    let mut fields = output.fields();
    let json = match (fields.next(), fields.next()) {
        // Empty message (Empty / void return) → null.
        (None, _) => Json::Null,
        // Single field → unwrap to its bare value (inverse of the scalar-wrap).
        (Some(field), None) => value_to_json(message.get_field(&field).as_ref()),
        // Multiple fields → an object keyed by field name (a struct/BaseModel return).
        (Some(_), Some(_)) => message_to_json(&message),
    };
    serde_json::to_string(&json).map_err(|e| DriverCallError::Unknown(format!("encode result json: {e}")))
}

// ---- dynamic Value <-> JSON ------------------------------------------------------
//
// These mirror `jumpstarter_protocol::value` semantics where the models overlap, but
// operate on `prost_reflect::Value` (a *typed* dynamic value) rather than
// `google.protobuf.Value`. The key difference: a proto `int64` field is a real integer
// here, so it stays a JSON integer (no int->f64 collapse). The `Value`/`Struct`
// well-known types still go through the JSON-shaped path.

/// Convert a typed dynamic field value into JSON for the args array / nested encoding.
fn value_to_json(value: &Value) -> Json {
    match value {
        Value::Bool(b) => Json::Bool(*b),
        Value::I32(n) => Json::Number((*n).into()),
        Value::I64(n) => Json::Number((*n).into()),
        Value::U32(n) => Json::Number((*n).into()),
        Value::U64(n) => Json::Number((*n).into()),
        Value::F32(n) => json_from_f64(*n as f64),
        Value::F64(n) => json_from_f64(*n),
        Value::String(s) => Json::String(s.clone()),
        // Python's JSON-mode dump decodes bytes as UTF-8 into a string; do the same so
        // the args JSON matches (lossy fallback keeps us panic-free on non-UTF-8).
        Value::Bytes(b) => Json::String(String::from_utf8_lossy(b).into_owned()),
        Value::EnumNumber(n) => Json::Number((*n).into()),
        Value::Message(m) => message_to_json(m),
        Value::List(items) => Json::Array(items.iter().map(value_to_json).collect()),
        Value::Map(entries) => Json::Object(
            entries
                .iter()
                .map(|(k, v)| (map_key_to_string(k), value_to_json(v)))
                .collect(),
        ),
    }
}

/// Convert a nested dynamic message to a JSON object (field name → JSON), in declared order.
fn message_to_json(message: &DynamicMessage) -> Json {
    Json::Object(
        message
            .descriptor()
            .fields()
            .map(|field| {
                (
                    field.name().to_string(),
                    value_to_json(message.get_field(&field).as_ref()),
                )
            })
            .collect(),
    )
}

/// Convert a JSON value into a typed dynamic value for a field of the given kind.
///
/// `is_list`/`is_map` drive the repeated/map shapes; otherwise the scalar/message path
/// is taken. Conversions are lenient where the driver JSON is float-y (e.g. a `42.0`
/// JSON number into an `int64` field) — matching how the `google.protobuf.Value` codec
/// would have produced a float that a typed field must still accept.
fn json_to_value(
    json: &Json,
    kind: &Kind,
    is_list: bool,
    is_map: bool,
) -> Result<Value, DriverCallError> {
    if is_map {
        // Maps are encoded as a JSON object; the proto map-entry's value kind is `kind`.
        let Json::Object(obj) = json else {
            return Err(DriverCallError::Unknown(
                "expected JSON object for map field".to_string(),
            ));
        };
        let mut map = std::collections::HashMap::new();
        for (k, v) in obj {
            // Map keys are always strings in JSON; proto map keys are scalar — we only
            // support string keys here (the common case for driver maps). Other key
            // types would need the map-entry key kind; deferred.
            map.insert(MapKey::String(k.clone()), json_to_value(v, kind, false, false)?);
        }
        return Ok(Value::Map(map));
    }

    if is_list {
        let Json::Array(items) = json else {
            return Err(DriverCallError::Unknown(
                "expected JSON array for repeated field".to_string(),
            ));
        };
        let mut out = Vec::with_capacity(items.len());
        for item in items {
            out.push(json_scalar_to_value(item, kind)?);
        }
        return Ok(Value::List(out));
    }

    json_scalar_to_value(json, kind)
}

/// Convert one JSON scalar (or nested object/array for message/Value kinds) into a
/// typed dynamic value of `kind`.
fn json_scalar_to_value(json: &Json, kind: &Kind) -> Result<Value, DriverCallError> {
    let type_err = |want: &str| {
        DriverCallError::Unknown(format!("result JSON {json} not convertible to proto {want}"))
    };

    Ok(match kind {
        Kind::Bool => Value::Bool(json.as_bool().ok_or_else(|| type_err("bool"))?),
        Kind::Int32 | Kind::Sint32 | Kind::Sfixed32 => {
            Value::I32(json_as_i64(json).ok_or_else(|| type_err("int32"))? as i32)
        }
        Kind::Int64 | Kind::Sint64 | Kind::Sfixed64 => {
            Value::I64(json_as_i64(json).ok_or_else(|| type_err("int64"))?)
        }
        Kind::Uint32 | Kind::Fixed32 => {
            Value::U32(json_as_u64(json).ok_or_else(|| type_err("uint32"))? as u32)
        }
        Kind::Uint64 | Kind::Fixed64 => {
            Value::U64(json_as_u64(json).ok_or_else(|| type_err("uint64"))?)
        }
        Kind::Float => Value::F32(json.as_f64().ok_or_else(|| type_err("float"))? as f32),
        Kind::Double => Value::F64(json.as_f64().ok_or_else(|| type_err("double"))?),
        Kind::String => Value::String(json.as_str().ok_or_else(|| type_err("string"))?.to_string()),
        Kind::Bytes => Value::Bytes(
            // Symmetric with the decode path: a JSON string becomes its UTF-8 bytes.
            json.as_str()
                .ok_or_else(|| type_err("bytes"))?
                .as_bytes()
                .to_vec()
                .into(),
        ),
        Kind::Enum(_) => Value::EnumNumber(json_as_i64(json).ok_or_else(|| type_err("enum"))? as i32),
        Kind::Message(desc) => Value::Message(result_to_message(json, desc)?),
    })
}

/// Coerce a JSON number to `i64`, accepting an integer-valued float (`12000.0` → `12000`)
/// since the `google.protobuf.Value` codec would have floated an integer on the way in.
fn json_as_i64(json: &Json) -> Option<i64> {
    if let Some(i) = json.as_i64() {
        return Some(i);
    }
    json.as_f64().and_then(|f| {
        if f.fract() == 0.0 && f.is_finite() {
            Some(f as i64)
        } else {
            None
        }
    })
}

/// Coerce a JSON number to `u64`, accepting an integer-valued float.
fn json_as_u64(json: &Json) -> Option<u64> {
    if let Some(u) = json.as_u64() {
        return Some(u);
    }
    json.as_f64().and_then(|f| {
        if f.fract() == 0.0 && f.is_finite() && f >= 0.0 {
            Some(f as u64)
        } else {
            None
        }
    })
}

/// Map an `f64` to a JSON number, falling back to null for non-finite values — the same
/// rule as [`jumpstarter_protocol::value`] (NaN/±Inf are not JSON-representable).
fn json_from_f64(n: f64) -> Json {
    serde_json::Number::from_f64(n)
        .map(Json::Number)
        .unwrap_or(Json::Null)
}

/// Stringify a proto map key for the JSON object key (JSON object keys are strings).
fn map_key_to_string(key: &MapKey) -> String {
    match key {
        MapKey::Bool(b) => b.to_string(),
        MapKey::I32(n) => n.to_string(),
        MapKey::I64(n) => n.to_string(),
        MapKey::U32(n) => n.to_string(),
        MapKey::U64(n) => n.to_string(),
        MapKey::String(s) => s.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dto::DriverNode;
    use crate::host::{DriverResultStream, DriverStreamOpen};
    use prost_reflect::prost_types::{
        field_descriptor_proto::{Label, Type},
        DescriptorProto, FieldDescriptorProto, FileDescriptorProto, MethodDescriptorProto,
        ServiceDescriptorProto,
    };
    use prost_reflect::DescriptorPool;
    use std::sync::Arc;
    use std::sync::Mutex;

    // ---- hand-built descriptor pool (no .proto / protoc) ---------------------------

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

    /// Build a pool describing:
    ///   message Empty {}
    ///   message SetVoltageRequest { int64 millivolts = 1; }
    ///   message PowerReading { double voltage = 1; double current = 2; }
    ///   service PowerInterface {
    ///     rpc On(Empty) returns (Empty);
    ///     rpc SetVoltage(SetVoltageRequest) returns (Empty);
    ///     rpc Read(Empty) returns (PowerReading);
    ///   }
    fn power_pool() -> DescriptorPool {
        let file = FileDescriptorProto {
            name: Some("power.proto".to_string()),
            package: Some("power.v1".to_string()),
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
                    method("On", ".power.v1.Empty", ".power.v1.Empty"),
                    method("SetVoltage", ".power.v1.SetVoltageRequest", ".power.v1.Empty"),
                    method("Read", ".power.v1.Empty", ".power.v1.PowerReading"),
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

    fn method_desc(pool: &DescriptorPool, rpc: &str) -> MethodDescriptor {
        pool.get_service_by_name("power.v1.PowerInterface")
            .expect("service present")
            .methods()
            .find(|m| m.name() == rpc)
            .expect("method present")
    }

    fn msg(pool: &DescriptorPool, name: &str) -> MessageDescriptor {
        pool.get_message_by_name(name).expect("message present")
    }

    // ---- a recording mock DriverApi ------------------------------------------------

    struct MockDriver {
        /// Records every (method, args_json) the dispatcher sent.
        calls: Mutex<Vec<(String, String)>>,
        /// Canned JSON result returned from every `driver_call`.
        result: String,
    }

    impl MockDriver {
        fn new(result: &str) -> Self {
            Self {
                calls: Mutex::new(Vec::new()),
                result: result.to_string(),
            }
        }
        fn last_call(&self) -> (String, String) {
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
            _uuid: String,
            method_name: String,
            args_json: String,
        ) -> Result<String, DriverCallError> {
            self.calls.lock().unwrap().push((method_name, args_json));
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

    /// Encode a dynamic message of `desc` from a JSON object, for building request bytes.
    fn encode_request(desc: &MessageDescriptor, json: Json) -> Vec<u8> {
        let m = result_to_message(&json, desc).expect("build request message");
        m.encode_to_vec()
    }

    #[tokio::test]
    async fn on_empty_request_dispatches_empty_args_and_empty_response() {
        let pool = power_pool();
        let m = DynamicMethod::from_descriptor(&method_desc(&pool, "On"), "on");
        let driver = MockDriver::new("null"); // void return

        // Empty request bytes.
        let request_bytes = encode_request(&msg(&pool, "power.v1.Empty"), Json::Null);
        let response = m.dispatch("u1", &request_bytes, &driver).await.unwrap();

        // Args were empty.
        let (method, args) = driver.last_call();
        assert_eq!(method, "on");
        assert_eq!(args, "[]");
        // Empty response → empty bytes.
        assert!(response.is_empty(), "Empty response must be empty bytes");
    }

    #[tokio::test]
    async fn set_voltage_request_decodes_to_positional_int_arg() {
        let pool = power_pool();
        let m = DynamicMethod::from_descriptor(&method_desc(&pool, "SetVoltage"), "set_voltage");
        let driver = MockDriver::new("null");

        let request_bytes = encode_request(
            &msg(&pool, "power.v1.SetVoltageRequest"),
            serde_json::json!({ "millivolts": 12000 }),
        );
        let response = m.dispatch("u1", &request_bytes, &driver).await.unwrap();

        let (method, args) = driver.last_call();
        assert_eq!(method, "set_voltage");
        // A proto int64 field stays an integer in the args JSON (no int->f64 collapse).
        assert_eq!(args, "[12000]");
        assert!(response.is_empty());
    }

    #[tokio::test]
    async fn read_encodes_result_object_into_power_reading_message() {
        let pool = power_pool();
        let m = DynamicMethod::from_descriptor(&method_desc(&pool, "Read"), "read");
        let driver = MockDriver::new(r#"{"voltage":5.0,"current":2.0}"#);

        let request_bytes = encode_request(&msg(&pool, "power.v1.Empty"), Json::Null);
        let response = m.dispatch("u1", &request_bytes, &driver).await.unwrap();

        // Empty args in, object result out.
        let (method, args) = driver.last_call();
        assert_eq!(method, "read");
        assert_eq!(args, "[]");

        // Decode the response bytes back into a PowerReading and check the fields.
        let decoded = DynamicMessage::decode(msg(&pool, "power.v1.PowerReading"), &response[..])
            .expect("decode PowerReading");
        let voltage = decoded
            .get_field_by_name("voltage")
            .unwrap()
            .as_f64()
            .unwrap();
        let current = decoded
            .get_field_by_name("current")
            .unwrap()
            .as_f64()
            .unwrap();
        assert_eq!(voltage, 5.0);
        assert_eq!(current, 2.0);
    }

    #[tokio::test]
    async fn scalar_result_wraps_into_single_field_message() {
        // A bare scalar result wraps into a one-field output message (the symmetric
        // single-return shape). Reuse SetVoltageRequest (one int64 field) as the output.
        let pool = power_pool();
        let out = msg(&pool, "power.v1.SetVoltageRequest");
        let m = DynamicMethod::new(msg(&pool, "power.v1.Empty"), out.clone(), "count");
        let driver = MockDriver::new("7"); // bare scalar return

        let request_bytes = encode_request(&msg(&pool, "power.v1.Empty"), Json::Null);
        let response = m.dispatch("u1", &request_bytes, &driver).await.unwrap();

        let decoded = DynamicMessage::decode(out, &response[..]).unwrap();
        assert_eq!(
            decoded
                .get_field_by_name("millivolts")
                .unwrap()
                .as_i64()
                .unwrap(),
            7
        );
    }

    #[test]
    fn float_valued_int_result_coerces_into_int64_field() {
        // The google.protobuf.Value codec floats integers; a `12000.0` result must still
        // land in an int64 field.
        let pool = power_pool();
        let out = msg(&pool, "power.v1.SetVoltageRequest");
        let m = result_to_message(&serde_json::json!({ "millivolts": 12000.0 }), &out).unwrap();
        assert_eq!(
            m.get_field_by_name("millivolts").unwrap().as_i64().unwrap(),
            12000
        );
    }

    #[test]
    fn request_to_args_preserves_declared_field_order() {
        let pool = power_pool();
        let reading = msg(&pool, "power.v1.PowerReading");
        let m = result_to_message(
            &serde_json::json!({ "current": 2.0, "voltage": 5.0 }),
            &reading,
        )
        .unwrap();
        // Declared order is voltage(1), current(2) regardless of JSON key order.
        let args = request_to_args(&m, &reading);
        assert_eq!(args, vec![serde_json::json!(5.0), serde_json::json!(2.0)]);
    }

    // ---- client-side encode_request / decode_response (the inverse) ------------------

    #[test]
    fn client_encode_request_positional_args_to_input_message() {
        // `set_voltage(12000)` → args `[12000]` → input message {millivolts: 12000} bytes
        // that the server decodes back to the same positional args.
        let pool = power_pool();
        let input = msg(&pool, "power.v1.SetVoltageRequest");
        let bytes = super::encode_request(&input, "[12000]").unwrap();

        // Server side reads it back positionally → [12000].
        let decoded = DynamicMessage::decode(input.clone(), &bytes[..]).unwrap();
        let args = request_to_args(&decoded, &input);
        assert_eq!(args, vec![serde_json::json!(12000)]);
    }

    #[test]
    fn client_encode_empty_request_ignores_extra_args() {
        // `on()` → args `[]` → Empty bytes; and extra args beyond the field count are dropped.
        let pool = power_pool();
        let empty = msg(&pool, "power.v1.Empty");
        assert!(super::encode_request(&empty, "[]").unwrap().is_empty());
        assert!(super::encode_request(&empty, "[1, 2, 3]").unwrap().is_empty());
    }

    #[test]
    fn client_decode_response_unwraps_empty_single_and_multi() {
        let pool = power_pool();
        // Empty message → null (void return).
        let empty = msg(&pool, "power.v1.Empty");
        assert_eq!(super::decode_response(&empty, &[]).unwrap(), "null");

        // Single-field message → bare scalar (the wrap inverse).
        let one = msg(&pool, "power.v1.SetVoltageRequest");
        let one_bytes = super::encode_request(&one, "[42]").unwrap();
        assert_eq!(super::decode_response(&one, &one_bytes).unwrap(), "42");

        // Multi-field message → object keyed by field name.
        let reading = msg(&pool, "power.v1.PowerReading");
        let reading_msg =
            result_to_message(&serde_json::json!({ "voltage": 5.0, "current": 2.0 }), &reading)
                .unwrap();
        let reading_bytes = reading_msg.encode_to_vec();
        let json: Json =
            serde_json::from_str(&super::decode_response(&reading, &reading_bytes).unwrap())
                .unwrap();
        assert_eq!(json, serde_json::json!({ "voltage": 5.0, "current": 2.0 }));
    }

    /// A mock whose `streaming_driver_call` yields a canned list of JSON results, recording the
    /// `(method, args_json)` it was opened with.
    struct StreamingMock {
        opened: Mutex<Option<(String, String)>>,
        results: Vec<String>,
    }
    impl StreamingMock {
        fn new(results: &[&str]) -> Arc<Self> {
            Arc::new(Self {
                opened: Mutex::new(None),
                results: results.iter().map(|s| s.to_string()).collect(),
            })
        }
    }
    struct VecResultStream {
        items: Mutex<std::collections::VecDeque<String>>,
    }
    #[async_trait::async_trait]
    impl DriverResultStream for VecResultStream {
        async fn next(&self) -> Result<Option<String>, DriverCallError> {
            Ok(self.items.lock().unwrap().pop_front())
        }
    }
    #[async_trait::async_trait]
    impl DriverApi for StreamingMock {
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
            method_name: String,
            args_json: String,
        ) -> Result<Arc<dyn DriverResultStream>, DriverCallError> {
            *self.opened.lock().unwrap() = Some((method_name, args_json));
            Ok(Arc::new(VecResultStream {
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

    #[tokio::test]
    async fn dispatch_streaming_yields_one_output_message_per_result() {
        use tokio_stream::StreamExt as _;
        let pool = power_pool();
        // Read(Empty) -> PowerReading, served as server-streaming (an async-generator @export).
        let m = DynamicMethod::new_streaming(
            msg(&pool, "power.v1.Empty"),
            msg(&pool, "power.v1.PowerReading"),
            "read",
        );
        let driver = StreamingMock::new(&[
            r#"{"voltage":1.0,"current":0.1}"#,
            r#"{"voltage":2.0,"current":0.2}"#,
            r#"{"voltage":3.0,"current":0.3}"#,
        ]);

        let request_bytes = encode_request(&msg(&pool, "power.v1.Empty"), Json::Null);
        let mut stream = m
            .dispatch_streaming("u1", &request_bytes, driver.clone())
            .await
            .unwrap();

        // Each yielded result is encoded into one PowerReading message; decode them back.
        let mut voltages = Vec::new();
        while let Some(item) = stream.next().await {
            let bytes = item.unwrap();
            let decoded =
                DynamicMessage::decode(msg(&pool, "power.v1.PowerReading"), &bytes[..]).unwrap();
            voltages.push(decoded.get_field_by_name("voltage").unwrap().as_f64().unwrap());
        }
        assert_eq!(voltages, vec![1.0, 2.0, 3.0]);

        // The driver's streaming seam was opened with the @export name + empty args.
        assert_eq!(
            *driver.opened.lock().unwrap(),
            Some(("read".to_string(), "[]".to_string()))
        );
    }

    #[tokio::test]
    async fn client_server_round_trip_set_voltage() {
        // Full inverse loop WITHOUT the network: client encode_request → server dispatch →
        // client decode_response, asserting the driver saw [12000] and the void return decodes
        // back to null.
        let pool = power_pool();
        let method = method_desc(&pool, "SetVoltage");
        let dynm = DynamicMethod::from_descriptor(&method, "set_voltage");
        let driver = MockDriver::new("null");

        // Client: args JSON → request bytes.
        let body = super::encode_request(&method.input(), "[12000]").unwrap();
        // Server: dispatch → response bytes.
        let resp = dynm.dispatch("u1", &body, &driver).await.unwrap();
        // Client: response bytes → result JSON.
        let result = super::decode_response(&method.output(), &resp).unwrap();

        let (m, args) = driver.last_call();
        assert_eq!(m, "set_voltage");
        assert_eq!(args, "[12000]");
        assert_eq!(result, "null");
    }
}
