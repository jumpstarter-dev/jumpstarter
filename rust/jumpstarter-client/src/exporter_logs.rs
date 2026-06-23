//! Client-side exporter-log streaming: stream the exporter's
//! `ExporterService.LogStream` and print each line (driver + hook output) to
//! stderr, interleaved with the shell session (`shell.py:log_stream_async`).
//!
//! Two transports: the direct (`--tls-grpc`) path dials the standalone exporter
//! over TCP; the controller path dials the local Unix-socket transport proxy
//! (`crate::transport`) which bridges to the exporter through the router. In both
//! cases `show_all_logs` mirrors Python: hook logs (before/afterLease) are always
//! shown; driver/system logs only when `--exporter-logs` is set.

use std::time::{Duration, Instant};

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
    let endpoint = Endpoint::try_from("http://localhost")
        .map_err(|e| e.to_string())?
        // Large HTTP/2 flow-control windows so a bulk resource/flash transfer isn't gated to
        // ~64 KiB-in-flight per round-trip (the h2 default); over the client→router→exporter
        // hops that default caps throughput at a few MiB/s regardless of chunk size.
        .initial_stream_window_size(8 * 1024 * 1024)
        .initial_connection_window_size(16 * 1024 * 1024);
    // Connect on the multi-threaded IO runtime so this channel's connection driver runs there
    // (not async-compat's single thread) — see `crate::io_runtime`.
    crate::io_runtime()
        .spawn(async move { endpoint.connect_with_connector(connector).await })
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}

/// Spawn a background task that streams the controller-mode exporter's logs (via the
/// local transport proxy socket) to stderr until the stream ends or the task is
/// aborted. `show_all_logs` gates driver/system logs; hook logs are always shown.
pub fn spawn_controller(socket: String, show_all_logs: bool) -> JoinHandle<()> {
    tokio::spawn(async move {
        // The shell aborts this task at session end, so retry resiliently — not just through a
        // brief mid-session drop (e.g. around afterLease) but through the whole exporter
        // *restart window*. The exporter can be briefly unreachable for several seconds after a
        // restart; the previous fixed 10-attempt (~1 s) cap gave up inside that window, so a
        // lease whose hooks ran after the cap was exhausted lost all of its streamed output.
        //
        // Strict policy: exponential backoff capped at `MAX_BACKOFF`, reset whenever a stream
        // delivers at least one line (progress), and a hard `GIVE_UP_AFTER` budget on a run of
        // *consecutive* failures so a truly-gone exporter can't spin forever.
        const MAX_BACKOFF: Duration = Duration::from_secs(1);
        const GIVE_UP_AFTER: Duration = Duration::from_secs(60);
        let mut backoff = Duration::from_millis(50);
        let mut failing_since: Option<Instant> = None;
        loop {
            if stream_controller_once(&socket, show_all_logs).await {
                // Made progress: reset the backoff and the consecutive-failure budget.
                backoff = Duration::from_millis(50);
                failing_since = None;
                continue;
            }
            let since = *failing_since.get_or_insert_with(Instant::now);
            if since.elapsed() >= GIVE_UP_AFTER {
                tracing::debug!("exporter log stream gave up after 60s of consecutive failures");
                break;
            }
            tokio::time::sleep(backoff).await;
            backoff = (backoff * 2).min(MAX_BACKOFF);
        }
    })
}

/// Connect once and stream until the stream ends. Returns whether at least one log line was
/// received, so the reconnect loop can reset its backoff on progress.
async fn stream_controller_once(socket: &str, show_all_logs: bool) -> bool {
    let channel = match uds_channel(socket.to_string()).await {
        Ok(c) => c,
        Err(e) => {
            tracing::debug!(error = %e, "log stream connect failed");
            return false;
        }
    };
    let mut client = ExporterServiceClient::new(channel);
    let mut stream = match client.log_stream(()).await {
        Ok(s) => s.into_inner(),
        Err(e) => {
            tracing::debug!(error = %e, "log stream rpc failed");
            return false;
        }
    };
    let mut received = false;
    while let Some(item) = stream.next().await {
        match item {
            Ok(resp) => {
                received = true;
                print_log(&resp, show_all_logs);
            }
            Err(_) => break,
        }
    }
    received
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
