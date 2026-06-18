//! Client-side driver-call surface.
//!
//! Connects to a `JUMPSTARTER_HOST` transport socket and invokes drivers, mirroring the
//! Python `AsyncDriverClient`'s gRPC calls so the Python driver clients (and `j`) can
//! route through the Rust core (FFI) instead of grpcio + generated stubs. Rust owns the
//! value codec and the wire protocol; args/results cross as plain JSON. This is the
//! consumer mirror of [`crate::foreign::ForeignDriverHost`].

use std::sync::Arc;

use jumpstarter_client::exporter_logs::uds_channel;
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::{
    DriverCallRequest, EndSessionRequest, StreamingDriverCallRequest, StreamingDriverCallResponse,
};
use tokio::sync::Mutex;
use tokio_stream::StreamExt as _;
use tonic::transport::Channel;
use tonic::{Code, Status, Streaming};

use crate::codec;
use crate::error::DriverCallError;

/// Map a wire `tonic::Status` to the driver-call error taxonomy the Python client maps to
/// its exceptions (`NOT_FOUND`→DriverMethodNotImplemented, `INVALID_ARGUMENT`→
/// DriverInvalidArgument, …) — the inverse of the host-side mapping.
fn err_from_status(status: Status) -> DriverCallError {
    let msg = status.message().to_string();
    match status.code() {
        Code::Unimplemented => DriverCallError::Unimplemented(msg),
        Code::InvalidArgument => DriverCallError::InvalidArgument(msg),
        Code::DeadlineExceeded => DriverCallError::DeadlineExceeded(msg),
        Code::NotFound => DriverCallError::NotFound(msg),
        _ => DriverCallError::Unknown(msg),
    }
}

/// A connection to an exporter via its local `JUMPSTARTER_HOST` transport socket.
pub struct ClientSession {
    channel: Channel,
}

impl ClientSession {
    /// Connect to the transport socket the lease holder serves (the `JUMPSTARTER_HOST`
    /// env value `jmp shell`/`jmp run` set).
    pub async fn connect(host: String) -> Result<Self, DriverCallError> {
        let channel = uds_channel(host).await.map_err(DriverCallError::Unknown)?;
        Ok(Self { channel })
    }

    fn exporter(&self) -> ExporterServiceClient<Channel> {
        ExporterServiceClient::new(self.channel.clone())
    }

    /// `GetReport` → a JSON array of the driver tree (uuid/parent/labels/methods), which
    /// the Python client uses to build its client object graph.
    pub async fn get_report(&self) -> Result<String, DriverCallError> {
        let report = self
            .exporter()
            .get_report(())
            .await
            .map_err(err_from_status)?
            .into_inner();
        let nodes: Vec<serde_json::Value> = report
            .reports
            .iter()
            .map(|r| {
                serde_json::json!({
                    "uuid": r.uuid,
                    "parent_uuid": r.parent_uuid,
                    "labels": r.labels,
                    "description": r.description,
                    "methods_description": r.methods_description,
                })
            })
            .collect();
        serde_json::to_string(&nodes).map_err(|e| DriverCallError::Unknown(e.to_string()))
    }

    /// Invoke a unary driver call: `args_json` is a JSON array, returns the JSON result.
    pub async fn driver_call(
        &self,
        uuid: String,
        method: String,
        args_json: String,
    ) -> Result<String, DriverCallError> {
        let args = codec::json_args_to_values(&args_json)?;
        let resp = self
            .exporter()
            .driver_call(DriverCallRequest { uuid, method, args })
            .await
            .map_err(err_from_status)?
            .into_inner();
        Ok(codec::value_result_to_json(&resp.result.unwrap_or_default())?)
    }

    /// Invoke a streaming driver call; results are pulled JSON-at-a-time from the returned
    /// [`ClientResultStream`].
    pub async fn streaming_driver_call(
        &self,
        uuid: String,
        method: String,
        args_json: String,
    ) -> Result<Arc<ClientResultStream>, DriverCallError> {
        let args = codec::json_args_to_values(&args_json)?;
        let stream = self
            .exporter()
            .streaming_driver_call(StreamingDriverCallRequest { uuid, method, args })
            .await
            .map_err(err_from_status)?
            .into_inner();
        Ok(Arc::new(ClientResultStream {
            inner: Mutex::new(stream),
        }))
    }

    /// Signal the exporter to end the session early (runs afterLease).
    pub async fn end_session(&self) -> Result<bool, DriverCallError> {
        let resp = self
            .exporter()
            .end_session(EndSessionRequest {})
            .await
            .map_err(err_from_status)?
            .into_inner();
        Ok(resp.success)
    }
}

/// A streaming-driver-call result stream, pulled JSON-at-a-time.
pub struct ClientResultStream {
    inner: Mutex<Streaming<StreamingDriverCallResponse>>,
}

impl ClientResultStream {
    /// Next JSON result, or `None` at end of stream.
    pub async fn next(&self) -> Result<Option<String>, DriverCallError> {
        let mut stream = self.inner.lock().await;
        match stream.next().await {
            Some(Ok(resp)) => Ok(Some(codec::value_result_to_json(
                &resp.result.unwrap_or_default(),
            )?)),
            Some(Err(status)) => Err(err_from_status(status)),
            None => Ok(None),
        }
    }
}
