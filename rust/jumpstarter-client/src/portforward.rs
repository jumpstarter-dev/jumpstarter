//! TCP and UDP port-forwarding adapters for `@exportstream` driver methods.
//!
//! Creates a local TCP or UDP listener and forwards traffic
//! through a `StreamChannel` to the remote driver.

use std::net::SocketAddr;
use std::sync::Arc;

use bytes::Bytes;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, UdpSocket};
use tokio::sync::oneshot;
use tonic::metadata::MetadataMap;

use crate::session::ExporterSession;
use crate::stream_channel::StreamChannel;

/// A local TCP listener that forwards connections to a remote driver stream.
///
/// Each accepted TCP connection opens a new `StreamChannel` and bidirectionally
/// copies bytes between the TCP socket and the gRPC stream.
///
/// # Example
///
/// ```no_run
/// # async fn example() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
/// use jumpstarter_client::ExporterSession;
/// use jumpstarter_client::portforward::TcpPortforwardAdapter;
///
/// let session = ExporterSession::from_env().await?;
/// let adapter = TcpPortforwardAdapter::open(&session, "driver-uuid", "connect").await?;
/// println!("Listening on {}", adapter.local_addr());
/// // adapter is dropped when no longer needed, which shuts down the listener
/// # Ok(())
/// # }
/// ```
pub struct TcpPortforwardAdapter {
    local_addr: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
}

impl TcpPortforwardAdapter {
    /// Start a TCP listener on `127.0.0.1:0` that forwards connections to the
    /// given driver method via `RouterService.Stream`.
    pub async fn open(
        session: &ExporterSession,
        driver_uuid: &str,
        method: &str,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        Self::open_on_channel(session.channel().clone(), driver_uuid, method).await
    }

    /// Start a TCP listener using a raw gRPC channel.
    pub async fn open_on_channel(
        channel: tonic::transport::Channel,
        driver_uuid: &str,
        method: &str,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let local_addr = listener.local_addr()?;
        let (shutdown_tx, mut shutdown_rx) = oneshot::channel();

        let driver_uuid = driver_uuid.to_owned();
        let method = method.to_owned();

        tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = &mut shutdown_rx => break,
                    accept = listener.accept() => {
                        match accept {
                            Ok((tcp_stream, _)) => {
                                let ch = channel.clone();
                                let uuid = driver_uuid.clone();
                                let m = method.clone();
                                tokio::spawn(async move {
                                    if let Err(e) = handle_connection(ch, &uuid, &m, tcp_stream).await {
                                        eprintln!("portforward connection error: {e}");
                                    }
                                });
                            }
                            Err(e) => {
                                eprintln!("portforward accept error: {e}");
                                break;
                            }
                        }
                    }
                }
            }
        });

        Ok(Self {
            local_addr,
            shutdown: Some(shutdown_tx),
        })
    }

    /// The local address the listener is bound to.
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }
}

impl Drop for TcpPortforwardAdapter {
    fn drop(&mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
    }
}

/// Build the gRPC metadata that the router uses to route a driver stream.
///
/// The Python client sends a `request` metadata key containing a JSON object:
/// `{"kind": "driver", "uuid": "<uuid>", "method": "<method>"}`.
fn build_stream_metadata(
    driver_uuid: &str,
    method: &str,
) -> Result<MetadataMap, Box<dyn std::error::Error + Send + Sync>> {
    let mut metadata = MetadataMap::new();
    let request_json = format!(
        r#"{{"kind":"driver","uuid":"{}","method":"{}"}}"#,
        driver_uuid, method
    );
    metadata.insert("request", request_json.parse()?);
    Ok(metadata)
}

/// Handle a single TCP connection by bridging it to a StreamChannel.
async fn handle_connection(
    channel: tonic::transport::Channel,
    driver_uuid: &str,
    method: &str,
    tcp_stream: tokio::net::TcpStream,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let metadata = build_stream_metadata(driver_uuid, method)?;
    let (stream_tx, mut stream_rx) = StreamChannel::open_with_metadata(channel, metadata).await?;

    let (mut tcp_read, mut tcp_write) = tcp_stream.into_split();

    // TCP → StreamChannel
    let tx_handle = tokio::spawn(async move {
        let mut buf = vec![0u8; 8192];
        loop {
            match tcp_read.read(&mut buf).await {
                Ok(0) => break,
                Ok(n) => {
                    if stream_tx.send(Bytes::copy_from_slice(&buf[..n])).await.is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });

    // StreamChannel → TCP
    let rx_handle = tokio::spawn(async move {
        while let Some(data) = stream_rx.recv().await {
            if tcp_write.write_all(&data).await.is_err() {
                break;
            }
        }
    });

    // Wait for either direction to finish, then cancel the other
    tokio::select! {
        _ = tx_handle => {}
        _ = rx_handle => {}
    }

    Ok(())
}

/// A local UDP socket that forwards datagrams to a remote driver stream.
///
/// Binds a `UdpSocket` to `127.0.0.1:0` and opens a single `StreamChannel`.
/// Each incoming datagram is sent as one StreamChannel message, and each
/// received StreamChannel message is sent back as a datagram to the last
/// known peer.
///
/// # Example
///
/// ```no_run
/// # async fn example() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
/// use jumpstarter_client::ExporterSession;
/// use jumpstarter_client::portforward::UdpPortforwardAdapter;
///
/// let session = ExporterSession::from_env().await?;
/// let adapter = UdpPortforwardAdapter::open(&session, "driver-uuid", "connect").await?;
/// println!("UDP listening on {}", adapter.local_addr());
/// # Ok(())
/// # }
/// ```
pub struct UdpPortforwardAdapter {
    local_addr: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
}

impl UdpPortforwardAdapter {
    /// Start a UDP socket on `127.0.0.1:0` that forwards datagrams to the
    /// given driver method via `RouterService.Stream`.
    pub async fn open(
        session: &ExporterSession,
        driver_uuid: &str,
        method: &str,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        Self::open_on_channel(session.channel().clone(), driver_uuid, method).await
    }

    /// Start a UDP socket using a raw gRPC channel.
    pub async fn open_on_channel(
        channel: tonic::transport::Channel,
        driver_uuid: &str,
        method: &str,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let socket = UdpSocket::bind("127.0.0.1:0").await?;
        let local_addr = socket.local_addr()?;
        let (shutdown_tx, mut shutdown_rx) = oneshot::channel();

        let metadata = build_stream_metadata(driver_uuid, method)?;
        let (stream_tx, mut stream_rx) =
            StreamChannel::open_with_metadata(channel, metadata).await?;

        let socket = Arc::new(socket);
        let recv_socket = socket.clone();

        // UDP recv → StreamChannel send
        tokio::spawn(async move {
            let mut buf = vec![0u8; 65535];
            loop {
                tokio::select! {
                    _ = &mut shutdown_rx => break,
                    result = recv_socket.recv_from(&mut buf) => {
                        match result {
                            Ok((n, _peer)) => {
                                if stream_tx
                                    .send(Bytes::copy_from_slice(&buf[..n]))
                                    .await
                                    .is_err()
                                {
                                    break;
                                }
                            }
                            Err(_) => break,
                        }
                    }
                }
            }
        });

        // StreamChannel recv → UDP send back to last peer
        let send_socket = socket;
        tokio::spawn(async move {
            while let Some(data) = stream_rx.recv().await {
                if send_socket.send(&data).await.is_err() {
                    break;
                }
            }
        });

        Ok(Self {
            local_addr,
            shutdown: Some(shutdown_tx),
        })
    }

    /// The local address the UDP socket is bound to.
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }
}

impl Drop for UdpPortforwardAdapter {
    fn drop(&mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
    }
}
