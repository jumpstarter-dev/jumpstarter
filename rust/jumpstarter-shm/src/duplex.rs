//! A bidirectional `AsyncRead + AsyncWrite` byte duplex over a *pair* of SPSC rings — the IO that
//! the native-gRPC `Transport` `Shm` variant runs tonic/h2 over. One ring carries this endpoint's
//! writes (`self → peer`), the other its reads (`peer → self`); the peer endpoint mirrors them with
//! the rings swapped. It reuses the hardened ring bridge: the read half polls the
//! [`RingReader`](crate::bridge::RingReader)'s drained chunks (surfacing a producer abort as a
//! `ConnectionReset`, not a clean EOF), and the write half feeds a `PollSender` into the shared
//! ring-writer thread (closing it on shutdown so the peer sees a clean ring EOF).
//!
//! This makes "gRPC over shared memory" fall out of the existing tonic stack: hand a `ShmDuplex` to
//! tonic as the connection IO and h2 multiplexing + flow control work unchanged, with **no loopback
//! socket** — eliminating the local hub↔host and client↔hub hops.

use std::io;
use std::pin::Pin;
use std::task::{Context, Poll};

use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use tokio::sync::mpsc;
use tokio_util::sync::PollSender;

use crate::bridge::{spawn_writer_thread, RingReader};
use crate::Ring;

/// Max bytes handed to the write ring per `poll_write` (bounds one chunk allocation; h2 flow
/// control keeps the channel from growing without bound regardless).
const WRITE_CHUNK: usize = 256 * 1024;

/// A duplex endpoint over two rings (see module docs). `AsyncRead + AsyncWrite`, so tonic runs over
/// it directly. `Unpin` (every field is), so the poll methods recover `&mut Self` cheaply.
pub struct ShmDuplex {
    reader: RingReader,
    /// The current inbound chunk and read offset (for partial reads spanning multiple `poll_read`s).
    chunk: Vec<u8>,
    pos: usize,
    read_done: bool,
    tx: PollSender<Vec<u8>>,
}

impl ShmDuplex {
    /// Build a duplex endpoint: `read_ring` carries the `peer → self` direction (this endpoint
    /// consumes it), `write_ring` the `self → peer` direction (this endpoint produces it). `chunk`
    /// bounds one ring read; `cap` is each bridge channel's depth (backpressure).
    pub fn new(read_ring: Ring, write_ring: Ring, chunk: usize, cap: usize) -> Self {
        let reader = RingReader::spawn(read_ring, chunk, cap);
        let (tx, rx) = mpsc::channel::<Vec<u8>>(cap);
        spawn_writer_thread(write_ring, rx);
        Self {
            reader,
            chunk: Vec::new(),
            pos: 0,
            read_done: false,
            tx: PollSender::new(tx),
        }
    }
}

impl AsyncRead for ShmDuplex {
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let me = self.get_mut();
        loop {
            // Drain any buffered chunk first.
            if me.pos < me.chunk.len() {
                let n = (me.chunk.len() - me.pos).min(buf.remaining());
                buf.put_slice(&me.chunk[me.pos..me.pos + n]);
                me.pos += n;
                return Poll::Ready(Ok(()));
            }
            if me.read_done {
                return Poll::Ready(Ok(())); // EOF: leave `buf` unfilled.
            }
            match me.reader.poll_recv(cx) {
                Poll::Ready(Some(chunk)) => {
                    me.chunk = chunk;
                    me.pos = 0;
                    // Loop to copy it out (skips a possible empty chunk).
                }
                Poll::Ready(None) => {
                    me.read_done = true;
                    if me.reader.aborted() {
                        return Poll::Ready(Err(io::Error::new(
                            io::ErrorKind::ConnectionReset,
                            "shm duplex: peer aborted (truncated stream)",
                        )));
                    }
                    return Poll::Ready(Ok(())); // clean EOF
                }
                Poll::Pending => return Poll::Pending,
            }
        }
    }
}

impl AsyncWrite for ShmDuplex {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        data: &[u8],
    ) -> Poll<io::Result<usize>> {
        if data.is_empty() {
            return Poll::Ready(Ok(0));
        }
        let me = self.get_mut();
        match me.tx.poll_reserve(cx) {
            Poll::Ready(Ok(())) => {
                let n = data.len().min(WRITE_CHUNK);
                me.tx.send_item(data[..n].to_vec()).map_err(|_| {
                    io::Error::new(io::ErrorKind::BrokenPipe, "shm duplex: write half closed")
                })?;
                Poll::Ready(Ok(n))
            }
            Poll::Ready(Err(_)) => Poll::Ready(Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "shm duplex: write half closed",
            ))),
            Poll::Pending => Poll::Pending,
        }
    }

    fn poll_flush(self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        // No app-level buffering beyond the channel + ring; the writer thread drains continuously.
        Poll::Ready(Ok(()))
    }

    fn poll_shutdown(self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        // Close the sender so the writer thread sees the channel close → marks the ring EOF, which
        // the peer reads as a clean end-of-stream.
        self.get_mut().tx.close();
        Poll::Ready(Ok(()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    /// Build two cross-connected `ShmDuplex` endpoints over a fresh ring-pair in `dir`.
    fn connected_pair(dir: &std::path::Path, cap: usize, chunk: usize) -> (ShmDuplex, ShmDuplex) {
        let ab = dir.join("ab"); // A → B
        let ba = dir.join("ba"); // B → A
        let ab_prod = Ring::create(&ab, cap).unwrap();
        let ab_cons = Ring::open(&ab, cap).unwrap();
        let ba_prod = Ring::create(&ba, cap).unwrap();
        let ba_cons = Ring::open(&ba, cap).unwrap();
        let a = ShmDuplex::new(ba_cons, ab_prod, chunk, cap);
        let b = ShmDuplex::new(ab_cons, ba_prod, chunk, cap);
        (a, b)
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn duplex_round_trips_both_directions() {
        let dir = tempfile::tempdir().unwrap();
        let (mut a, mut b) = connected_pair(dir.path(), 64 * 1024, 16 * 1024);

        // A → B
        a.write_all(b"hello from A").await.unwrap();
        a.flush().await.unwrap();
        let mut buf = [0u8; 12];
        b.read_exact(&mut buf).await.unwrap();
        assert_eq!(&buf, b"hello from A");

        // B → A
        b.write_all(b"hi from B!!!").await.unwrap();
        b.flush().await.unwrap();
        let mut buf2 = [0u8; 12];
        a.read_exact(&mut buf2).await.unwrap();
        assert_eq!(&buf2, b"hi from B!!!");
    }

    /// A large transfer that wraps the ring many times exercises chunking + backpressure + partial
    /// reads, and shutdown delivers a clean EOF (`read_to_end` returns, not a `ConnectionReset`).
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn duplex_large_transfer_then_clean_eof() {
        let dir = tempfile::tempdir().unwrap();
        let (mut a, mut b) = connected_pair(dir.path(), 64 * 1024, 16 * 1024);

        let total = 8 * 1024 * 1024usize; // 8 MiB through a 64 KiB ring
        let producer = tokio::spawn(async move {
            let block = vec![0x5Au8; 64 * 1024];
            let mut sent = 0;
            while sent < total {
                a.write_all(&block).await.unwrap();
                sent += block.len();
            }
            a.shutdown().await.unwrap(); // clean EOF
        });

        let mut got = Vec::new();
        b.read_to_end(&mut got).await.unwrap();
        producer.await.unwrap();
        assert_eq!(got.len(), total);
        assert!(got.iter().all(|&x| x == 0x5A));
    }
}
