//! Client-side exporter-log streaming: stream the exporter's
//! `ExporterService.LogStream` and print each line (driver + hook output) to
//! stderr, interleaved with the shell session (`shell.py:log_stream_async`).
//!
//! Two transports: the direct (`--tls-grpc`) path dials the standalone exporter
//! over TCP; the controller path dials the local Unix-socket transport proxy
//! (`crate::transport`) which bridges to the exporter through the router. In both
//! cases `show_all_logs` mirrors Python: hook logs (before/afterLease) are always
//! shown; driver/system logs only when `--exporter-logs` is set.

use std::time::Duration;

use hyper_util::rt::TokioIo;
use jumpstarter_protocol::v1::exporter_service_client::ExporterServiceClient;
use jumpstarter_protocol::v1::{LogSource, LogStreamResponse};
use tokio::net::UnixStream;
use tokio::task::JoinHandle;
use tokio_stream::StreamExt as _;
use tonic::metadata::MetadataValue;
use tonic::transport::{Channel, Endpoint};
use tonic::{Request, Status};

const PASSPHRASE_METADATA_KEY: &str = "x-jumpstarter-passphrase";

/// Build a tonic channel to the local transport proxy's Unix socket (controller
/// mode). The proxy bridges `ExporterService` RPCs to the exporter via the router.
/// Public so the FFI client surface (jumpstarter-core) can connect to `JUMPSTARTER_HOST`.
pub async fn uds_channel(socket: String) -> Result<Channel, String> {
    let connector = tower::service_fn(move |_: http::Uri| {
        let socket = socket.clone();
        async move { Ok::<_, std::io::Error>(TokioIo::new(UnixStream::connect(socket).await?)) }
    });
    Endpoint::try_from("http://localhost")
        .map_err(|e| e.to_string())?
        .connect_with_connector(connector)
        .await
        .map_err(|e| e.to_string())
}

/// Spawn a background task that streams the controller-mode exporter's logs (via the
/// local transport proxy socket) to stderr until the stream ends or the task is
/// aborted. `show_all_logs` gates driver/system logs; hook logs are always shown.
pub fn spawn_controller(socket: String, show_all_logs: bool) -> JoinHandle<()> {
    tokio::spawn(async move {
        // Reconnect a few times so a brief mid-session stream drop (e.g. around
        // afterLease) doesn't lose the tail (`core.py:log_stream` reconnect loop).
        let mut attempts = 0;
        while attempts < 10 {
            match uds_channel(socket.clone()).await {
                Ok(channel) => {
                    let mut client = ExporterServiceClient::new(channel);
                    match client.log_stream(()).await {
                        Ok(stream) => {
                            let mut stream = stream.into_inner();
                            while let Some(item) = stream.next().await {
                                match item {
                                    Ok(resp) => print_log(&resp, show_all_logs),
                                    Err(_) => break,
                                }
                            }
                        }
                        Err(e) => tracing::debug!(error = %e, "log stream rpc failed"),
                    }
                }
                Err(e) => tracing::debug!(error = %e, "log stream connect failed"),
            }
            attempts += 1;
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
}

/// Spawn a background task that streams the direct exporter's logs to stderr until
/// the stream ends or the task is aborted. Only the insecure (plaintext) path is
/// implemented (the only one the e2e direct-listener suite uses).
pub fn spawn_direct(address: String, insecure: bool, passphrase: Option<String>) -> JoinHandle<()> {
    tokio::spawn(async move {
        if let Err(e) = stream_direct(&address, insecure, passphrase).await {
            tracing::debug!(error = %e, "exporter log stream ended");
        }
    })
}

async fn stream_direct(
    address: &str,
    insecure: bool,
    passphrase: Option<String>,
) -> Result<(), String> {
    if !insecure {
        // TLS log streaming is not yet ported; the shell still works without logs.
        return Err("exporter-logs over TLS not yet supported".to_string());
    }
    let channel = Endpoint::from_shared(format!("http://{address}"))
        .map_err(|e| e.to_string())?
        .connect()
        .await
        .map_err(|e| e.to_string())?;

    let pass: Option<MetadataValue<_>> = passphrase
        .filter(|p| !p.is_empty())
        .and_then(|p| MetadataValue::try_from(p).ok());
    let mut client =
        ExporterServiceClient::with_interceptor(channel, move |mut req: Request<()>| {
            if let Some(p) = &pass {
                req.metadata_mut()
                    .insert(PASSPHRASE_METADATA_KEY, p.clone());
            }
            Ok::<_, Status>(req)
        });

    let mut stream = client
        .log_stream(())
        .await
        .map_err(|e| e.to_string())?
        .into_inner();
    while let Some(item) = stream.next().await {
        match item {
            // Direct mode only streams when `--exporter-logs` is set, so show all.
            Ok(resp) => print_log(&resp, true),
            Err(_) => break,
        }
    }
    Ok(())
}

/// Prefix the exporter puts on a hook log/status line when a hook failed with
/// `on_failure: warn` (`common/__init__.py:HOOK_WARNING_PREFIX`).
const HOOK_WARNING_PREFIX: &str = "[HOOK_WARNING] ";

fn print_log(resp: &LogStreamResponse, show_all_logs: bool) {
    let source = resp.source.and_then(|s| LogSource::try_from(s).ok());
    let is_hook = matches!(
        source,
        Some(LogSource::BeforeLeaseHook | LogSource::AfterLeaseHook)
    );
    // Mirror `core.py:log_stream`: always show hook logs, gate everything else.
    if !is_hook && !show_all_logs {
        return;
    }
    // A hook warning (`on_failure: warn`) is rendered as `Warning: …` (`shell.py`).
    if let Some(text) = resp.message.strip_prefix(HOOK_WARNING_PREFIX) {
        eprintln!("Warning: {text}");
        return;
    }
    let label = match source {
        Some(LogSource::BeforeLeaseHook) => "beforeLease",
        Some(LogSource::AfterLeaseHook) => "afterLease",
        Some(LogSource::Driver) => "driver",
        _ => "exporter",
    };
    eprintln!("[{label}] {}", resp.message);
}
