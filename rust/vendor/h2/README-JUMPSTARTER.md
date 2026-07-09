# Vendored h2 (0.4.15) — Jumpstarter patch

Upstream: https://github.com/hyperium/h2 @ v0.4.15 (crates.io source, verbatim except one change).

**The one change** (`src/server.rs`, `Peer::convert_poll_message`): a request whose
`:authority` pseudo-header fails `http::uri::Authority` parsing is served with the
authority treated as ABSENT, instead of being reset as malformed (RST_STREAM
PROTOCOL_ERROR).

**Why:** legacy gRPC C-core clients (grpc-python, as shipped in pre-rewrite
Jumpstarter ≤ 0.7.x) dialing a `unix:///path` target set `:authority` to the
percent-encoded socket path (`var%2Ffolders%2F...%2Fsocket`, per the gRPC naming
spec). The `http` crate rejects `%` in an authority, so the stock h2 server resets
every RPC — which broke protocol backwards-compat between old clients and the Rust
exporter through the router tunnel. gRPC servers do not route on `:authority`, so
dropping it is safe.

Remove this vendor once upstream h2/http accept percent-encoded authorities or
grow a tolerance option (track hyperium/http authority parsing issues).
