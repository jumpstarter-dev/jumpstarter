//! Exporter session management.
//!
//! Reads `JUMPSTARTER_HOST` from the environment (set by `jmp shell`),
//! creates a gRPC channel, and calls `GetReport()` for driver discovery.

use std::env;

use hyper_util::rt::TokioIo;
use tonic::transport::{Channel, Endpoint, Uri};
use tower::service_fn;

use crate::proto::jumpstarter::v1::exporter_service_client::ExporterServiceClient;
use crate::report::DriverReport;

const ENV_HOST: &str = "JUMPSTARTER_HOST";

/// A session connected to a Jumpstarter exporter inside a `jmp shell`.
///
/// # Example
///
/// ```no_run
/// # async fn example() -> Result<(), Box<dyn std::error::Error>> {
/// use jumpstarter_client::ExporterSession;
///
/// let session = ExporterSession::from_env().await?;
/// let report = session.report();
/// let power = report.find_by_name("power").expect("power driver not found");
/// println!("Power driver UUID: {}", power.uuid());
/// # Ok(())
/// # }
/// ```
#[derive(Debug)]
pub struct ExporterSession {
    channel: Channel,
    report: DriverReport,
}

impl ExporterSession {
    /// Connect to an exporter using the `JUMPSTARTER_HOST` environment variable.
    ///
    /// Supports both Unix domain sockets (default for `jmp shell`) and TCP addresses.
    pub async fn from_env() -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let host = env::var(ENV_HOST).map_err(|_| {
            format!(
                "{ENV_HOST} environment variable is not set. \
                 Are you running inside a 'jmp shell' session?"
            )
        })?;
        Self::connect(&host).await
    }

    /// Connect to an exporter at the given address.
    ///
    /// The address can be a Unix socket path or a `host:port` TCP address.
    pub async fn connect(addr: &str) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let channel = if is_tcp_address(addr) {
            let uri: Uri = format!("http://{addr}").parse()?;
            Channel::builder(uri).connect().await?
        } else {
            // Unix domain socket
            let socket_path = addr.to_owned();
            Endpoint::try_from("http://[::]:50051")?
                .connect_with_connector(service_fn(move |_: Uri| {
                    let path = socket_path.clone();
                    async move {
                        let stream = tokio::net::UnixStream::connect(path).await?;
                        Ok::<_, std::io::Error>(TokioIo::new(stream))
                    }
                }))
                .await?
        };

        let mut client = ExporterServiceClient::new(channel.clone());
        let response = client.get_report(()).await?;
        let report = DriverReport::from_response(response.into_inner());

        Ok(Self { channel, report })
    }

    /// Get the cached driver report.
    pub fn report(&self) -> &DriverReport {
        &self.report
    }

    /// Get the underlying gRPC channel for creating native stubs.
    pub fn channel(&self) -> &Channel {
        &self.channel
    }
}

fn is_tcp_address(addr: &str) -> bool {
    if addr.starts_with('/') {
        return false;
    }
    match addr.rfind(':') {
        Some(pos) if pos > 0 => addr[pos + 1..].parse::<u16>().is_ok(),
        _ => false,
    }
}
