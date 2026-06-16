//! `TLSConfig` — the `tls` block shared by client and exporter configs
//! (`python/packages/jumpstarter/jumpstarter/config/tls.py`).

use serde::{Deserialize, Serialize};

/// TLS settings for a controller/exporter channel.
///
/// `insecure` here means certificate pinning is bypassed (spec doc 07 §8.3), not
/// plaintext. Both fields are always serialized (matching Python's visible
/// defaults `ca: ''`, `insecure: false`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct TlsConfig {
    #[serde(default)]
    pub ca: String,
    #[serde(default)]
    pub insecure: bool,
}
