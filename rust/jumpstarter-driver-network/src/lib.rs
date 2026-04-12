//! Jumpstarter network driver client.
//!
//! Provides `NetworkClient`, a typed client for the `NetworkInterface` gRPC
//! service that exposes TCP and UDP port-forwarding via native bidi streaming.

use std::net::SocketAddr;
use std::sync::Arc;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, UdpSocket};
use tokio::sync::{mpsc, oneshot};
use tonic::transport::Channel;

use jumpstarter_client::{ExporterSession, UuidInterceptor};

/// Generated protobuf/gRPC types for the NetworkInterface service.
pub mod proto {
    tonic::include_proto!("jumpstarter.interfaces.network.v1");
}

use proto::network_interface_client::NetworkInterfaceClient as GrpcClient;
use proto::StreamData;

type InterceptedChannel = tonic::service::interceptor::InterceptedService<Channel, UuidInterceptor>;

/// A typed network driver client.
///
/// Wraps the native `NetworkInterface` gRPC stub (with UUID routing) and
/// provides port-forwarding helpers using native bidi streaming on `Connect`.
///
/// # Example
///
/// ```no_run
/// # async fn example() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
/// use jumpstarter_client::ExporterSession;
/// use jumpstarter_driver_network::NetworkClient;
///
/// let session = ExporterSession::from_env().await?;
/// let mut network = NetworkClient::new(&session, "network")?;
///
/// let tcp = network.connect_tcp().await?;
/// println!("TCP on {}", tcp.local_addr());
///
/// let udp = network.connect_udp().await?;
/// println!("UDP on {}", udp.local_addr());
/// # Ok(())
/// # }
/// ```
pub struct NetworkClient {
    stub: GrpcClient<InterceptedChannel>,
}

impl NetworkClient {
    /// Create a new client for the named driver instance.
    ///
    /// Looks up the driver UUID from the exporter's device report and
    /// creates a native gRPC stub with UUID metadata routing.
    pub fn new(
        session: &ExporterSession,
        driver_name: &str,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let instance = session
            .report()
            .find_by_name(driver_name)
            .ok_or_else(|| format!("driver '{}' not found in exporter report", driver_name))?;
        let uuid = instance.uuid().to_owned();
        let channel = session.channel().clone();
        let stub = GrpcClient::with_interceptor(channel, UuidInterceptor::new(&uuid));
        Ok(Self { stub })
    }

    /// Access the underlying native gRPC stub for direct RPC calls.
    pub fn stub(&mut self) -> &mut GrpcClient<InterceptedChannel> {
        &mut self.stub
    }

    // -- Port-forwarding via native bidi streaming --

    /// Open a TCP port-forwarding listener for the `Connect` bidi stream.
    ///
    /// Binds a local TCP listener on `127.0.0.1:0`. For each accepted
    /// connection, opens a native `Connect` bidi stream and bridges bytes
    /// bidirectionally between the TCP socket and the gRPC stream.
    pub async fn connect_tcp(
        &self,
    ) -> Result<TcpPortforwardHandle, Box<dyn std::error::Error + Send + Sync>> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let local_addr = listener.local_addr()?;
        let (shutdown_tx, mut shutdown_rx) = oneshot::channel();

        let stub = self.stub.clone();

        tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = &mut shutdown_rx => break,
                    accept = listener.accept() => {
                        match accept {
                            Ok((tcp_stream, _)) => {
                                let mut conn_stub = stub.clone();
                                tokio::spawn(async move {
                                    if let Err(e) = handle_tcp_connection(&mut conn_stub, tcp_stream).await {
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

        Ok(TcpPortforwardHandle {
            local_addr,
            shutdown: Some(shutdown_tx),
        })
    }

    /// Open a UDP port-forwarding socket for the `Connect` bidi stream.
    ///
    /// Binds a local UDP socket on `127.0.0.1:0`. Opens a single native
    /// `Connect` bidi stream and bridges datagrams bidirectionally.
    pub async fn connect_udp(
        &self,
    ) -> Result<UdpPortforwardHandle, Box<dyn std::error::Error + Send + Sync>> {
        let socket = UdpSocket::bind("127.0.0.1:0").await?;
        let local_addr = socket.local_addr()?;
        let (shutdown_tx, mut shutdown_rx) = oneshot::channel();

        let (outbound_tx, outbound_rx) = mpsc::channel::<StreamData>(64);
        let outbound_stream = tokio_stream::wrappers::ReceiverStream::new(outbound_rx);

        let mut stub = self.stub.clone();
        let response = stub.connect(outbound_stream).await?;
        let mut inbound = response.into_inner();

        let socket = Arc::new(socket);
        let recv_socket = socket.clone();

        // UDP recv -> outbound gRPC stream
        tokio::spawn(async move {
            let mut buf = vec![0u8; 65535];
            loop {
                tokio::select! {
                    _ = &mut shutdown_rx => break,
                    result = recv_socket.recv_from(&mut buf) => {
                        match result {
                            Ok((n, _peer)) => {
                                let msg = StreamData {
                                    payload: buf[..n].to_vec(),
                                };
                                if outbound_tx.send(msg).await.is_err() {
                                    break;
                                }
                            }
                            Err(_) => break,
                        }
                    }
                }
            }
        });

        // Inbound gRPC stream -> UDP send
        let send_socket = socket;
        tokio::spawn(async move {
            while let Ok(Some(msg)) = inbound.message().await {
                if send_socket.send(&msg.payload).await.is_err() {
                    break;
                }
            }
        });

        Ok(UdpPortforwardHandle {
            local_addr,
            shutdown: Some(shutdown_tx),
        })
    }
}

/// Bridge a single TCP connection over a native `Connect` bidi stream.
async fn handle_tcp_connection(
    stub: &mut GrpcClient<InterceptedChannel>,
    tcp_stream: tokio::net::TcpStream,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let (outbound_tx, outbound_rx) = mpsc::channel::<StreamData>(64);
    let outbound_stream = tokio_stream::wrappers::ReceiverStream::new(outbound_rx);

    let response = stub.connect(outbound_stream).await?;
    let mut inbound = response.into_inner();

    let (mut tcp_read, mut tcp_write) = tcp_stream.into_split();

    // TCP read -> outbound gRPC stream
    let tx_handle = tokio::spawn(async move {
        let mut buf = vec![0u8; 8192];
        loop {
            match tcp_read.read(&mut buf).await {
                Ok(0) => break,
                Ok(n) => {
                    let msg = StreamData {
                        payload: buf[..n].to_vec(),
                    };
                    if outbound_tx.send(msg).await.is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });

    // Inbound gRPC stream -> TCP write
    let rx_handle = tokio::spawn(async move {
        while let Ok(Some(msg)) = inbound.message().await {
            if tcp_write.write_all(&msg.payload).await.is_err() {
                break;
            }
        }
    });

    tokio::select! {
        _ = tx_handle => {}
        _ = rx_handle => {}
    }

    Ok(())
}

/// Handle to an active TCP port-forwarding session.
///
/// The listener shuts down when this handle is dropped.
pub struct TcpPortforwardHandle {
    local_addr: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
}

impl TcpPortforwardHandle {
    /// The local address the TCP listener is bound to.
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }
}

impl Drop for TcpPortforwardHandle {
    fn drop(&mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
    }
}

/// Handle to an active UDP port-forwarding session.
///
/// The socket shuts down when this handle is dropped.
pub struct UdpPortforwardHandle {
    local_addr: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
}

impl UdpPortforwardHandle {
    /// The local address the UDP socket is bound to.
    pub fn local_addr(&self) -> SocketAddr {
        self.local_addr
    }
}

impl Drop for UdpPortforwardHandle {
    fn drop(&mut self) {
        if let Some(tx) = self.shutdown.take() {
            let _ = tx.send(());
        }
    }
}
