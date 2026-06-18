# Native exporter migration: Rust-served ExporterService + slim per-lease driver host

Status: design (branch `rust-core-rewrite`). Source of truth is the **current code**; every
load-bearing claim is cited `path:line`. This doc supersedes the "router byte-bridge to a Python
session" seam in `rust/docs/02-exporter-core-plan.md` §1.4/§2 and resolves the Phase-B→Phase-C step
that plan left open ("graduate to a thin host", `rust/docs/02-exporter-core-plan.md:83-85`). It folds
in an adversarial review; every blocker is resolved in the design or recorded in §11 with its chosen
mitigation.

---

## 1. Where we are and what changes

### 1.1 Today (verified)

A single Python subprocess owns the **entire** exporter protocol surface for the whole driver tree.
`session_host.py` instantiates one `Composite` root wrapping `config.export` and serves the full
`ExporterService` + `RouterService` on two UDS (main + hook), holding the session at `LEASE_READY`
for its whole life (`rust/jumpstarter-exporter/python/session_host.py:32-47`,
`python/packages/jumpstarter/jumpstarter/exporter/session.py:294-420`). The Rust core is a **byte
bridge**: each `ListenResponse` dials the main socket and pumps raw router frames between the router
and that socket (`rust/jumpstarter-exporter/src/exporter.rs:339-351`,
`rust/jumpstarter-client/src/router.rs:37-104`). Rust already owns the lease loop, FSM, hooks, and
status-to-controller projection (`rust/jumpstarter-exporter/src/{exporter,fsm,hooks,control}.rs`).

Tunnel nesting (the wire contract, **un-collapsible**): client → outer `RouterService.Stream` (raw
gRPC connection bytes) → exporter; the inner driver `DriverCall`/`StreamingDriverCall`/`Stream` RPCs
multiplex over that tunnel.

### 1.2 Target boundary

Rust **terminates the router tunnel and serves `ExporterService` + the inner `RouterService.Stream`
itself** (a tonic server on the per-lease UDS), routing `DriverCall`/`StreamingDriverCall`/`Stream`
by driver UUID and answering `GetReport`/`GetStatus`/`LogStream`/`Reset`/`EndSession` from Rust
state. Python is reduced to a **slim per-lease driver host** that owns the Python `Driver` objects
and answers the *driver-level* RPCs (`DriverCall`/`StreamingDriverCall`/`Stream`/`GetReport`/
`LogStream`) on its own **single** private UDS — but no longer serves any protocol to clients or to
hooks, and no longer owns the lease lifecycle, status gate, or socket lifecycle.

```
            ┌─────────────────────── Rust exporter core ────────────────────────┐
client ──►  │ outer router tunnel terminator → ExporterServiceServer (tonic)    │
            │   main UDS  → clients      hook UDS → hook `j` subprocesses        │
            │   GetReport / DriverCall / StreamingDriverCall / Stream            │
            │   LogStream / GetStatus / Reset(UNIMPL) / EndSession               │
            │   uuid→host routing │ lease FSM │ hooks │ status │ LogStream agg    │
            └──────────────────────────────┬────────────────────────────────────┘
                              private host UDS │ (driver-level RPCs only)
                                       ┌───────▼────────┐
                                       │  driver host   │  one process per LEASE,
                                       │  Python Driver │  owns the WHOLE config
                                       │  tree (full)   │  tree (enumerate / Proxy /
                                       └────────────────┘  reset / close in-process)
```

**The decisive architectural choice (resolving four review blockers at once): the per-lease Python
host owns the WHOLE driver tree, not one process per leaf.** "Per-driver-instance" in the goal is
realized as *Rust routing by uuid into one host*, not *one OS process per driver*. This is faithful
to "Python is reduced to a slim host that just dispatches its own calls" — the host no longer serves
ExporterService to anyone but Rust — while keeping `enumerate()`/Proxy resolution/`reset`/`close` as
single-process, in-tree operations exactly as today. One-host-per-leaf-driver is deferred to an
optional, separately-justified increment (§7 inc6), gated on a real need (true fault isolation or
native non-Python hosts), because splitting the tree across processes breaks Proxy dispatch and the
in-process tree walk (§11 OQ1, OQ2). The router byte-bridge is replaced by a tunnel→tonic adapter
(§6.3); `router::bridge` stays in the client crate unchanged.

### 1.3 What stays in Python (the irreducible seams)

1. Per-lease driver-tree instantiation from config (`import_class`, constructor kwargs)
   — `python/packages/jumpstarter/jumpstarter/config/exporter.py:112-141`.
2. `reset()` on session entry / `close()` on exit, errors swallowed
   — `python/packages/jumpstarter/jumpstarter/exporter/session.py:57-75`.
3. `enumerate()`/`report()` metadata, **including Proxy full-subtree splice and Composite nodes**
   — `python/packages/jumpstarter/jumpstarter/driver/base.py:202-230`,
   `python/packages/jumpstarter-driver-composite/jumpstarter_driver_composite/driver.py:42-48`.
4. Call dispatch by method name: marker lookup, arg decode, exception→status mapping,
   `google.protobuf.Value` marshaling, the resource-handle FSM and `compress_stream` codecs
   — `python/packages/jumpstarter/jumpstarter/driver/base.py:113-200,232-238,348-374`,
   `python/packages/jumpstarter/jumpstarter/common/serde.py:6-14`.

Everything else (the protocol surface to clients/hooks, lease FSM, status, EndSession, LogStream
aggregation, router termination) is Rust.

---

## 2. The Rust ExporterService + RouterService session server (tonic)

### 2.1 The routing table

The session server holds an `Arc<SessionRouter>`, built once per lease after the host reports:

```rust
struct SessionRouter {
    /// One channel to the single per-lease driver host (cloneable; concurrent
    /// calls HTTP/2-multiplex over the host UDS). All uuids route here today;
    /// the field is a map so inc6 can fan out to per-leaf hosts without a reshape.
    host: ExporterServiceClient<Channel>,
    /// uuid → host. With one host this is "every uuid → the one host"; the map
    /// is materialized from the host's GetReport so Rust validates/normalizes
    /// uuids at the boundary. Proxy duplicate uuids collapse to one entry,
    /// matching Session.mapping (session.py:81).
    drivers: HashMap<String, ExporterServiceClient<Channel>>,
    /// The aggregated pre-order GetReport, cached at lease start (static for the
    /// lease; UUIDs are frozen once instantiated, metadata.py:7-10). REBUILT per
    /// lease from the live host — never reused from the registration host (§3.3).
    report: GetReportResponse,
    /// Bounded log queue fed by the host LogStream + Rust's own records (§4.1).
    logs: LogQueue,
    /// Status projection from the lease FSM (§4.2).
    status: watch::Receiver<StatusSnapshot>,
    /// EndSession trigger wired into the lease task (§4.3).
    end_session: Arc<Notify>,
    /// Current FSM phase, read by the driver-call gate (§2.2).
    phase: watch::Receiver<LeasePhase>,
}
```

UUIDs are dispatch keys (`session.py:305-314`); children are never reachable through a parent UUID.
Because one host owns the whole tree, `drivers[uuid]` resolves to that host for **every** uuid —
including Proxy duplicates — so Proxy DriverCall/Stream dispatch is correct with zero cross-process
resolution (the review's hard Proxy blocker is dissolved, not merely patched).

### 2.2 The seven RPCs + Stream

`build.rs` already emits server stubs (`build_server(true)`, `rust/jumpstarter-protocol/build.rs:42`),
so we implement `exporter_service_server::ExporterService` and `router_service_server::RouterService`
on one `Arc<SessionRouter>` and register both on a single tonic server, on **two** `UnixListener`s
(main + hook), mirroring Python's "two ports, one server" (`session.py:244-257`).

| RPC | Rust behavior |
|---|---|
| `GetReport(Empty)` | Return the cached per-lease `report` verbatim (§3). No host round-trip. |
| `DriverCall(req)` | Look up `drivers[req.uuid]`; on miss → `Status::unknown(...)` (§2.5). Else `host.driver_call(req)` and forward the typed `DriverCallResponse` / tonic `Status` **unchanged** (no re-marshal). |
| `StreamingDriverCall(req)` | Same lookup; relay the response stream item-by-item, preserving deliver-then-abort ordering with the trailing `Status` (`base.py:140-168`). |
| `LogStream(Empty)` | Drain the aggregated `LogQueue` (§4.1); not status-gated; `uuid=""`, `source` always present. |
| `GetStatus(Empty)` | Build from the FSM `StatusSnapshot` (§4.2). |
| `Reset(ResetReq)` | `Err(Status::unimplemented(...))` — frozen quirk (`protocol/proto/jumpstarter/v1/jumpstarter.proto:142`). |
| `EndSession(Empty)` | Signal the lease task; reply immediately (§4.3). |
| `RouterService.Stream` | Parse `request` metadata (driver\|resource), route by uuid, **gRPC-proxy** the inner Stream to the host (§5). |

**Driver-call status gate.** `_check_status_for_driver_call` is a **no-op on main today** (un-awaited
`context.abort`, `session.py:268-292`). Rust replicates the no-op by **default**: calls always pass.
A `strict_driver_gate: bool` (default `false`) opt-in rejects calls outside `{Ready, BeforeLease,
AfterLease}` with `FAILED_PRECONDITION`. **The gate, when enabled, consults the Rust FSM `phase`
only** — the single source of truth. The host keeps forcing `LEASE_READY` (§5.1) so the host's own
(unused) gate never fires; Rust never double-gates (resolves the review's no-op-inversion blocker).

### 2.3 Concurrency and backpressure

Each inbound RPC is its own tonic-spawned task; the per-host `ExporterServiceClient` is cloned per
call and HTTP/2-multiplexes over the host UDS. No global lock on the hot path — `SessionRouter` is
`Arc`, `drivers` is read-only after build. Stream-proxy buffering is bounded (§5.5).

### 2.4 Plugging into the existing lease loop / FSM / hooks

The session server is **per-lease**; it owns the per-lease host channel and is started in
`spawn_lease` during `Starting` (before `BeforeLease`). The FSM (`fsm.rs:34-77`) and hooks
(`hooks.rs:62-161`) are unchanged in shape; hooks still drive the `BEFORE_LEASE_HOOK→LEASE_READY→
AFTER_LEASE_HOOK→AVAILABLE` projection to the controller. New wiring:

- `spawn_lease` spawns the per-lease host (§4.4), GetReports it, builds `SessionRouter`, and binds
  the tonic server on the per-lease **main + hook** UDS — **all within `Starting`, before
  `BeforeLease`** so the hook socket is routable when the beforeLease hook runs `j power on`
  (resolves the spawn-ordering/LEASE_READY-deadlock blocker; `exporter.rs:246-249`). A per-lease
  spawn+bind timeout (`HOST_START_TIMEOUT`, default 30s; reuse `driver_host.rs:28`) maps failure to
  `Failed` + `OFFLINE`.
- `spawn_listen` (`exporter.rs:303-354`) still opens `Listen`, but each `ListenResponse` now
  terminates the outer tunnel into the local tonic server (§6.3) instead of byte-bridging to Python.
- Hook subprocesses still get `JUMPSTARTER_HOST` = the **hook UDS** (`hooks.rs:213`), now a bind
  address of the Rust tonic server. `j power on` hits the Rust `ExporterServiceServer`, which routes
  to the host. Main/hook stay **separate listeners** so the SSL-frame-isolation property
  (`session.py:244-257`) is preserved.

### 2.5 Unknown / malformed UUID

Python surfaces missing-uuid (`KeyError`) and malformed-uuid (`ValueError`) as gRPC `UNKNOWN`,
because both are raised **outside** the driver try-block (`session.py:308,317,319`). Rust replicates
**`UNKNOWN` by default** for both DriverCall and Stream (no test pins a cleaner code, and the client
maps `NOT_FOUND→DriverMethodNotImplemented` at `core.py:416-418`, which is observably different). A
cleaner `NOT_FOUND`/`INVALID_ARGUMENT` is gated behind `strict_driver_gate` (§11 OQ4).

---

## 3. GetReport construction in Rust

### 3.1 Source of truth: the host's full-tree GetReport

Because the single host owns the whole tree, its `GetReport` already returns the **complete,
pre-order, parent-linked** `reports` list — Proxy full-subtree splices, Composite nodes,
`jumpstarter.dev/client`/`jumpstarter.dev/name` labels and all (`session.py:294-303`,
`base.py:202-230`, `composite/driver.py:42-48`). **Rust does not reassemble the tree from per-node
metadata.** It takes the host's `reports` essentially verbatim and only adjusts the envelope (§3.2).
This sidesteps the review's two GetReport blockers (Proxy emits a full subtree, not one report;
optional-field presence) — Rust never re-emits per-node reports, so it cannot get the splice or the
absent-vs-present-empty bytes wrong. The Proxy subtree, root-has-no-`parent_uuid`/`name`, and
empty-description→absent are all produced by Python and copied.

### 3.2 The envelope

Today the envelope `uuid`/`labels` come from the `Session`'s `Metadata` — built **without** uuid or
labels in `session_host.py`, so the envelope `uuid` is a random `uuid4` and `labels` is empty
(`Metadata` default_factory; `session.py:296-298`). The registration path forwards `report.labels`
to the controller (`exporter.rs:80-92`). **Decision:** Rust sets the envelope from **exporter config
metadata** (uuid + labels), not the host's random envelope, because that is the identity the
controller and CLI expect; the empty-envelope-labels behavior was an accident of constructing the
Session without metadata, and the controller registration already wants the config labels. The
host's `reports` (the load-bearing per-driver content) are copied unchanged. `alternative_endpoints`
stays **empty** for parity (`jumpstarter.proto:155`). (Recorded as §11 OQ3: confirm the controller
and `client_from_channel` do not depend on a random envelope uuid; today's path already substitutes
config labels at registration.)

### 3.3 Per-lease rebuild — never reuse the registration tree

Each lease instantiates a fresh tree with fresh `uuid4`s (`metadata.py:8`). Therefore:

- The **registration** GetReport (controller `RegisterRequest`) uses a throwaway host set up once at
  startup, then killed (§4.4). Its UUIDs are discarded.
- The **per-lease** cached `report` **and** the `drivers` routing map are **rebuilt from the
  live per-lease host** at `Starting`, every lease. Rust never routes against registration-time
  UUIDs (resolves the throwaway-vs-lease UUID-identity blocker). The client always re-fetches
  GetReport per session (`client.py:99`), so per-lease UUID churn is invisible to it.

---

## 4. LogStream, GetStatus, Reset, EndSession, per-lease re-instantiation

### 4.1 LogStream aggregation

Today the Python session drains a `deque(maxlen=256)` on a 50ms poll (`session.py:331-337`). New
model:

- `LogQueue` = bounded ring (cap 256, drop-oldest) behind a `Mutex<VecDeque>` + `Notify` (exact
  drop-oldest semantics). The Rust `LogStream` RPC drains it; **not** status-gated. Every record:
  `uuid=""` (present, empty) and `source` always set, normalizing host records for field-set parity
  (resolves the minor LogStream-presence hole).
- At lease start Rust opens **one** long-lived `LogStream` to the host, tagging each record
  `LogSource::DRIVER`/`SYSTEM` as the host emits. The pump task is supervised: host EOF/error logs a
  debug and exits quietly without tearing down aggregation; the abrupt close on host SIGKILL at
  `Releasing` is treated as normal teardown (resolves the LogStream fault-isolation hole). Because
  there is **one** host, the 256-ring is not a shared multi-driver budget and the drop-semantics
  change the review flagged for N hosts does **not** arise.
- Rust's own lifecycle logs and hook stdout/stderr (`hooks.rs:266-276`) are pushed as
  `LogSource::SYSTEM`/`{BEFORE,AFTER}_LEASE_HOOK` **by current FSM phase at enqueue time** — Rust
  owns phase tagging; the host boundary only distinguishes SYSTEM vs DRIVER.

### 4.2 GetStatus

The FSM drives a `watch::Sender<StatusSnapshot>` bumped on every status report (`hooks.rs:163-167`):

```rust
struct StatusSnapshot {
    status: ExporterStatus,
    message: String,          // always present (default ""), never absent (session.py:372-375)
    status_version: u64,      // monotonic, ++ on EVERY update (not just transitions)
    previous: Option<ExporterStatus>, // set only when status actually changes
}
```

`GetStatus` returns it directly. `status_version` increments on every `report_status` to preserve
missed-transition detection (`session.py:339-360`). `message` is always present; FSM per-phase
strings are documented as non-load-bearing (no client substring-matches GetStatus.message). The dead
`_status_update_event` (`session.py:357-360`) is dropped.

**Cross-lease idle reachability (resolves two majors).** GetStatus and EndSession must answer when no
lease is active (clients/controller poll GetStatus for `AVAILABLE` after afterLease; EndSession
answers `success=false` when idle — `session.py:389-407`). Therefore the tonic
ExporterServiceServer's **GetStatus, EndSession, LogStream, Reset, and GetReport handlers are served
by a process-lifetime server**, not torn down between leases. Concretely: the main+hook tonic
listeners and the `Arc<SessionRouter>`'s status/logs/end_session fields live for the **exporter
process lifetime**; only the **per-lease host channel and the `drivers`/`report` routing state** are
swapped per lease (an `ArcSwap`/`watch` of the per-lease routing table). When idle, `drivers` is
empty so DriverCall/Stream return `UNKNOWN`/unavailable (no lease), but GetStatus returns the current
FSM status (`AVAILABLE`) and EndSession returns `success=false, "No active lease context"`
(`session.py:402-407`). This makes the server's existence lease-independent while routing is
lease-scoped.

### 4.3 EndSession wired to afterLease

`EndSession` returns immediately and signals the lease task, never waiting for the hook
(`session.py:381-420`):

- The lease task creates `end_session: Arc<Notify>` exposed via the process-lifetime server.
- `EndSession` RPC: if a lease is **active** (lease task exists and phase ∈ `{BeforeLease, Ready,
  Ending, AfterLease}`) → `end_session.notify_one()`, reply `success=true, "Session end triggered,
  afterLease hook running asynchronously"`; else `success=false, "No active lease context"`
  (matching Python's `lease_context is None` check; phase predicate recorded as §11 OQ6).
- The lease task selects on `{ end_session.notified(), controller lease-end (`end`,
  `exporter.rs:257`), shutdown }`. Whichever fires first drives `Ready→Ending`. afterLease runs
  **exactly once** via the single `Ready→Ending→AfterLease` task path — no CAS claim needed (Python
  needs `after_lease_hook_started` only because two coroutines compete; we collapsed it to one task).
- **afterLease must run regardless of client state.** Today Rust runs afterLease only when
  `has_client` (`exporter.rs:275`). For an explicit `EndSession`, Rust **forces `has_client=true`**
  before driving `Ending`, because Python promises afterLease runs after EndSession irrespective of
  whether a tunnel was opened (resolves the `has_client` vs EndSession hole; §11 OQ6 records the
  alternative of confirming controller expectation).
- **Teardown ordering with in-flight streams (resolves the EndSession-teardown blocker).** afterLease
  runs while the session server **and host stay alive**, so the hook's `j power off` (a DriverCall
  through the hook UDS) works and a client's still-open Stream is not cut. The host is killed and the
  per-lease routing state cleared only at `Releasing`, **after** afterLease completes **and after**
  the controller's `leased=false` `end` fires (so a client polling LogStream to `AVAILABLE` is not
  cut by an early EndSession-triggered afterLease). Concretely the lease task, after an
  EndSession-driven afterLease, reports `AVAILABLE` but waits on the controller `end` (or a grace)
  before dropping the host — mirroring Python keeping the socket open post-EndSession
  (`session.py:381-420`).

> **Latency (inc3) and pre-warming (resolved).** Spawning a fresh *process* per lease
> is slower than Python's in-process `device_factory()` re-instantiation: a power-only
> host spawns in ~300 ms, but a heavy driver import (e.g. `opendal`) can take seconds,
> which — left at lease start — delays `LEASE_READY` past the controller's
> lease-readiness window and gets the lease reclaimed before a client connects.
> **Fixed by pre-warming the host pipeline** (`Warm` in `exporter.rs`): the next fresh
> host is spawned in the background *during* the current lease, and the registration
> host is reused as the first warm one, so a lease pays only routing setup, not the
> spawn. Verified: with a **3 s simulated spawn delay**, back-to-back leases still
> reach `LEASE_READY` in ~250 ms. Each lease still gets a freshly-spawned, `reset()`
> host — just spawned ahead of time. (A slow `beforeLease` *hook* — Python `j` startup
> with heavy imports — is a separate cost, addressed later by the native Rust `j`.)
> Per-driver-instance pre-warmed processes (the user's other option) were considered
> and deferred: they'd add isolation/parallelism but break server-side cross-driver /
> composite calls and cross-driver resource handles (§7 inc6 / JEP-0013).

### 4.4 Per-lease re-instantiation (spawn/kill host per lease)

Fresh drivers per lease is a hard invariant (`device_factory()` per lease,
`exporter.py:577-593`; the MockPower `reset()` counter is the test). Today `DriverHost::spawn` runs
**once** at startup (`exporter.rs:66`). It moves into `spawn_lease`:

- On `Starting`: spawn the host subprocess (§5.1), read its **single** UDS line, build the
  `ExporterServiceClient`, GetReport it, cache the per-lease `report` + `drivers` map, bind the
  per-lease routing into the process-lifetime tonic server.
- On `Releasing`/lease-end/abort: **gracefully stop the per-lease routing** (drain in-flight,
  grace ≈ 1.0s + 0.1s settle to match `session.py:130-136`), then drop the host (`kill_on_drop`,
  `driver_host.rs:114-118`), **await host process exit**, clean up the host UDS temp file, then
  `INTER_LEASE_SETTLE` (`exporter.rs:41`). Awaiting exit (not just the lease-task handle) prevents
  overlapping host sockets across leases (resolves the inter-lease-settle/SIGKILL major). The
  process-lifetime listeners are **not** rebound per lease, so there is no per-lease main/hook socket
  churn — only the host UDS churns.
- The startup registration needs one tree before the first lease: spawn a **throwaway** host, GetReport
  it for `RegisterRequest`, kill it (§3.3). Smallest delta; mirrors Python's throwaway
  `async with self.session(): pass` (`exporter.py:786-787`). The double-instantiate cost is recorded
  as §11 OQ5.

---

## 5. Streaming + resource-handle proxying (the riskiest path)

The inner `RouterService.Stream` is **not** a byte relay — it is a full gRPC proxy. There is **no
metadata channel in the router frame stream** (`classify` knows only DATA/GOAWAY/PING,
`rust/jumpstarter-protocol/src/router.rs:39-47`), so the "metadata copied for free" premise is false
and is dropped. `grep -rn initial_metadata rust/` returns nothing today; this is net-new code.

### 5.1 The slim host protocol (single private UDS)

The host serves a **single-UDS** `ExporterService` + `RouterService` over the **whole tree**, reduced
from `session_host.py` to: instantiate the config root (`Composite(children=config.export)`), enter
`Session`, serve on **one** `serve_unix_async` socket (no hook socket — hooks are lease-scoped and
served by Rust), force `LEASE_READY` (permissive gate), print **one** line, sleep forever. It reuses
`Session`'s dispatch, marker lookup, exception mapping, Value serde, the resource FSM, and
`compress_stream` **verbatim** (`session.py:238-241,305-329`, `base.py:113-200`). The only new Python
is the ~20-line wrapper and the single-socket serve. See `slim_host_protocol` for the exact contract.

### 5.2 The inner-Stream gRPC proxy (`tunnel.rs`)

On an inbound client `RouterService.Stream`, Rust MUST, **in this order**:

1. Parse the `request` invocation metadata (`{kind:driver,uuid,method}` or
   `{kind:resource,uuid,x_jmp_content_encoding}`, `common/streams.py:14-33`). Unknown extra metadata
   keys are ignored; unknown uuid / malformed metadata → `UNKNOWN` (§2.5).
2. **Eagerly** open a tonic `RouterService.Stream` client call to the host, forwarding the `request`
   metadata (the host needs it to enter the driver/resource context). Do **not** wait for a client
   frame first — the client blocks on `initial_metadata()` before sending anything
   (`core.py:433,450`), so waiting for an uplink frame deadlocks (resolves the handshake-inversion
   and metadata-before-frame deadlock blockers). The uplink is, however, allowed to flow concurrently
   for the rare driver that needs a client byte first.
3. **Await the host call's initial response metadata** (`response.metadata()` on the tonic
   `Streaming` response) and **re-emit it on the client-facing servicer response before yielding any
   frame**. Relay **only** the keys the host sent: empty for driver streams; exactly `resource` +
   `x_jmp_accept_encoding` for resource streams (`session.py:320-323`). No extra tonic/reserved keys
   leak, so `ResourceMetadata(**dict(initial_metadata()))` parses (`core.py:450-452`) (resolves the
   initial-metadata blockers).
4. Only then start the bounded, **message-by-message** bidi frame relay (§5.5).

`x_jmp_accept_encoding` is read from the host metadata as an **opaque ASCII string** (including the
empty-string case the host emits when declining, `base.py:189-198`) and re-emitted byte-identically.
Rust **never** derives, defaults, or omits this key (resolves the None→`""` coercion major; §11 OQ7
records the canonical absent representation for non-Python clients).

### 5.3 Held-open + teardown semantics

The inner Stream RPC stays open until the **client** half-closes/cancels the HTTP/2 call, **not** when
the driver stream EOFs (`session.py:325-329`). So `tunnel.rs` MUST NOT terminate the client-facing
Stream on host-side EOF; the reused `router::bridge` shape (which breaks on `FrameAction::Eof`,
`router.rs:91`) is **only** valid for the **outer** tunnel, not this inner hop. On its own teardown
the proxy MUST abort the client-facing servicer with `Status` code `ABORTED`, details exactly
`"RouterStream: aclose"`, matching `RouterStream.aclose` (`streams/router.py:65-69`) — old clients
treat ABORTED-on-close as normal (resolves the early-close and clean-close-vs-ABORTED blockers; §11
OQ8 records getting maintainer sign-off if we ever want clean OK).

### 5.4 What Rust routes vs what the host does

- **Rust routes** by uuid and gRPC-proxies the inner Stream as above; it never decompresses, never
  registers a handle, never inspects payloads.
- **The host does** all stream semantics: `@exportstream` context entry, resource memory-pipe
  registration in `driver.resources`, `compress_stream` (gzip/xz/bz2/zstd), single-use `resource()`
  consume + `finally` delete (`base.py:182-200,232-238,348-357`). `JMP_DISABLE_COMPRESSION` stays a
  host env. Rust does **no** codec work, and **no** progress accounting — `ProgressStream`/
  `ProgressAttribute.total` are endpoint-local (client + host), never crossing the proxy
  (`streams/progress.py`; resolves the progress minor as a documented non-action).
- **Resource Stream + DriverCall overlap.** A resource upload is a two-RPC dance on the **same** uuid:
  the Stream RPC mints `driver.resources[uuid]` and stays open while the DriverCall drains it
  (`base.py:182-200,232-238`). Both land on the **same host process** (one host owns the tree), and
  `driver.resources` is per-Driver-instance, so the two independent HTTP/2 streams to the host share
  the registry correctly. Rust MUST NOT couple the Stream-proxy task lifetime to the DriverCall task
  (resolves the resource-overlap blocker). Cross-driver handle passing is unaffected here because the
  whole tree is one process (the per-leaf narrowing only appears in inc6; §11 OQ2).

### 5.5 Frame-boundary-preserving, bounded relay

The inner hop relays **whole `StreamRequest`/`StreamResponse` messages**, not bytes through a fixed
`CHUNK` buffer. `router::bridge`'s 32KiB byte-read loop (`router.rs:24,57`) would **split** the host's
64KiB frames and change interactive latency on consoles; `tunnel.rs` instead forwards each inbound
frame as one outbound frame, preserving boundaries and zero-length DATA passthrough vs the GOAWAY=EOF
distinction (`classify` maps GOAWAY→Eof, PING→Drop; empty-payload DATA stays `Payload(vec![])` and is
written through — `write_all(&[])` is a no-op but the frame is preserved). The relay channel is
**bounded at 32** (matching the Python 32-item memory pipe and the client bridge's mpsc(32),
`streams/common.py:48-53`, `router.rs:49`) so backpressure propagates end-to-end and a large image
flash cannot OOM (resolves the backpressure and re-chunking majors). HTTP/2 keepalive options on the
inner hop match `session.py:32-35` so idle tunnels survive (`specs/rust-core/06-streams-and-router.md`
§2.4).

**Implementation note (inc2).** `tunnel.rs` realizes this as a *transparent message passthrough*
rather than an explicit `mpsc(32)` relay: it returns the host's `Streaming<StreamResponse>` directly
and forwards the client uplink (`filter_map(|f| f.ok())`) as the host call's request body. This is
simpler and strictly better on two of the contracts above — the host's trailing
`ABORTED "RouterStream: aclose"` status propagates for free, and client cancellation drops the host
call via `Drop` — while still preserving message boundaries 1:1 (tonic delivers each protobuf message
as one stream item, never split or batched). Backpressure is **HTTP/2 flow control** instead of the
explicit 32-deep channel: because the proxy is pull-driven (each direction is polled by its
downstream peer with no intermediate buffer), a slow client throttles the host and vice versa.
Verified empirically: a **500 MB resource upload** round-trips byte-exact while the Rust exporter's
RSS stays at **~20 MB (3 MB of growth)** — no unbounded buffering. The adversarial inc2 verification
flagged the absence of an explicit `bounded(32)` loop as an OOM risk; this measurement refutes it.

---

## 6. Crate placement and module layout

### 6.1 Decision: grow `jumpstarter-exporter`; extract `jumpstarter-driver-host` later

The host boundary today is one 118-line module + one Python script
(`rust/jumpstarter-exporter/src/driver_host.rs`). We grow `jumpstarter-exporter` and extract a
`jumpstarter-driver-host` crate only when a second consumer appears (standalone mode, or inc6's
per-leaf/native hosts). Premature splitting churns the workspace (`rust/Cargo.toml:11-18`) for no
reuse. The session server and routing table are exporter-internal. (See §11 OQ9: keep `session/`
structured so a standalone TCP+TLS listener with a passphrase interceptor can reuse the same
servicers, and so JEP-0013 per-driver native services can register alongside ExporterService.)

### 6.2 New/changed modules in `rust/jumpstarter-exporter/src/`

| Module | Responsibility | Status |
|---|---|---|
| `session/mod.rs` | `SessionRouter`, the tonic `ExporterService`+`RouterService` impls, bind main+hook UDS (process-lifetime) | new |
| `session/route.rs` | per-lease uuid→host table build; `request`-metadata parse for Stream | new |
| `session/logs.rs` | `LogQueue` (256 ring, drop-oldest) + the single host LogStream pump | new |
| `session/status.rs` | `StatusSnapshot` + `watch` from the FSM | new |
| `tunnel.rs` | (a) terminate the **outer** `RouterService.Stream` into the local tonic server (§6.3); (b) the **inner** Stream gRPC proxy (§5.2–5.5) | new |
| `report.rs` | take the host's full-tree `reports`; set the config envelope (§3) | new |
| `driver_host.rs` | spawn the slim host (single-UDS handshake) per lease | grow (one-line read; drop the hook-socket line) |
| `exporter.rs` | move host spawn into `spawn_lease`; bind process-lifetime tonic server; replace `router::bridge` call with `tunnel::terminate`; force `has_client` on EndSession | grow |
| `fsm.rs` / `hooks.rs` / `control.rs` | unchanged shape; `hooks.rs` also bumps `StatusSnapshot` + the `phase` watch | minor |

`slim_driver_host.py` lives alongside `session_host.py` (kept for inc0–inc1 fallback; removed at inc4).

### 6.3 Terminating the OUTER tunnel into the local tonic server

A `ListenResponse` gives a router endpoint+token; the bytes inside that tunnel are a client gRPC
connection that must reach the Rust `ExporterServiceServer`. Two options:

- **(A) In-process duplex.** Open the outer `RouterService.Stream` to the router (reuse
  `client/router.rs` framing), expose the de-framed bytes as a `tokio::io::DuplexStream`, and feed a
  tonic `Server` via `serve_with_incoming` over a stream that yields **one `DuplexStream` per inbound
  tunnel** (so N concurrent client connections per lease are independent accepted connections, not
  serialized; the review's serve_with_incoming concurrency note). No loopback socket.
- **(B) Loopback to the per-lease UDS.** Bridge the outer tunnel bytes to the **main UDS the tonic
  server already listens on** (literally `router::bridge(UnixStream::connect(main), …)`), i.e. the
  existing bridge with its target flipped from the Python socket to the Rust server's own socket.

**Recommend (B) for the first cutover** (inc1): it reuses `router::bridge` unchanged — the diff is
"the main UDS is now served by Rust, not Python" — and each bridge is an independent accepted
connection on the main UDS, preserving per-connection isolation. **Optimize to (A) in inc5** to shed
the loopback hop. The "(B) reuses the bridge unchanged" claim holds **only** for the outer tunnel;
the inner Stream hop (§5) never reuses the bridge.

---

## 7. Sequenced increment plan

Ground rule: every increment keeps **both** suites green (Python `pytest`, Rust `cargo test`) and is
**live-verifiable** against the e2e env (MockPower round-trip + hooks). Baseline to preserve: e2e
`core` 30/30, `hooks||dut-network||direct-listener` 18/19, Python 519 passed
(`e2e-local-environment.md`). Lowest-risk first; no big-bang cutover.

### Increment 0 (FIRST): slim host serves the whole tree on ONE socket; Rust still byte-bridges

**This is the first increment.** Add `slim_driver_host.py`: instantiate the config root
(`Composite(children=config.export)`, identical tree to `session_host.py:34-38`), enter `Session`,
serve on **one** `serve_unix_async` socket, force `LEASE_READY`, print **one** line. Add
`DriverHost::spawn_single` reading a **single** UDS line, and reuse the **existing** byte bridge to
that one socket — for the bridge target only; hooks still need a socket, so inc0 keeps the
two-socket `session_host.py` path for the hook socket **or** binds hooks to the same single socket
(inc0 has no Rust server yet, so we keep `session_host.py` as the live path and exercise
`slim_driver_host.py` only behind the new spawn flag + a Rust unit test). The Rust core, FSM, hooks,
registration, and `router::bridge` are untouched. Strictly additive: a Python script + a spawn-path
variant + a unit test.

- **Deliverables:** `slim_driver_host.py` (one socket, one line); `DriverHost::spawn_single`; a Rust
  unit test spawning it for a MockPower-only config and asserting `GetReport` returns the
  Composite-root + MockPower leaf with `jumpstarter.dev/client =
  jumpstarter_driver_power.client.MockPowerClient`. No change to `exporter.rs`/`router::bridge`/
  `fsm`/`hooks`.
- **Live verification:** point a single-driver config (`export: {power: {type: MockPower}}`) at the
  unchanged `session_host.py` path for the live run (the slim host is unit-tested only this
  increment); e2e `core` MockPower round-trip stays 30/30; Python pytest green. Risk: **low.**

### Increment 1: Rust ExporterServiceServer on the per-lease main UDS; host behind it (loopback termination, no hooks/streams)

Stand up `session/` (the process-lifetime tonic server) bound on the main UDS; build the per-lease
`SessionRouter` from the slim host (now driving the live path). `GetReport` from the cached host tree
(§3); `DriverCall`/`StreamingDriverCall` proxy to the host; `Reset` UNIMPL; `GetStatus`/`LogStream`
basic from FSM/queue. Keep `router::bridge` but flip its connect target to the Rust server's own main
UDS (§6.3-B). Stream + hooks + per-lease respawn deferred.

- **Deliverables:** `session/{mod,route,logs,status}.rs`, `report.rs`; DriverCall/StreamingDriverCall/
  GetReport/LogStream/GetStatus/Reset wired; `tunnel::terminate` (outer, option B). FSM unchanged.
- **Live verification:** same MockPower round-trip, but `DriverCall(power.on)` now lands on the Rust
  server which proxies to the host; add a Rust integration test driving `DriverCall` through the
  local server. e2e `core` 30/30; Python pytest green. Risk: **medium** (first Rust-served inner RPC).

### Increment 2: inner-Stream gRPC proxy (driver streams + resources)

Implement `RouterService.Stream` on the Rust server per §5: parse `request`, eagerly open the host
Stream, relay host initial metadata **before** any frame, then bounded message-by-message frame relay;
held-open until client hangup; ABORTED `"RouterStream: aclose"` on teardown. Enables `@exportstream`
streams and resource handles through Rust.

- **Deliverables:** `tunnel.rs` inner-Stream proxy; `session/route.rs` metadata parse;
  initial-metadata capture/relay ordering; bounded(32) frame relay; encoding passthrough (§5.2).
- **Live verification (named tests):** (1) a resource Stream through the Rust server asserts
  `resource` + `x_jmp_accept_encoding` arrive in the client's `initial_metadata()` **before** the
  client sends bytes; (2) a deadlock-regression test opens a driver Stream and asserts the client
  unblocks on `initial_metadata()` without sending; (3) a resource upload where the Stream and the
  draining DriverCall overlap asserts the full payload arrives; (4) a console-style `@exportstream`
  round-trips. Run e2e `dut-network`/`direct-listener` labels green; Python pytest green. Risk:
  **medium-high.**

### Increment 3: per-lease host spawn/kill + EndSession + status snapshot from FSM

Move host spawn into `spawn_lease` (within `Starting`, before BeforeLease); respawn per lease (fresh
drivers); graceful per-lease routing teardown + await host exit + UDS cleanup + settle; throwaway-host
registration GetReport; wire `EndSession` (force `has_client`, hold host alive through afterLease,
drop at Releasing after controller `end`); `StatusSnapshot`/`phase` watches to GetStatus and the
(default-off) gate.

- **Deliverables:** per-lease `DriverHost` lifecycle; throwaway-host registration; `EndSession`→lease
  task with held-open teardown; `status.rs`; LogStream phase tagging.
- **Live verification:** back-to-back leases show MockPower `reset()` counter incrementing (fresh
  drivers); a Python client `EndSession` triggers afterLease early **and** afterLease runs even with
  no prior tunnel; `GetStatus` reports monotonic `status_version` and answers `AVAILABLE` idle between
  leases; `EndSession` while idle returns `success=false`. e2e `core` across multiple leases; Python
  pytest green. Risk: **medium.**

### Increment 4: hooks on the Rust hook socket (retire `session_host.py`)

Bind the **hook UDS** on the same process-lifetime tonic server so hook `j` commands route through
Rust; confirm beforeLease `j power on` succeeds while the FSM is in BeforeLease and the host is up
(gate off **and** on); afterLease `j power off` succeeds during teardown with a client Stream still
open (killed only at Releasing). Remove `session_host.py`; `slim_driver_host.py` is the only host.

- **Deliverables:** hook UDS bind on the Rust server; hooks env `JUMPSTARTER_HOST` → Rust hook
  socket; the EndSession-teardown + open-Stream e2e test from the review; remove `session_host.py`.
- **Live verification:** the **hooks** e2e suite (`beforeLease`/`afterLease` with `j power on`/`off`)
  passes, including the known-flaky afterLease-teardown spec as a differential target; a test where
  the client opens a Stream, calls EndSession, the afterLease hook runs `j power off` while the Stream
  is open, asserting the hook DriverCall succeeds and the Stream is torn down only at Releasing. e2e
  `hooks||dut-network||direct-listener` at the 18/19 baseline; Python pytest green. Risk: **high**
  (full surface + teardown race). Run before inc5.

### Increment 5 (optional, perf): in-process outer-tunnel termination (§6.3-A) + optional Rust marshaling

Shed the loopback hop (duplex-stream tonic incoming yielding one connection per tunnel; assert N
concurrent client connections per lease). Optionally move Value marshaling Rust-side
(`rust/jumpstarter-protocol/src/value.rs` is ready, `value_golden.rs`) — **only** behind a benchmark.
Pure optimization; no wire change. Risk: **low-medium**, deferrable.

### Increment 6 (optional, future): per-leaf / native hosts

Only if true fault isolation or non-Python hosts are needed: split the tree across hosts. This
**requires** first solving (a) Proxy cross-process dispatch (resolve Proxy in Rust at tree-build,
pointing the proxy's duplicate uuid at the **target leaf's** host channel; never spawn a host for a
Proxy/Composite node), (b) per-leaf GetReport aggregation in Rust from per-leaf single-driver reports
(re-introducing the splice logic Rust avoids in inc0–inc5), (c) N-stream LogStream aggregation with a
shared/global ring budget, (d) per-host crash→lease-fail wiring so afterLease `j power off` never
silently fails on a dead host, and (e) cross-driver resource-handle narrowing. Each is a blocker the
single-host model dissolves; inc6 reintroduces them and is therefore explicitly **out of the cutover
path**. Risk: **high**; do not start without the §11 OQ1/OQ2 sign-offs and the per-leaf round-trip
e2e (a DriverCall through a Proxy to a sibling leaf, not just GetReport parity).

---

## 8. Parity checklist (freeze these)

- `GetReport`: host emits pre-order, Proxy full-subtree splice, root no `jumpstarter.dev/name`,
  `client`/`name` not overridable, absent `parent_uuid`/`description` (not present-empty) — copied
  verbatim from the host (`base.py:202-230`, `composite/driver.py:42-48`). Envelope uuid/labels from
  config; `alternative_endpoints` empty (`jumpstarter.proto:155`).
- `DriverCall` exceptions: `NotImplementedError→UNIMPLEMENTED`, `ValueError→INVALID_ARGUMENT`,
  `TimeoutError→DEADLINE_EXCEEDED`, else `UNKNOWN`; method-missing/unmarked `NOT_FOUND` — produced by
  the host, relayed unchanged (`base.py:131-138,359-374`).
- Unknown/malformed uuid (DriverCall **and** Stream): `UNKNOWN` by default (`session.py:308,317,319`).
- `Reset` → `UNIMPLEMENTED` (`jumpstarter.proto:142`).
- `LogStream`: 256 ring, drop-oldest, not status-gated, `uuid=""` present, `source` present
  (`session.py:331-337`).
- `GetStatus`: monotonic `status_version` on every update; `previous` only on change; `message`
  present (`session.py:339-379`); answerable idle between leases.
- `EndSession`: immediate `success=true`; `success=false`/`"No active lease context"` idle; afterLease
  runs regardless of client state; answerable idle (`session.py:381-420`).
- Inner Stream: client `initial_metadata()` answered before any frame; relay only `resource` +
  `x_jmp_accept_encoding` (resource) / empty (driver); held open until client hangup; ABORTED
  `"RouterStream: aclose"` on teardown; frame boundaries 1:1; zero-length DATA passthrough; bounded(32)
  backpressure; `x_jmp_accept_encoding` relayed verbatim incl. `""` (`session.py:316-329`,
  `streams/router.py:47-69`, `base.py:189-198`, `core.py:433,450-452`).
- Driver-call status gate: no-op by default; `strict_driver_gate` opt-in, FSM-phase-sourced.
- Inter-lease: graceful routing stop (grace 1.0 + 0.1s), await host exit, UDS cleanup, then
  `INTER_LEASE_SETTLE` 0.2s (`exporter.rs:41`, `session.py:130-136`).

---

## 9. Summary of resolved review blockers

| Blocker | Resolution |
|---|---|
| Proxy cross-process dispatch | One host owns the whole tree; every uuid (incl. Proxy duplicates) routes to it. Per-leaf split deferred to inc6 with explicit prerequisites. |
| Proxy full-subtree GetReport / optional-field presence | Rust copies the host's full-tree `reports` verbatim; never re-emits per-node reports. |
| Throwaway-vs-lease UUID identity | Registration uses a throwaway host (UUIDs discarded); the per-lease `report`+`drivers` are rebuilt from the live host every lease. |
| Spawn-in-BeforeLease vs LEASE_READY deadlock | Host spawn + routing build + bind complete in `Starting`, before BeforeLease; the hook socket is routable when `j power on` runs. |
| Hook-socket gate inversion | Proxied DriverCall is gated only by the Rust FSM phase; host stays permanently `LEASE_READY`. |
| Inner-Stream initial-metadata handshake / metadata-not-in-frames / deadlock | `tunnel.rs` is a gRPC proxy: eagerly open host Stream, relay host `response.metadata()` before any frame, then relay frames. |
| Early-close on host EOF / clean-close-vs-ABORTED | Inner Stream held open until client hangup; ABORTED `"RouterStream: aclose"` on teardown. |
| Frame re-chunking / backpressure | Message-by-message relay, frame boundaries 1:1, bounded(32) channel. |
| EndSession idle reachability / GetStatus idle | Process-lifetime tonic server; only routing state is per-lease. |
| EndSession teardown vs in-flight streams / `has_client` | afterLease forces `has_client`, runs while host alive; host dropped at Releasing after controller `end`. |
| LogStream N-stream fault isolation / shared 256 budget | One host ⇒ one supervised pump, single 256 ring (no multi-driver budget contention). |
| Inter-lease settle / SIGKILL of N hosts | Graceful routing stop + await single host exit + UDS cleanup + settle. |
| Resource Stream + DriverCall overlap | Same host process; Stream-proxy lifetime decoupled from DriverCall. |
