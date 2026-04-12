//! Jumpstarter network driver client.
//!
//! Provides `NetworkClient`, a typed client for the `NetworkInterface` gRPC
//! service that also exposes TCP and UDP port-forwarding via `@exportstream`.

use std::net::SocketAddr;

use tonic::transport::Channel;

use jumpstarter_client::portforward::{TcpPortforwardAdapter, UdpPortforwardAdapter};
use jumpstarter_client::{ExporterSession, UuidInterceptor};

/// Generated protobuf/gRPC types for the NetworkInterface service.
pub mod proto {
    tonic::include_proto!("jumpstarter.interfaces.network.v1");
}

use proto::network_interface_client::NetworkInterfaceClient as GrpcClient;

type InterceptedChannel = tonic::service::interceptor::InterceptedService<Channel, UuidInterceptor>;

/// A typed network driver client.
///
/// Wraps the native `NetworkInterface` gRPC stub (with UUID routing) and
/// provides port-forwarding helpers for the `Connect` stream method.
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
/// // Port-forwarding (for @exportstream Connect method)
/// let tcp = network.connect_tcp(None).await?;
/// println!("TCP on {}", tcp.local_addr());
///
/// let udp = network.connect_udp(None).await?;
/// println!("UDP on {}", udp.local_addr());
/// # Ok(())
/// # }
/// ```
pub struct NetworkClient {
    stub: GrpcClient<InterceptedChannel>,
    channel: Channel,
    driver_uuid: String,
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
        let stub = GrpcClient::with_interceptor(channel.clone(), UuidInterceptor::new(&uuid));
        Ok(Self {
            stub,
            channel,
            driver_uuid: uuid,
        })
    }

    /// Access the underlying native gRPC stub for direct RPC calls.
    ///
    /// This is useful for calling any future typed methods added to the
    /// `NetworkInterface` proto definition.
    pub fn stub(&mut self) -> &mut GrpcClient<InterceptedChannel> {
        &mut self.stub
    }

    // -- Port-forwarding for @exportstream methods --

    /// Open a TCP port-forwarding adapter for the `Connect` stream.
    ///
    /// Returns a handle whose [`local_addr`](TcpPortforwardHandle::local_addr)
    /// gives the local TCP listener address. Defaults to method `"connect"`.
    pub async fn connect_tcp(
        &self,
        method: Option<&str>,
    ) -> Result<TcpPortforwardHandle, Box<dyn std::error::Error + Send + Sync>> {
        let adapter = TcpPortforwardAdapter::open_on_channel(
            self.channel.clone(),
            &self.driver_uuid,
            method.unwrap_or("connect"),
        )
        .await?;
        Ok(TcpPortforwardHandle { adapter })
    }

    /// Open a UDP port-forwarding adapter for the `Connect` stream.
    ///
    /// Returns a handle whose [`local_addr`](UdpPortforwardHandle::local_addr)
    /// gives the local UDP socket address. Defaults to method `"connect"`.
    pub async fn connect_udp(
        &self,
        method: Option<&str>,
    ) -> Result<UdpPortforwardHandle, Box<dyn std::error::Error + Send + Sync>> {
        let adapter = UdpPortforwardAdapter::open_on_channel(
            self.channel.clone(),
            &self.driver_uuid,
            method.unwrap_or("connect"),
        )
        .await?;
        Ok(UdpPortforwardHandle { adapter })
    }
}

/// Handle to an active TCP port-forwarding session.
///
/// The listener shuts down when this handle is dropped.
pub struct TcpPortforwardHandle {
    adapter: TcpPortforwardAdapter,
}

impl TcpPortforwardHandle {
    /// The local address the TCP listener is bound to.
    pub fn local_addr(&self) -> SocketAddr {
        self.adapter.local_addr()
    }
}

/// Handle to an active UDP port-forwarding session.
///
/// The socket shuts down when this handle is dropped.
pub struct UdpPortforwardHandle {
    adapter: UdpPortforwardAdapter,
}

impl UdpPortforwardHandle {
    /// The local address the UDP socket is bound to.
    pub fn local_addr(&self) -> SocketAddr {
        self.adapter.local_addr()
    }
}
