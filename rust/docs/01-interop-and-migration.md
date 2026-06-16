# Interop seam and incremental migration plan

This document pins the **first abstraction boundary** between the existing Python
implementation and the Rust core, and sequences the first opt-in steps so the two
regression suites (Go e2e + Python pytest) stay green at every commit.

Source of truth: the current Python/Go code (`path:line` citations below). Design
intent: `specs/rust-core/09-rust-core-requirements.md` §3.3–3.4, §5.

## The seam: `JUMPSTARTER_HOST`

The cleanest, already-existing process boundary in Jumpstarter is the
`JUMPSTARTER_HOST` environment contract. A "shell host" (today the Python
`jmp shell`) sets up transport to an exporter session and exports an endpoint; any
number of short-lived clients (the `j` command, driver-client libraries,
JEP-0014 polyglot runtimes) discover it purely through that env var.

```
                    JUMPSTARTER_HOST = unix:///tmp/...sock   (or host:port)
   ┌─────────────────────────┐        │        ┌──────────────────────────────┐
   │  TRANSPORT (host) side   │  serves│ consumes│  CLIENT side                 │
   │  - acquire lease         │────────┼────────▶│  client_from_path(host, ...) │
   │  - Dial controller       │  gRPC  │        │  → DriverClient proxy tree    │
   │  - bridge router stream  │ Exporter│        │  → driver .cli() / methods   │
   │    onto a local UDS      │ Service │        │                              │
   └─────────────────────────┘  (+Router)        └──────────────────────────────┘
        candidate Rust owner                          stays Python (unchanged)
```

Citations:
- `JUMPSTARTER_HOST` constant: `python/packages/jumpstarter/jumpstarter/config/env.py:9`.
- Host side exports it when spawning the shell: `launch_shell(... host ...)` sets
  `JUMPSTARTER_HOST: host` in the child env
  (`python/packages/jumpstarter/jumpstarter/common/utils.py:87,118`).
- Client side discovers + connects: `env_async` reads `JUMPSTARTER_HOST` and calls
  `client_from_path(host, ...)`
  (`python/packages/jumpstarter/jumpstarter/utils/env.py:21-37`;
  `python/packages/jumpstarter/jumpstarter/client/client.py:38`).
- The latency-critical `j` command is *only* this:
  `env_async → client.cli()` (`python/packages/jumpstarter-cli/jumpstarter_cli/j.py:24-31`).

**Contract across the seam:** `JUMPSTARTER_HOST` is a gRPC endpoint string — a
`unix://` socket path or `host:port` — serving a real `ExporterService` (and
`RouterService` for logical sub-streams) such that an unmodified gRPC client speaks
through it (spec §2.1 item 7; `exporter/session.py:38-44`). Whoever serves the
endpoint owns *transport*; whoever calls `client_from_path` owns *driver clients*.
Because the boundary is a socket + the existing wire protocol, either side can be
Rust or Python independently — which is exactly what lets us migrate module by
module.

## Migration order — lowest risk first

The exporter is the higher-risk component (process supervision, hooks, the deadlock
history in spec §2.2), so we go client/pure-data first and keep the Python driver
ecosystem untouched the whole way.

| Step | What goes Rust | Boundary that keeps suites green | Differential oracle |
| --- | --- | --- | --- |
| 0 ✅ | `jumpstarter-protocol` (generated bindings + wire-enum contract tests) | n/a (library only) | `cargo test` wire-tag asserts |
| 1 | Wire helpers: **`Value` codec ✅**, **router frame rules ✅**, exception↔status tables (live in `jumpstarter-protocol`, pure functions) | none — pure functions | **golden tests** ✅ for the Value codec: 25 fixtures from the real Python `encode_value`, replayed with byte-identical + semantic assertions; router frame classify/build unit-tested |
| 2 ✅ | `jumpstarter-config`: the three YAML kinds + env overrides + path resolution | config files are read identically by both | round-trip + **bidirectional** differential tests against Python-written fixtures (`tests/roundtrip.rs`); Python also re-parses Rust's output |
| 3 ✅ | **Transport host** (`jumpstarter-client`): lease lifecycle + **`Dial` + router→local-UDS bridge + `JUMPSTARTER_HOST`** | **the seam above** — Python `j`/driver clients connect unchanged | hermetic FSM/dial/frame unit tests + **live e2e: a real Python `j power on` tunnels through the Rust socket to the exporter** |
| 4 | Exporter core with Python driver host (subprocess), then stream/resource data plane | tunneled `ExporterService` over UDS; drivers stay Python | Go e2e `hooks`/`dut-network`/`direct-listener` + pytest |

Each step is shippable on its own and reverts to Python by config if it regresses.

## Why step 1 (the `Value` codec) is first real code — done

It is the single most compatibility-critical surface (spec §2.4, memory
`rust-core-rewrite-status`), it is pure (no I/O, no async, no cluster), and it is
trivially differential-testable against the Python `TypeAdapter` output. Getting the
int→float / tuple→list / bytes-UTF-8 quirks pinned in Rust *before* any runtime code
depends on them de-risks every later step.

**Status:** the codec landed in `jumpstarter-protocol::value`
(`encode_value`/`decode_value`/`encode_args`/`decode_args`), mapping
`serde_json::Value` ↔ `prost_types::Value`. Verified by 9 unit tests + 4 differential
golden tests that replay 25 fixtures captured from the real Python codec — asserting
**byte-identical** wire output for struct-free values and semantic equality
(map-order-insensitive, f64-normalized) elsewhere, plus decode-compat against Python's
`MessageToDict`. Remaining step-1 wire helpers: router frame rules and the
exception↔gRPC-status tables.

## Step 2 (the `jumpstarter-config` crate) — done

Type-safe serde models for all three YAML kinds (`ClientConfig`, `ExporterConfig`,
`UserConfig`) plus the shared blocks (`ObjectMeta`, `TlsConfig`, `grpcOptions` as a
typed int|string map, the driver-tree `DriverInstance` Base/Composite/Proxy union),
the config-home resolution chain, and the client env-override builder. Depends on
neither tonic nor the runtime crates (spec §3.5).

Serialization is **idiomatic, not byte-identical** to Python's `yaml.safe_dump`
(absent options omitted, keys sorted, `null` placeholders dropped) — a deliberate,
semantics-preserving choice. Verified by 5 unit tests + 8 round-trip/differential
tests over fixtures saved by the **real Python config code**
(`tests/fixtures/generate_config_golden.py`): every fixture parses, round-trips
unchanged, and Rust's parse agrees with Python's own reload. Bidirectional
compatibility was confirmed out-of-band (Python re-parses Rust's serialized output
for all fixtures). One documented leniency: `metadata.namespace` is optional in Rust
(Python requires it present-or-from-env) so round-trips stay total.

## Step 3 (the transport host) — done

The `jumpstarter-client` crate is a working transport host: it acquires a lease,
serves a local Unix socket, and tunnels each connection to the exporter through the
router — so an unmodified Python `j`/driver client runs through it via
`JUMPSTARTER_HOST`.

- **Lease lifecycle.** The condition FSM is decoupled from gRPC behind a
  `LeaseProvider` trait, so the whole acquisition machine (`Ready`, `Pending`→`Ready`,
  `NoExporter` re-create, `Unsatisfiable`, `Invalid`, not-`Pending`, `Released`,
  timeout, wrong-owner) is exhaustively unit-tested without a controller.
- **Channel/auth.** `service::ControllerClient` builds an authed TLS channel (bearer
  token + `jumpstarter-kind/namespace/name` metadata; CA or system roots) for both
  `ClientService` and `ControllerService`.
- **Dial.** `dial::dial_with_retry` replicates the `0.3→2.0 s` backoff bounded by
  `dial_timeout` on `FAILED_PRECONDITION "not ready"` / `UNAVAILABLE` (paused-time
  unit tests).
- **Router bridge.** `router::bridge` opens `RouterService.Stream` (router_token
  bearer) and forwards raw bytes both ways — `DATA` frames out, `GOAWAY`/end = EOF —
  using the pure frame rules in `jumpstarter-protocol::router`. It never parses the
  tunneled gRPC.
- **UDS host.** `transport::serve` binds a temp socket and bridges each accepted
  connection; `jumpstarter_host()` is the `JUMPSTARTER_HOST` value.

- **`jmp shell`.** `shell::run` ties it together — acquire → serve → spawn `$SHELL`
  or a command with the env contract (`JUMPSTARTER_HOST`, `JMP_DRIVERS_ALLOW`,
  `_JMP_SUPPRESS_DRIVER_WARNINGS`) → release on exit. The `jumpstarter-cli` crate
  wraps it in a clap `jmp` binary.

**Verified live (2026-06-15):** the real `jmp` binary —
`jmp shell --client … --selector … -- j power on` — acquired a lease, served the
socket, and ran a Python `j power on` through it; the exporter logs show
`Handling new connection request … (router=router.…:8083)` then
`driver.MockPower:power on`, and `jmp shell` exited 0. An unmodified Python `j` runs
against the Rust `jmp shell`.

**Hardening (2026-06-15).** Added: **`insecure` (TLS skip-verify)** via a custom
rustls verifier fed to tonic through a `connect_with_connector` connector
(triggered by `tls.insecure` or `JUMPSTARTER_GRPC_INSECURE`/`JMP_GRPC_INSECURE`) —
verified live with a **CA-less `insecure: true` config** running `jmp shell -- j
power on` through both the controller and router; the **`GetLease` transient-retry**
(exp backoff 1→120 s on `UNAVAILABLE`, bounded by the acquire budget) in the poll
loop; and propagation of `JMP_GRPC_INSECURE` into the spawned shell env.

Still open: lease-expiry monitoring/warnings, the `JMP_LEASE` existing-lease wiring,
standalone TCP/direct mode, applying config `grpcOptions`, and the remaining step-1
exception↔status tables. The router bridge currently lives in `jumpstarter-client`
and can later move to a dedicated `jumpstarter-streams` crate.

## Baseline established (2026-06-12)

- Go e2e `core`: **30 passed / 0 failed**.
- Python `jumpstarter` pytest: **519 passed**.
- `jumpstarter-protocol`: builds, 4 wire-contract tests pass.

These are the green bars that every migration step above must preserve.
