//! A single-producer / single-consumer (SPSC) shared-memory **byte ring** for the local
//! hub↔host byte plane (resource/flash transfers between the polyglot hub and a per-driver
//! host subprocess on the same machine).
//!
//! The control plane (stream open/close, metadata) stays on the existing gRPC/UDS seam; only
//! the *bulk bytes* move through this ring, which is a lock-free `AtomicU64` head/tail over a
//! shared `mmap` of a file. Portable POSIX (`mmap`/`ftruncate`) — measured at ~40 GiB/s on both
//! macOS (arm64) and Linux (aarch64), i.e. memory-bandwidth-bound — so the byte transport stops
//! being the bottleneck. (A framework like iceoryx2 would be ~80 transitive crates for what is a
//! 1:1 pipe; this is one file + `libc`.)
//!
//! One `Ring` is unidirectional. A duplex channel uses two rings (e.g. two files, or two regions
//! of one file). `try_write`/`try_read` are **non-blocking** and return the byte count moved, so
//! the async layer above bridges to tokio without busy-spinning inside the crate; a `spin_*`
//! helper is provided for throughput tests. The producer marks EOF with [`Ring::close`].
//!
//! # Safety / invariants
//! - Exactly one producer and one consumer (SPSC). Two producers or two consumers is UB.
//! - `cap` MUST match between the producer's [`Ring::create`] and the consumer's [`Ring::open`].
//! - `head`/`tail` are monotonic byte counters (never wrapped); the data index is `pos % cap`.

use std::fs::{File, OpenOptions};
use std::io;
use std::os::unix::io::AsRawFd;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(feature = "bridge")]
pub mod bridge;
#[cfg(feature = "bridge")]
pub mod duplex;

/// Wire contract for the local shared-memory byte plane, shared by the client (`jumpstarter-core`),
/// the hub, and the host (`jumpstarter-exporter`) so all three agree without a cross-crate dep.
///
/// A producer sets [`SHM_UP_KEY`] in a `RouterService.Stream`'s request metadata to the path of a
/// ring it created (capacity [`RING_CAP`]); the consumer at the next hop opens it and reads in
/// [`RING_CHUNK`] units. The metadata is regenerated per hop (client→hub, then hub→host), so the
/// rings never collide.
pub mod wire {
    /// Request-metadata key carrying the uplink ring's file path (producer → next-hop consumer).
    pub const SHM_UP_KEY: &str = "x-jmp-shm-up";
    /// Response-metadata key carrying the downlink ring's file path (leaf/host → client). The leaf
    /// creates it and writes the driver's output there; intermediaries relay the key; the client
    /// reads it directly (direct mode is single-machine). EOF is the ring's close flag; the gRPC
    /// stream still carries the trailing `ABORTED "aclose"` status.
    pub const SHM_DOWN_KEY: &str = "x-jmp-shm-down";
    /// Ring capacity in data bytes; MUST match on both ends. 8 MiB ≫ one [`RING_CHUNK`] so the
    /// producer rarely stalls on a full ring.
    pub const RING_CAP: usize = 8 * 1024 * 1024;
    /// Consumer read chunk — one DATA frame per ring read.
    pub const RING_CHUNK: usize = 1024 * 1024;
}

/// Header is three cache lines: `head` (consumer), `tail` (producer), `flags` — each on its own
/// line to avoid false sharing between the two cores/processes.
const HEAD_OFF: usize = 0;
const TAIL_OFF: usize = 64;
const FLAGS_OFF: usize = 128;
const HDR: usize = 192;

const FLAG_CLOSED: u64 = 1; // producer signalled end-of-stream
const FLAG_ABORTED: u64 = 2; // producer ended abnormally (panic/teardown) — stream is truncated

/// A unidirectional SPSC shared-memory byte ring backed by an `mmap`'d file.
pub struct Ring {
    base: *mut u8,
    cap: usize,
    mapsz: usize,
    _file: File, // keep the fd alive for the mapping's lifetime
}

// SAFETY: the ring is designed for exactly one producer and one consumer, which may live in
// different threads/processes; all shared state is accessed through atomics with acquire/release
// ordering. The raw pointer is into a shared mapping that outlives the `Ring`.
unsafe impl Send for Ring {}
unsafe impl Sync for Ring {}

impl Ring {
    /// Producer side: create/truncate `path` to hold a ring of `cap` data bytes and zero the
    /// header. The consumer opens the same path with the same `cap`.
    pub fn create(path: &Path, cap: usize) -> io::Result<Self> {
        let file = OpenOptions::new().read(true).write(true).create(true).truncate(false).open(path)?;
        let mapsz = HDR + cap;
        file.set_len(mapsz as u64)?;
        let ring = unsafe { Self::map(file, cap, mapsz)? };
        // Zero the header (fresh ring). Data does not need zeroing — it's only read after written.
        ring.head().store(0, Ordering::Relaxed);
        ring.tail().store(0, Ordering::Relaxed);
        ring.flags().store(0, Ordering::Relaxed);
        Ok(ring)
    }

    /// Consumer side: `mmap` an existing ring file created with the same `cap`.
    pub fn open(path: &Path, cap: usize) -> io::Result<Self> {
        let file = OpenOptions::new().read(true).write(true).open(path)?;
        let mapsz = HDR + cap;
        unsafe { Self::map(file, cap, mapsz) }
    }

    unsafe fn map(file: File, cap: usize, mapsz: usize) -> io::Result<Self> {
        let base = libc::mmap(
            std::ptr::null_mut(),
            mapsz,
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_SHARED,
            file.as_raw_fd(),
            0,
        );
        if base == libc::MAP_FAILED {
            return Err(io::Error::last_os_error());
        }
        Ok(Self { base: base as *mut u8, cap, mapsz, _file: file })
    }

    #[inline]
    fn head(&self) -> &AtomicU64 {
        unsafe { &*(self.base.add(HEAD_OFF) as *const AtomicU64) }
    }
    #[inline]
    fn tail(&self) -> &AtomicU64 {
        unsafe { &*(self.base.add(TAIL_OFF) as *const AtomicU64) }
    }
    #[inline]
    fn flags(&self) -> &AtomicU64 {
        unsafe { &*(self.base.add(FLAGS_OFF) as *const AtomicU64) }
    }
    #[inline]
    fn data(&self) -> *mut u8 {
        unsafe { self.base.add(HDR) }
    }

    /// Bytes currently buffered (producer view of what's unconsumed). Clamped against the
    /// untrusted shared header: a peer process could corrupt head/tail, so a `tail < head`
    /// inconsistency must not report a bogus ~`u64::MAX` length (which would drive OOB elsewhere).
    pub fn len(&self) -> usize {
        let t = self.tail().load(Ordering::Acquire);
        let h = self.head().load(Ordering::Acquire);
        t.wrapping_sub(h).min(self.cap as u64) as usize
    }
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
    pub fn capacity(&self) -> usize {
        self.cap
    }

    /// Producer: copy as many bytes from `buf` as currently fit; returns how many were written
    /// (0 if the ring is full). Non-blocking.
    pub fn try_write(&self, buf: &[u8]) -> usize {
        let t = self.tail().load(Ordering::Relaxed);
        let h = self.head().load(Ordering::Acquire);
        // The header is shared with another process and therefore untrusted: use `wrapping_sub`
        // and refuse to write if `used` exceeds capacity (an impossible state from corruption or
        // a broken SPSC invariant) rather than underflow `free` and run the copy past the mapping.
        let used = t.wrapping_sub(h);
        if used > self.cap as u64 {
            return 0;
        }
        let free = self.cap - used as usize;
        let n = buf.len().min(free);
        if n == 0 {
            return 0;
        }
        let off = (t as usize) % self.cap;
        let n1 = n.min(self.cap - off);
        unsafe {
            std::ptr::copy_nonoverlapping(buf.as_ptr(), self.data().add(off), n1);
            if n1 < n {
                std::ptr::copy_nonoverlapping(buf.as_ptr().add(n1), self.data(), n - n1);
            }
        }
        self.tail().store(t + n as u64, Ordering::Release);
        n
    }

    /// Consumer: copy up to `out.len()` available bytes into `out`; returns how many (0 if empty).
    /// Non-blocking.
    pub fn try_read(&self, out: &mut [u8]) -> usize {
        let h = self.head().load(Ordering::Relaxed);
        let t = self.tail().load(Ordering::Acquire);
        // Untrusted shared header (see `try_write`): clamp `avail` to capacity so a corrupted or
        // racing tail can't drive an out-of-bounds read.
        let avail = t.wrapping_sub(h).min(self.cap as u64) as usize;
        let n = out.len().min(avail);
        if n == 0 {
            return 0;
        }
        let off = (h as usize) % self.cap;
        let n1 = n.min(self.cap - off);
        unsafe {
            std::ptr::copy_nonoverlapping(self.data().add(off), out.as_mut_ptr(), n1);
            if n1 < n {
                std::ptr::copy_nonoverlapping(self.data(), out.as_mut_ptr().add(n1), n - n1);
            }
        }
        self.head().store(h + n as u64, Ordering::Release);
        n
    }

    /// Producer: mark end-of-stream. The consumer sees this via [`Ring::is_closed`] once it has
    /// drained all bytes written before the close.
    pub fn close(&self) {
        self.flags().fetch_or(FLAG_CLOSED, Ordering::Release);
    }

    /// Consumer: true once the producer has closed AND all produced bytes have been read.
    pub fn is_closed(&self) -> bool {
        self.flags().load(Ordering::Acquire) & FLAG_CLOSED != 0 && self.is_empty()
    }

    /// Producer: mark the stream ABORTED — it ended abnormally (the producer thread panicked or
    /// was torn down mid-transfer), so the bytes delivered so far are truncated, not a clean EOF.
    /// Also sets `FLAG_CLOSED` so the consumer unblocks instead of spinning forever.
    pub fn abort(&self) {
        self.flags()
            .fetch_or(FLAG_ABORTED | FLAG_CLOSED, Ordering::Release);
    }

    /// Consumer: whether the producer aborted (a truncated stream) rather than closing cleanly.
    pub fn is_aborted(&self) -> bool {
        self.flags().load(Ordering::Acquire) & FLAG_ABORTED != 0
    }

    /// Throughput-test helper: write the whole buffer, busy-spinning while the ring is full.
    pub fn spin_write_all(&self, mut buf: &[u8]) {
        while !buf.is_empty() {
            let n = self.try_write(buf);
            if n == 0 {
                std::hint::spin_loop();
            } else {
                buf = &buf[n..];
            }
        }
    }
}

impl Drop for Ring {
    fn drop(&mut self) {
        unsafe {
            libc::munmap(self.base as *mut libc::c_void, self.mapsz);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_threads_wrapping() {
        // Producer and consumer threads sharing one ring file; total >> cap forces wrapping.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ring");
        let cap = 4096;
        let total: usize = 4 * 1024 * 1024;
        let prod = Ring::create(&path, cap).unwrap();
        let cons = Ring::open(&path, cap).unwrap();

        let writer = std::thread::spawn(move || {
            let chunk = vec![0xABu8; 333]; // odd size to exercise wraps
            let mut sent = 0;
            while sent < total {
                prod.spin_write_all(&chunk);
                sent += chunk.len();
            }
            prod.close();
        });

        let mut got = 0usize;
        let mut buf = vec![0u8; 1000];
        loop {
            let n = cons.try_read(&mut buf);
            if n > 0 {
                assert!(buf[..n].iter().all(|&b| b == 0xAB));
                got += n;
            } else if cons.is_closed() {
                break;
            } else {
                std::hint::spin_loop();
            }
        }
        writer.join().unwrap();
        // total rounded up to a multiple of the 333-byte chunk
        assert_eq!(got, total.div_ceil(333) * 333);
    }
}
