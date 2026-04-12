//! Stream channel for `@exportstream` methods via `RouterService.Stream`.

use bytes::Bytes;
use tokio::sync::mpsc;
use tonic::Streaming;

use crate::proto::jumpstarter::v1::router_service_client::RouterServiceClient;
use crate::proto::jumpstarter::v1::{FrameType, StreamRequest, StreamResponse};

/// A bidirectional byte stream backed by `RouterService.Stream`.
///
/// Returns a `(Sender, Receiver)` pair for writing and reading bytes.
///
/// # Example
///
/// ```no_run
/// # async fn example() -> Result<(), Box<dyn std::error::Error>> {
/// use jumpstarter_client::StreamChannel;
/// use tonic::transport::Channel;
///
/// let channel: Channel = todo!("get channel");
/// let (tx, mut rx) = StreamChannel::open(channel).await?;
///
/// tx.send(bytes::Bytes::from("hello")).await?;
/// if let Some(data) = rx.recv().await {
///     println!("received: {:?}", data);
/// }
/// # Ok(())
/// # }
/// ```
pub struct StreamChannel;

impl StreamChannel {
    /// Open a new stream channel on the given gRPC channel.
    ///
    /// Returns `(sender, receiver)` — write bytes to the sender, read from the receiver.
    pub async fn open(
        channel: tonic::transport::Channel,
    ) -> Result<
        (mpsc::Sender<Bytes>, mpsc::Receiver<Bytes>),
        Box<dyn std::error::Error + Send + Sync>,
    > {
        let mut client = RouterServiceClient::new(channel);

        let (outbound_tx, mut outbound_rx) = mpsc::channel::<Bytes>(64);
        let (inbound_tx, inbound_rx) = mpsc::channel::<Bytes>(64);

        // Build the outbound stream from the sender
        let outbound_stream = async_stream::stream! {
            while let Some(data) = outbound_rx.recv().await {
                yield StreamRequest {
                    payload: data.to_vec(),
                    frame_type: FrameType::Data as i32,
                };
            }
        };

        let response = client.stream(outbound_stream).await?;
        let mut inbound: Streaming<StreamResponse> = response.into_inner();

        // Spawn a task to forward inbound messages to the receiver
        tokio::spawn(async move {
            while let Ok(Some(msg)) = inbound.message().await {
                if msg.frame_type == FrameType::Data as i32 {
                    if inbound_tx.send(Bytes::from(msg.payload)).await.is_err() {
                        break;
                    }
                }
            }
        });

        Ok((outbound_tx, inbound_rx))
    }
}
