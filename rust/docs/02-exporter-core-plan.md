# Rust exporter core (Phase B) — implementation plan

Produced by the `map-exporter-core` workflow (6 parallel source-readers over the
Python exporter subsystem + spec docs 03/09, then synthesis); all claims verified
against current source. Working plan for the `jumpstarter-exporter` crate;
cross-reference specs 02 (gRPC), 03 (exporter FSM), 05 (driver model), 06 (streams).

All verified against source. Here is the implementation-ready plan.

---

# `jumpstarter-exporter` Crate — Phase B Implementation Plan

**Verification basis (this session):** Phase-A Rust client confirmed at `rust/jumpstarter-client/src/{router.rs,channel.rs,transport.rs,service.rs}` and `rust/jumpstarter-protocol/src/{router.rs,lib.rs}`. The protocol crate builds **both** server and client stubs (`build_server(true)` at `rust/jumpstarter-protocol/build.rs`), so `ExporterServiceServer`/`RouterServiceServer` and the `ControllerServiceClient` are all available. A Python UDS entry point that serves the full `ExporterService`+`RouterService` from a config alias **already exists**: `ExporterConfigV1Alpha1.serve_unix_async()` (`python/packages/jumpstarter/jumpstarter/config/exporter.py:310-325`). Exporter `uuid` is a local `uuid4()` default (`common/metadata.py:9`), confirming the "ignore `RegisterResponse.uuid`" rule.

---

## 1. WIRE CONTRACT (controller / router / client-facing MUSTs)

### 1.1 Startup ordering (load-bearing, verbatim)
`GetReport (local UDS) → Register(labels, reports) (remote) → ReportStatus(AVAILABLE) → open Status stream`. The Python `serve()` does a complete throwaway registration first: `async with self.session(): pass` (`exporter.py:786-787`), whose only effect is `_register_with_controller` (`exporter.py:311-336`). Then it opens the Status stream (`exporter.py:791-800`).

- `GetReport` request type is `google.protobuf.Empty`; only `response.reports` is consumed (`exporter.py:318-319`, proto `jumpstarter.proto:134`).
- `Register(RegisterRequest{labels, reports})` — **`RegisterResponse` is fully discarded**; identity stays the local `uuid4()` (`exporter.py:324-329`; `common/metadata.py:9`). Rust MUST NOT wire `RegisterResponse.uuid` into identity.
- After Register: `_registered=True`; if no lease context, `ReportStatus(AVAILABLE, "Exporter registered and available")` (`exporter.py:330-336`).

### 1.2 ReportStatus is non-fatal + the status enum
- `ReportStatus(ReportStatusRequest{status=<int>, message, release_lease?})`. `status.to_proto()` is the raw `IntEnum` value (`common/enums.py:43-45`).
- **UNIMPLEMENTED = capability probe**: log-and-continue; all other errors swallowed (`exporter.py:360-366`). Rust SHOULD latch a "skip ReportStatus" flag on first UNIMPLEMENTED to avoid log spam.
- `release_lease=true` is paired with `status=AVAILABLE` to actively drop a lease (`exporter.py:392-398`).
- **Exact enum (frozen contract):** `UNSPECIFIED=0, OFFLINE=1, AVAILABLE=2, BEFORE_LEASE_HOOK=3, LEASE_READY=4, AFTER_LEASE_HOOK=5, BEFORE_LEASE_HOOK_FAILED=6, AFTER_LEASE_HOOK_FAILED=7` (`common/enums.py:8-45`; spec `03-exporter-runtime-fsm.md:30`). The controller scheduler only assigns leases to `AVAILABLE`/`UNSPECIFIED`/empty — a wrong projection wedges scheduling.

### 1.3 Status & Listen streams + retry
- `Status(StatusRequest{})` server-stream, process-lifetime, into a **5-slot** buffer (`exporter.py:299-309,789`). Consumed fields: `leased` (bool), `lease_name` (str), `client_name` (str) (`exporter.py:803-822`).
- `Listen(ListenRequest{lease_name})` server-stream, **per-lease**, into a **10-slot** buffer (`exporter.py:287-297,682`). Each `ListenResponse{router_endpoint, router_token}` → one bridge.
- Both wrapped in `_retry_stream`: **retries=5, backoff=1.0s**, transient fast-path **0.5s** for `"Stream removed"`/`"UNAVAILABLE"`, counter reset after first item, **reconnect even on clean close** (`exporter.py:237-285`). The open Status stream is the controller's liveness signal (lastSeen refresh ~10s, offline after ~1min) — keep it open and reconnect promptly (spec `03:248`).

### 1.4 Router byte-bridge (exporter side — mirror of Phase A)
For each `ListenResponse`: dial the session **MAIN** UDS, open `RouterService.Stream` to `router_endpoint` with per-stream `router_token` + endpoint TLS, and pump bytes bidirectionally (`exporter.py:446-474`). Framing identical to Phase A: `DATA`→payload, `GOAWAY`→EOF, `PING`→noop, EOF→`GOAWAY` (`streams/router.py:31-69`; already implemented in `rust/jumpstarter-protocol/src/router.rs` + `rust/jumpstarter-client/src/router.rs`).

### 1.5 Lease lifecycle wire-visible behaviors
- **One lease at a time**, gated by `_lease_context is None AND lease_name != "" AND leased` (`exporter.py:807-817`).
- **Suppress LEASE_READY** if lease ended during the beforeLease hook → go BEFORE_LEASE→ENDING, not →READY (spec `03:314,540`).
- **Exactly one afterLease** per lease regardless of trigger (EndSession RPC / controller lease-end / shutdown), run while session sockets still serve (spec `03:434,438`).
- **Stale-release guard**: before exporter-initiated `ReportStatus(release_lease=true)`, skip if `lease_ended` already set; always set `lease_ended` locally afterward (controller may not echo `leased=false`) (`exporter.py:368-409`; spec `03:330,345`).
- `HOOK_WARNING_PREFIX = "[HOOK_WARNING] "` byte-for-byte in warn-mode messages (`common/__init__.py:12`).
- **onFailure:exit** double-report: `*_FAILED` then `OFFLINE`, then deferred `shutdown(exit_code=1)` (spec `03:316,329,482-484`).

### 1.6 Session RPC surface (served on the MAIN/HOOK UDS)
`GetReport, DriverCall, StreamingDriverCall, LogStream, Reset, GetStatus, EndSession` + `RouterService.Stream` on the same server (`session.py:95-96,122-123`; proto `jumpstarter.proto:132-149`).
- `GetStatus` → `{status, message, status_version (monotonic), previous_status?}` (`session.py:370-379`).
- `EndSession` → immediate `success=true` after setting `end_session_requested`; `success=false "No active lease context"` if none (`session.py:381-420`).
- `Reset` → `UNIMPLEMENTED` (declared, never overridden) (proto `jumpstarter.proto:142`).
- `LogStream` → drains a `deque(maxlen=256)` on a **0.05s** poll; `LogStreamResponse{uuid="", severity=levelname, message, source}` (`session.py:331-337`; `logging.py:44-51`).
- **Driver-call status gate is a NO-OP on main** (un-awaited `context.abort`) — driver calls are never actually rejected on status (`session.py:268-292`). The real driver-call window is enforced controller-side via the Dial allow-list (spec `03:508`).

### 1.7 Unregister + dual socket
- Unregister: `move_on_after(10)` → `ReportStatus(OFFLINE,"Exporter shutting down")` → `Unregister(reason="Exporter shutdown")`, errors never re-raised, channel close shielded; only if `_registered AND _unregister` (`exporter.py:411-433`; spec `03:182,191`).
- **Dual UDS** (main = client/router-bridge, hook = `j` commands) prevents SSL frame corruption; the bridge always dials **main** (`session.py:243-257`; `exporter.py:686-696`). Graceful stop = `grace=1.0` then sleep `0.1s` (`session.py:133-136`).

**Key constants:** Listen buffer 10, Status buffer 5, byte-pipe 32/direction, retry 5×1.0s (0.5s transient), inter-lease settle 0.2s, unregister 10s, hook timeout 120s, cleanup safety wait 15s (or `before_lease.timeout+30`), post-afterLease grace 1.0s, LogStream poll 0.05s/deque-256.

---

## 2. DRIVER-HOST BOUNDARY

**Only four seams need Python** (spec `03:567`): (1) per-session driver-tree instantiation; (2) `reset()`/`close()` lifecycle; (3) `enumerate()`/`report()` for GetReport; (4) call-dispatch-by-UUID (`DriverCall`/`StreamingDriverCall`/`Stream`). Everything else — registration, Status/Listen consumption + retry, the lease FSM, status projection, hooks orchestration, the router byte-bridge, dual-socket serving, supervisor, standalone — the Rust core owns.

### Option (a): spawn the EXISTING Python session server — **RECOMMENDED**

An entry point already exists. `ExporterConfigV1Alpha1.serve_unix_async()` (`config/exporter.py:310-325`) builds a `Session` from the config's `export` tree (wrapped in `Composite`), serves the full `ExporterService` + `RouterService` on a `TemporarySocket` UDS, and forces status `LEASE_READY`. The Rust core would:
1. Spawn a small wrapper around this (see caveats) that prints/handshakes its UDS path(s) on stdout.
2. Connect a **local UDS gRPC channel** to it for `GetReport` (the registration payload) and as the **bridge target** for `RouterService.Stream` — the *exact* mirror of Phase A, where `transport.rs` dials a UDS and `router.rs` bridges it. Here the direction is router↔session-UDS instead of client↔router, but the Phase-A `router::bridge` byte pump and frame classifier are reused unchanged.

**Caveats that make `serve_unix_async()` not quite drop-in (must be addressed):**
- It serves a **single** socket via `serve_unix_async`, not the dual main+hook split; it forces `LEASE_READY` and has **no lease/hook/status state machine** (it's the "local usage" path). For the FIRST INCREMENT (no hooks, single lease) that is exactly what we want. For full hook support the host must instead drive `Session.serve_unix_with_hook_socket_async()` (`session.py:244-257`) and expose `update_status`.
- A per-lease **fresh** `Session`/driver tree is required (`device_factory()` per lease) (spec `03:567`). The host process must (re)instantiate the Session per lease, or the Rust core spawns a fresh host subprocess per lease.

### Option (b): a new thin Python driver-host

A purpose-built `jumpstarter-driver-host` module (≈100 lines) that: loads `ExporterConfig`, on a control message (stdin/JSON-RPC or a tiny control UDS) creates a fresh `Session` per lease, serves main+hook UDS via `serve_unix_with_hook_socket_async`, accepts `update_status(status, message)` calls from Rust, and emits status/log events back. Reuses `Session` and the `Composite` instantiation verbatim — only the *orchestration shim* is new.

### Recommendation: **(a) for the FIRST INCREMENT, evolving into (b)**

Start with (a) verbatim — `serve_unix_async()` already gives a registerable, dialable session with zero new Python code, which is the smallest path to an end-to-end live test and directly reuses the Phase-A UDS+bridge machinery. Then graduate to (b)'s thin host to add: the dual main/hook socket, Rust-driven `update_status` (so the Rust FSM owns the wire status projection, §1.2/§4), and per-lease re-instantiation. (b) is strictly an additive shim over the same `Session` class — no driver code changes, preserving the "Python driver ecosystem unchanged" strategy. Owning `update_status` from Rust is the deciding factor: the wire `ExporterStatus` projection (the thing that wedges the scheduler) must live in the Rust FSM, and `serve_unix_async` hardcodes `LEASE_READY`, so we cannot stay on (a) for hooks/leases.

---

## 3. MODULE DECOMPOSITION (`rust/jumpstarter-exporter/`)

Add `jumpstarter-exporter` to `rust/Cargo.toml` members (`rust/Cargo.toml:12-17`). Depends on `jumpstarter-protocol`, `jumpstarter-config`, and a small shared-transport extraction from `jumpstarter-client`.

| Module | Responsibility | Phase-A reuse |
|---|---|---|
| `channel.rs` | Controller channel (auth interceptor: bearer + `jumpstarter-kind=Exporter`/namespace/name) + per-call fresh-channel semantics | **Reuse** `jumpstarter-client/src/channel.rs` `AuthInterceptor`/`connect_router`/`is_insecure` — generalize the role string ("Client"→"Exporter"); promote to a shared `jumpstarter-transport` crate or `pub use` |
| `registration.rs` | §1.1 startup sequence: local-UDS `GetReport` → `Register` → `ReportStatus(AVAILABLE)`; unregister (10s) | new; `ControllerServiceClient` from protocol crate |
| `controller_stream.rs` | `Status` + `Listen` consumption with `_retry_stream` semantics (5×1.0s, 0.5s transient, reset-on-data, reconnect-on-clean-close) into bounded `mpsc` (5 / 10) | new (small) |
| `lease_fsm.rs` | The `LeasePhase` enum + `allowed(from,to)` exhaustive match + two wait points + `ExporterStatus` projection (§4) | new; port of `impl-jep-0012-lease_lifecycle.py` |
| `lease.rs` | Per-lease driver: spawn/own the driver-host session, run Listen consumer + per-`ListenResponse` bridge tasks, drive the FSM, exactly-once afterLease, stale-release guard | new; two-level task-group structure |
| `bridge.rs` | router↔session-MAIN-UDS byte bridge | **Reuse** `jumpstarter-client/src/router.rs::bridge` + `jumpstarter-protocol/src/router.rs` (frames/classify) **unchanged** |
| `driver_host.rs` | Spawn/manage the Python host (option a/b), expose UDS path(s), `update_status`, lifecycle; `GetReport` proxy | new; `tokio::process` |
| `hooks.rs` | beforeLease/afterLease subprocess execution: PTY/line-buffered, env (`JUMPSTARTER_HOST`=hook socket, `JMP_DRIVERS_ALLOW=UNSAFE`), setsid+close_fds, timeout-race, onFailure modes, log routing | new (largest); `hooks.py` |
| `supervisor.rs` | fork+setsid restart loop, signal forwarding (`killpg`), zombie reaping (PID-1), rapid-failure breaker (5/60s), exit-code contract (128+sig / exit_code / 0) | new; `nix` crate |
| `standalone.rs` | `serve_standalone_tcp`: single `"standalone"` session over TCP(+TLS)+temp-hook-UDS, beforeLease-once→LEASE_READY, park until stop; passphrase interceptor (`x-jumpstarter-passphrase`, constant-time) | new; tonic server + `subtle` |
| `cli` (in `jumpstarter-cli`) | `jmp run` subcommand: flag validation, listener parsing, invoke supervisor | extend `rust/jumpstarter-cli/src/main.rs` |

**Recommended pre-step:** extract `channel.rs` + `router.rs` (bridge) + `insecure.rs` from `jumpstarter-client` into a shared `jumpstarter-transport` crate so both client and exporter consume one copy (the bridge is needed verbatim in both directions).

---

## 4. FSM

### 4.1 Enum + exhaustive transition table (ported verbatim from `impl-jep-0012-lease_lifecycle.py:11-44`)

```rust
enum LeasePhase { Created, Starting, BeforeLease, Ready, Ending, AfterLease, Releasing, Done, Failed }

fn allowed(from: LeasePhase, to: LeasePhase) -> bool {
    use LeasePhase::*;
    matches!((from, to),
        (Created,     Starting) | (Created,     Failed)
      | (Starting,    BeforeLease) | (Starting, Ready) | (Starting, Ending) | (Starting, Failed)
      | (BeforeLease, Ready) | (BeforeLease, Ending) | (BeforeLease, Failed)
      | (Ready,       Ending) | (Ready, Failed)
      | (Ending,      AfterLease) | (Ending, Releasing) | (Ending, Done) | (Ending, Failed)
      | (AfterLease,  Releasing) | (AfterLease, Failed)
      | (Releasing,   Done) | (Releasing, Failed)
    ) // Done, Failed terminal
}
```

**Wait points (exactly two)** — port `impl:78-83`: `ready` signal set on entry to `Ready | Done | Failed` (terminal states also unblock `wait_ready` — the anti-deadlock measure); `complete` signal set on entry to `Done | Failed` only. Replaces main's overloaded `before_lease_hook`/`after_lease_hook_done` events. Use `tokio::sync::Notify` or `watch`.

**`request_end()` policy** — port `impl:85-90` but fix the CREATED split (see §4.3): always set the single `end` signal; if `Ready`→`Ending`; if `BeforeLease | Starting` defer (record `end_requested`, transition on hook/startup completion).

**Orthogonal flags OFF the enum** (separate fields): `has_client`, `stop_requested`, `skip_after_lease` (set only from a beforeLease `onFailure:exit`) — `impl:50-51,65-70`.

### 4.2 ExporterStatus projection (derived, never stored in the enum)

| Lifecycle point | ReportStatus | Message |
|---|---|---|
| After Register (unleased) | `AVAILABLE(2)` | `"Exporter registered and available"` |
| Enter BeforeLease | `BEFORE_LEASE_HOOK(3)` | `"Running beforeLease hook"` |
| Enter Ready | `LEASE_READY(4)` | `"Ready for commands"` (or `LEASE_READY` + `[HOOK_WARNING] beforeLease hook warning: {msg}`) |
| Enter AfterLease | `AFTER_LEASE_HOOK(5)` | `"Running afterLease hooks"` |
| afterLease success | `AVAILABLE(2)` | `"Available for new lease"` (or `+[HOOK_WARNING] afterLease hook warning: {msg}`) |
| Failed (before phase) | `BEFORE_LEASE_HOOK_FAILED(6)` | hook failure msg |
| Failed (after phase) | `AFTER_LEASE_HOOK_FAILED(7)` | hook failure msg |
| onFailure:exit | `*_FAILED` **then** `OFFLINE(1)` (double-report) | — |
| Shutdown | `OFFLINE(1)` | `"Exporter shutting down"` |

`Failed` carries the failing phase so projection picks 6 vs 7 (spec `03:556`). Freeze the literal messages in §4.2 as the contract pending Q9.

### 4.3 Resolved decisions for the spec's flagged gaps

- **Q3 Next-lease gating → STRICT.** No new lease until the previous lifecycle reaches `Done`; drive the next lease off `Done`, **not** a `previous_leased` edge. Eliminates main's back-to-back-lease wedge (spec `03:555,581`).
- **Q4 Driver-call window → replicate the NO-OP, behind a config flag.** Ship ungated (matching main's actual behavior); add `strict_driver_gate: bool` (default false) that, when set, rejects calls outside `{Ready, BeforeLease, AfterLease}` with `FAILED_PRECONDITION` (spec `03:550,582`).
- **FAILED→status projection → FREEZE main's mapping** incl. the FAILED-then-OFFLINE double-report (§4.2) (spec `03:556,559`).
- **End-during-STARTING → defer** (treat like BeforeLease: record `end_requested`, transition to `Ending` once startup completes). One explicit policy; avoids main's immediate `conn_tg` cancel race (spec `03:552`).
- **CREATED `request_end` split → single end signal.** Always set the one `end` event; never let `is_end_requested()` and the flag disagree (spec `03:554`).
- **Q1 Clean-close reconnect → add small jitter/backoff** on clean Status/Listen close (avoid main's hot-loop risk at `exporter.py:284-285`) (spec `03:501,579`).
- **Drop dead code:** `Session._status_update_event`, `LeaseContext.drivers_ready()/wait_for_drivers()/clear_client()` (spec `03:269,512,583`).

---

## 5. FIRST INCREMENT (smallest end-to-end-verifiable milestone)

**Goal:** Rust `jmp run --exporter <alias>` registers with a live Go controller, serves **one** lease by spawning the Python session over a UDS and bridging the router, so an unmodified Python client can `DriverCall` (e.g. `MockPower.on()`) through it. **No hooks, no supervisor fork, no standalone, no FSM beyond Created→Starting→Ready→Ending→Done.**

### Scope (thin vertical slice)
1. **`channel.rs`** — reuse `jumpstarter-client` `AuthInterceptor` with role `"Exporter"`; build the controller channel from `ExporterConfig` (already parsed: `rust/jumpstarter-config/src/exporter.rs` has `endpoint`/`token`/`tls`/`metadata`/`export`).
2. **`driver_host.rs`** — spawn `python -m ...` invoking `ExporterConfigV1Alpha1.serve_unix_async()` (option a, `config/exporter.py:310`); print the UDS path on stdout; Rust reads it.
3. **`registration.rs`** — local-UDS `GetReport(Empty)` → `Register(labels, reports)` → `ReportStatus(AVAILABLE)` (`exporter.py:311-336`); discard `RegisterResponse`.
4. **`controller_stream.rs`** — consume `Status` (5-buf, retry 5×1.0s); on `leased && lease_name != "" && no current lease`, start one lease.
5. **`lease.rs` + `bridge.rs`** — open `Listen(lease_name)` (10-buf); per `ListenResponse`, dial the host UDS and run `router::bridge` (reused). Report `LEASE_READY` once Listen is established (no-hook path, `exporter.py:756-761`).
6. On `leased=false`: clear lease, set `Done`, `sleep(0.2)`. On SIGINT: `ReportStatus(OFFLINE)` → `Unregister` (10s) → exit.

### Live-cluster test plan (mirrors Phase A verification)
```bash
# 0. Live Go controller reachable; admin context.
# 1. Create exporter + config alias with a MockPower driver
jmp admin create exporter rust-exp-1 --label board=mock --save \
    --oneshot   # or hand-write ~/.config/jumpstarter/exporters/rust-exp-1.yaml:
#   export:
#     power: { type: jumpstarter_driver_power.driver.MockPower }

# 2. Start the Rust exporter (THIS crate)
cargo run -p jumpstarter-cli -- run --exporter rust-exp-1
#   EXPECT: registers; controller shows exporter Online/Available;
#           Status stream stays open.

# 3. In another shell, drive it with an UNMODIFIED Python client
jmp create lease --selector board=mock --duration 1m   # or `jmp shell --selector board=mock`
python -c "
from jumpstarter.client import client_from_path  # standard client
# acquire lease, dial, then: power.on(); print(power.read())
"
#   EXPECT: client's DriverCall(power.on) tunnels client->router->Rust bridge
#           ->Python session UDS->MockPower.on(); returns OK.

# 4. Release the lease -> Rust logs Done, sleeps 0.2s, ready for next.
# 5. Ctrl-C the Rust exporter -> ReportStatus(OFFLINE)+Unregister; controller marks offline.
```

**Pass criteria:** (1) exporter appears Available in the controller; (2) a Python client completes a real `DriverCall` round-trip through the Rust bridge; (3) clean lease release returns the exporter to Available; (4) Ctrl-C unregisters within 10s. This is the exact shape of the Phase-A client verification (admin-created resource + MockPower + a real peer driving it across a live controller+router).

**Deliberately deferred to increment 2+:** hooks (`hooks.rs` + dual socket + option (b) host), supervisor fork/restart/breaker, standalone TCP + passphrase, the strict-gate config flag, FSM `Failed`/`Degraded` paths.

---

## 6. RISKS & DE-RISKING

1. **Deadlock-fix timing erased by the FSM rewrite.** Main relies on three force-set sites for `before_lease_hook` (handle_lease finally, cleanup safety timeout, hook-runner finally) (spec `03:323,333,551`). The JEP-0012 FSM replaces these with terminal-state wait-point unblocking (`Ready`/`Done`/`Failed` all set `ready`). **De-risk:** implement the two-wait-point rule from `impl:78-83` exactly (terminal states unblock `wait_ready`), keep `HookExecutor` a pure executor (DD-4: exporter applies transitions, hook never sets completion) (spec `03 JEP-0012:137-147`), and unit-test the "end-during-beforeLease" path (BeforeLease→Ending, LEASE_READY suppressed) before any live test.

2. **Session-teardown "SSL corruption" sleeps.** The `0.2s` inter-lease settle and `grace=1.0 + 0.1s` stop exist to avoid overlapping sessions corrupting SSL frames (`exporter.py:853-855`; `session.py:133-136`). **De-risk:** under the strict "no new lease until Done" gate (§4.3 Q3), overlapping sessions are structurally impossible, so these MAY be dropped (spec `03:571`) — but **keep them initially** and only remove once the no-overlap invariant is proven by a back-to-back-lease soak test. Keep the post-afterLease `1.0s` client-poll grace regardless (papers over a client poll race, spec `03:571`).

3. **Status-gate quirk: keep-or-fix.** Main's `_check_status_for_driver_call` `context.abort` is **un-awaited → no-op**; driver calls are never rejected on status (`session.py:268-292`). A strict Rust gate is a behavior change. **De-risk:** with option (a)/(b), the Python `Session` keeps owning the (no-op) gate, so we inherit main's behavior for free in the FIRST INCREMENT. Only if the Rust core later terminates the client gRPC end itself do we re-decide — and then behind the `strict_driver_gate` flag (§4.3 Q4) so the default stays bug-for-bug compatible.

4. **Hook process groups escaping the supervisor.** Hooks `setsid` into their own group (`hooks.py:306-307`) and escape the supervisor's `killpg`, so a hard kill of the runtime can orphan hook subprocesses (spec `03:573,586`, Q8). **De-risk:** in `supervisor.rs`/`hooks.rs`, track every hook child PID/pgid explicitly and kill the group on lease-end/abnormal-exit; on Linux set `PR_SET_PDEATHSIG`, on macOS use a kqueue `PROC_EXIT` watch. Track the child **pgid explicitly** rather than reusing the PID as main does (`exporter` supervisor relies on `pgid==pid` only because of setsid — spec supervisor open question). This is deferred to increment 2 but must be designed in `supervisor.rs` from the start (the fork+setsid+`killpg` skeleton).

5. **(Bonus) Driver-host process lifecycle / fresh-per-lease.** Authors rely on `__init__`/`reset`/`close` running per lease (spec `03:567`). **De-risk:** make `driver_host.rs` spawn a **fresh** Python session per lease (or send a per-lease "new session" control message in option (b)); never reuse one host process across leases in a way that skips `device_factory()` re-instantiation. Verify with a MockPower driver whose `reset()` increments an observable counter.