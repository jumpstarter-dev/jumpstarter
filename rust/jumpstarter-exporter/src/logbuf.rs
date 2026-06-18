//! A bounded hook-output log buffer that feeds the exporter's `LogStream`, so a
//! client using `--exporter-logs` sees `beforeLease`/`afterLease` hook output even
//! when a hook ran before it connected. Mirrors the Python session `_logging_queue`
//! + `LogHandler` (a 256-entry deque replayed on `LogStream` subscribe).

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use jumpstarter_protocol::v1::{LogSource, LogStreamResponse};
use tokio::sync::broadcast;

const CAPACITY: usize = 256;

/// A shared, bounded record of hook log lines: a replay buffer (for clients that
/// connect after the line was emitted) plus a live broadcast (for current streams).
pub struct HookLog {
    tx: broadcast::Sender<LogStreamResponse>,
    buffer: Mutex<VecDeque<LogStreamResponse>>,
}

impl HookLog {
    pub fn new() -> Arc<Self> {
        let (tx, _) = broadcast::channel(CAPACITY);
        Arc::new(Self {
            tx,
            buffer: Mutex::new(VecDeque::with_capacity(CAPACITY)),
        })
    }

    /// Record a hook output line, tagged with its source.
    pub fn push(&self, source: LogSource, message: String) {
        let entry = LogStreamResponse {
            uuid: String::new(),
            severity: "INFO".to_string(),
            message,
            source: Some(source as i32),
        };
        {
            let mut buf = self.buffer.lock().unwrap();
            if buf.len() == CAPACITY {
                buf.pop_front();
            }
            buf.push_back(entry.clone());
        }
        // Ignored when there are no live subscribers.
        let _ = self.tx.send(entry);
    }

    /// A snapshot of the buffered lines (replayed to a new subscriber first).
    pub fn replay(&self) -> Vec<LogStreamResponse> {
        self.buffer.lock().unwrap().iter().cloned().collect()
    }

    pub fn subscribe(&self) -> broadcast::Receiver<LogStreamResponse> {
        self.tx.subscribe()
    }
}
