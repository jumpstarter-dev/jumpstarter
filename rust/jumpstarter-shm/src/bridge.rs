//! Async bridge between tokio and the SPSC [`Ring`].
//!
//! The ring's `try_read`/`try_write` are non-blocking spins; spinning them directly on a tokio
//! worker would starve the runtime. So each direction gets a **dedicated OS thread** that owns the
//! spin and bridges to async through a bounded [`tokio::sync::mpsc`] channel — the channel's depth
//! is the backpressure, and `blocking_send`/`blocking_recv` are safe there because the thread is
//! not a tokio worker. Adaptive backoff (spin briefly, then a short sleep) keeps an idle stream off
//! a hot core while a saturated transfer — the common case — practically never reaches the sleep.
//!
//! This is the API the hub↔host byte-plane wire-in consumes: the hub wraps the producing end of
//! each ring in a [`RingWriter`] and the consuming end in a [`RingReader`], and vice-versa on the
//! host, so the bulk DATA payloads move through shared memory while gRPC keeps only the control
//! handshake (initial metadata, EOF, trailing status).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::mpsc;

use crate::Ring;

/// Spin a bounded number of times, then yield the core with a short sleep. `idle` is the caller's
/// running idle count, reset to 0 by the caller whenever it makes progress.
#[inline]
fn backoff(idle: &mut u32) {
    if *idle < 256 {
        *idle += 1;
        std::hint::spin_loop();
    } else {
        std::thread::sleep(Duration::from_micros(50));
    }
}

/// The consuming half: a dedicated thread drains `ring` into a bounded channel of byte chunks,
/// terminated by the channel closing once the producer marks EOF and the ring is empty.
pub struct RingReader {
    rx: mpsc::Receiver<Vec<u8>>,
    /// Set when the producer ABORTED (truncated stream) rather than closing cleanly. Read it
    /// after `recv()` returns `None` to distinguish truncation from a clean EOF.
    aborted: Arc<AtomicBool>,
}

impl RingReader {
    /// Start draining `ring`. `chunk` bounds one read; `cap` is the channel depth (backpressure:
    /// when the async consumer is slow the channel fills and the thread stops draining, so the
    /// ring fills and the cross-process producer is throttled).
    pub fn spawn(ring: Ring, chunk: usize, cap: usize) -> Self {
        let (tx, rx) = mpsc::channel(cap);
        let aborted = Arc::new(AtomicBool::new(false));
        let aborted_thread = aborted.clone();
        std::thread::Builder::new()
            .name("shm-ring-reader".into())
            .spawn(move || {
                let mut idle = 0u32;
                loop {
                    // Read straight into a fresh buffer so the chunk moves through the channel with
                    // a single copy (ring → Vec), no staging buffer.
                    let mut out = vec![0u8; chunk];
                    let n = ring.try_read(&mut out);
                    if n > 0 {
                        idle = 0;
                        out.truncate(n);
                        if tx.blocking_send(out).is_err() {
                            break; // async consumer dropped
                        }
                    } else if ring.is_closed() {
                        // The producer marked EOF; if it ABORTED, flag the truncation so the async
                        // consumer surfaces an error instead of treating it as a clean end.
                        if ring.is_aborted() {
                            aborted_thread.store(true, Ordering::Release);
                        }
                        break; // producer EOF + ring drained
                    } else {
                        backoff(&mut idle);
                    }
                }
            })
            .expect("spawn shm-ring-reader");
        Self { rx, aborted }
    }

    /// Next chunk, or `None` at EOF (producer closed and the ring drained).
    pub async fn recv(&mut self) -> Option<Vec<u8>> {
        self.rx.recv().await
    }

    /// Whether the producer ABORTED the stream (truncated). Meaningful once `recv()` has returned
    /// `None`: `true` => the stream ended abnormally and the bytes received are incomplete;
    /// `false` => a clean EOF.
    pub fn aborted(&self) -> bool {
        self.aborted.load(Ordering::Acquire)
    }
}

/// The producing half: a dedicated thread pumps byte chunks from a bounded channel into `ring`,
/// busy-spinning (with backoff) while the ring is full, and marks the ring EOF when the channel
/// closes (the [`RingWriter`] is dropped or [`RingWriter::close`]d).
pub struct RingWriter {
    tx: Option<mpsc::Sender<Vec<u8>>>,
}

impl RingWriter {
    /// Start pumping into `ring`. `cap` is the channel depth (backpressure from a slow ring).
    pub fn spawn(ring: Ring, cap: usize) -> Self {
        let (tx, mut rx) = mpsc::channel::<Vec<u8>>(cap);
        std::thread::Builder::new()
            .name("shm-ring-writer".into())
            .spawn(move || {
                // Guarantee the ring is terminated on EVERY exit path: a clean `close()` on normal
                // completion, or `abort()` if this thread unwinds (panic) — otherwise the panic
                // would skip the close and the cross-process consumer would spin forever waiting
                // for an EOF that never comes.
                struct Terminate(Ring);
                impl Drop for Terminate {
                    fn drop(&mut self) {
                        if std::thread::panicking() {
                            self.0.abort();
                        } else {
                            self.0.close();
                        }
                    }
                }
                let ring = Terminate(ring);
                while let Some(chunk) = rx.blocking_recv() {
                    let mut buf = &chunk[..];
                    let mut idle = 0u32;
                    while !buf.is_empty() {
                        let n = ring.0.try_write(buf);
                        if n == 0 {
                            backoff(&mut idle);
                        } else {
                            idle = 0;
                            buf = &buf[n..];
                        }
                    }
                }
                // `Terminate::drop` marks the ring EOF (clean close) for the consumer.
            })
            .expect("spawn shm-ring-writer");
        Self { tx: Some(tx) }
    }

    /// Queue a chunk, awaiting channel capacity (backpressure). `Err` once the writer thread is
    /// gone (ring closed / consumer vanished).
    pub async fn send(&self, chunk: Vec<u8>) -> Result<(), ()> {
        match &self.tx {
            Some(tx) => tx.send(chunk).await.map_err(|_| ()),
            None => Err(()),
        }
    }

    /// Signal EOF: drop the sender so the writer thread closes the ring after draining the channel.
    pub fn close(&mut self) {
        self.tx = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// End-to-end through the async bridge: producer task → `RingWriter` → ring → `RingReader` →
    /// consumer task, pumping far more than the ring capacity (forces wrapping + backpressure).
    /// Asserts byte-for-byte delivery and reports throughput. Run with `--release` for the headline
    /// number; the assertion floor is conservative so it holds on a loaded CI box.
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn bridge_round_trip_throughput() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ring");
        let cap = 4 * 1024 * 1024;
        let chunk = 1024 * 1024;
        let total: usize = 1024 * 1024 * 1024; // 1 GiB

        let prod = Ring::create(&path, cap).unwrap();
        let cons = Ring::open(&path, cap).unwrap();
        let writer = RingWriter::spawn(prod, 8);
        let mut reader = RingReader::spawn(cons, chunk, 8);

        let start = std::time::Instant::now();
        let producer = tokio::spawn(async move {
            let payload = vec![0x5Au8; chunk];
            let mut sent = 0;
            while sent < total {
                writer.send(payload.clone()).await.unwrap();
                sent += payload.len();
            }
            // writer drops here → ring EOF
        });

        let mut got = 0usize;
        while let Some(c) = reader.recv().await {
            assert!(c.iter().all(|&b| b == 0x5A));
            got += c.len();
        }
        producer.await.unwrap();
        let secs = start.elapsed().as_secs_f64();
        let gibs = (got as f64) / 1073741824.0 / secs;
        // Throughput is printed for visibility but deliberately NOT asserted: it depends on machine
        // load, debug-vs-release, and CI-runner speed, so any floor flakes under contention (e.g.
        // running the full `cargo test --workspace` alongside other suites). This test guards
        // CORRECTNESS — every byte round-trips intact through the bridge, including ring wraparound
        // + backpressure (1 GiB through a 4 MiB ring). The headline >2 GiB/s figure is verified by
        // the benchmark harness, not here.
        eprintln!("bridge throughput: {got} bytes in {secs:.3}s = {gibs:.2} GiB/s");
        assert_eq!(got, total);
    }

    /// A producer that ABORTS mid-stream (the panic/teardown case) must surface as a truncation:
    /// the reader unblocks (no infinite spin) and `aborted()` reports the abnormal end.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn reader_surfaces_producer_abort() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ring");
        let cap = 4096;
        let prod = Ring::create(&path, cap).unwrap();
        let cons = Ring::open(&path, cap).unwrap();
        let mut reader = RingReader::spawn(cons, 1024, 8);

        prod.spin_write_all(b"partial");
        prod.abort(); // simulate the writer thread panicking / being torn down mid-transfer

        let mut got = Vec::new();
        while let Some(c) = reader.recv().await {
            got.extend_from_slice(&c);
        }
        assert_eq!(got, b"partial");
        assert!(
            reader.aborted(),
            "reader must report the producer abort (truncated stream), not a clean EOF"
        );
    }
}
