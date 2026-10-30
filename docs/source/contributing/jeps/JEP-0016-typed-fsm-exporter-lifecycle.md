# JEP-0016: Typed FSM Runner and Exporter Lifecycle

| Field | Value |
| --- | --- |
| **JEP** | 0016 |
| **Title** | Typed FSM Runner and Exporter Lifecycle |
| **Author(s)** | @mangelajo (Miguel Angel Ajo Pelayo <miguelangel@ajo.es>) |
| **Status** | Discussion |
| **Type** | Standards Track |
| **Created** | 2026-10-26 |
| **Updated** | 2026-10-28 |
| **Discussion** | https://github.com/jumpstarter-dev/jumpstarter/pull/1250 |
| **Requires** | - |
| **Supersedes** | - |
| **Superseded-By** | - |

## Abstract

This JEP proposes a small, typed finite state machine (FSM) runner for Jumpstarter
and its use to make the exporter lifecycle explicit in controller and standalone
modes. One task
executes named states sequentially, while background tasks publish observations
and requests without performing transitions. A state awaits its activity and
returns a successor, returns `None` to let the runner wait for changed inputs, or
returns `ExitFSM` to terminate. The accompanying implementation first establishes
the reusable `jumpstarter.fsm` foundation, then migrates both exporter modes to
the states in `jumpstarter.exporter.fsm`.

## Motivation

The exporter already has an implicit lifecycle spread across `Exporter`,
`LeaseContext`, hook orchestration wrappers, session RPCs, and background tasks.
Understanding when hooks run, which task owns a lease, and when resources are
actually closed requires following flags, events, callbacks, and completion
messages across those components.

Several cases make this more than a readability problem:

- The controller can revoke or replace an assignment while a hook is running.
  Starting a replacement before old-session teardown completes risks overlapping
  ownership and access to the same hardware.
- A client's `EndSession` request finishes its hook phase, but does not release
  the controller's reservation or immediately close its status/log transport.
- Shutdown intent can arrive during any activity and may escalate from draining
  a lease to stopping locally without waiting for controller release.
- Reported status is not the complete lifecycle: a session can report
  `AVAILABLE` while retaining its reservation and final logs.

Exporter maintainers need to see these decisions together, and operators need
to identify the current phase and slow or stuck activities. Other Jumpstarter
components can reuse the same small runner without inheriting exporter-specific
lease or shutdown policy.

## Proposal

### Shared runner

`jumpstarter.fsm` provides four domain-independent building blocks:

```python
from contextlib import AsyncExitStack


class InputSource[InputsT](Protocol):
    def snapshot_and_watch(self) -> tuple[InputsT, Event]: ...


class State[InputsT](Protocol):
    async def run(
        self, inputs: InputsT, resources: AsyncExitStack
    ) -> State[InputsT] | ExitFSM | None: ...


@dataclass(frozen=True)
class ExitFSM:
    """Stop evaluating states; the runner unwinds remaining resources before exit."""
```

The fourth building block, `FSMRunner[InputsT]`, owns the current state, the
input wait, transition scheduling, and one `AsyncExitStack` spanning its run:

```python
from jumpstarter.fsm import FSMRunner

runner = FSMRunner(initial_state, inputs, name="exporter")
tg.start_soon(runner.run)
current = runner.current_state
```

- A successor runs immediately with a fresh snapshot. No extra notification or
  completion event is necessary. The same resource stack is passed to every state.
- Before each evaluation, an AnyIO checkpoint delivers cancellation and lets
  input producers run. Capture the snapshot afterward: awaiting a state alone
  need not yield, so immediate transition chains could otherwise starve both.
- `None` ends an evaluation, not the logical state. The runner waits for changed
  inputs and then evaluates that same state again.
- `ExitFSM()` stops state evaluation. The runner unwinds remaining registered
  resources before returning successfully; the marker has no activity of its own.
  It is also a valid initial value. Normal domain cleanup still belongs in states.
- Activity states await hooks or other concrete operations and return a
  successor. Input updates cannot rerun or interrupt the activity by themselves.
- Waiting states inspect one snapshot and return a decision. They contain no
  input-wait loops and must not repeat one-time side effects on reevaluation.
- States acquire resources when needed, register contexts with the supplied
  stack, and carry live references to successors. They decide normal release;
  the runner's stack supplies fallback unwinding on exit, error, or cancellation.
  Returning a successor or `None` does not close the stack.

```{mermaid}
flowchart TD
    Open["Runner opens AsyncExitStack"] --> Checkpoint["Cancellation and scheduling checkpoint"]
    Checkpoint --> Capture["Capture snapshot and change event"]
    Capture --> Run["await state.run(inputs, resources)"]
    Run --> Result{"Returned value"}
    Result -->|None| Wait["Await captured event; retain current state"]
    Wait --> Checkpoint
    Result -->|Successor| Transition["Set current state to successor"]
    Transition --> Checkpoint
    Result -->|ExitFSM| Unwind["Unwind remaining resources"]
    Run -->|exception or cancellation| Unwind
    Wait -->|cancellation| Unwind
    Checkpoint -->|cancellation| Unwind
    Unwind --> Exit["Return on successful exit; otherwise propagate failure"]
```

### Exporter specialization

`jumpstarter.exporter.fsm` supplies immutable `Inputs` containing the latest
`LeaseAssignment` and shutdown request. `InputBuffer` coalesces publications
(`publish_lease_assignment`, `request_shutdown`) and wakes the runner; it
satisfies `InputSource[Inputs]` structurally. The `State` alias specializes the
shared protocol for those inputs:

```python
from jumpstarter.fsm import State as FSMState

type State = FSMState[Inputs]
```

The shared runner knows nothing about leases, gRPC, drivers, or hook policies.
Exporter states retain those decisions in `jumpstarter.exporter.fsm.states`, with
inputs in `jumpstarter.exporter.fsm.input`. No empty exporter runner subclass or
factory is required. State class docstrings stay synchronized with this JEP via
the exporter-fsm-docs-sync project rule.

### Scope and implementation

The implementation in this branch includes the shared foundation and both
exporter modes. Hardware-backed acceptance remains a pre-merge check.

| Reference file | Purpose |
| --- | --- |
| `python/packages/jumpstarter/jumpstarter/fsm.py` | Generic interfaces, named single-use runner, current-state queries, and debug tracing |
| `python/packages/jumpstarter/jumpstarter/fsm_test.py` | An independent boolean-input example and runner behavior checks |
| `python/packages/jumpstarter/jumpstarter/exporter/fsm/__init__.py` | `ExporterFSM` wrapper and public re-exports for the exporter lifecycle package |
| `python/packages/jumpstarter/jumpstarter/exporter/fsm/input.py` | Immutable snapshots, shutdown merge policy, and coalescing input notifications |
| `python/packages/jumpstarter/jumpstarter/exporter/fsm/states.py` | Production lifecycle/failure states; class docstrings are the canonical phase descriptions |
| `python/packages/jumpstarter/jumpstarter/exporter/fsm/lease_context.py` | Live lease references, socket paths, and the EndSession request shared across states |
| `python/packages/jumpstarter/jumpstarter/exporter/fsm_test.py` | Typed demonstration states and exporter-input checks using existing `LeaseContext` objects |
| `python/packages/jumpstarter/jumpstarter/exporter/fsm_lifecycle_test.py` | Production lifecycle checks using real session sockets and controlled controller/hook operations |

The demo does not open real sessions or transports. Its compact `Cleanup` state
stands in for completion. Production states and integration checks implement the
distinct after-hook, reservation wait, and transport-close phases below.
`Exporter.serve()` now runs the lifecycle through `ExporterFSM`, the wrapper in
`jumpstarter.exporter.fsm` that owns the input buffer and the single-use
`FSMRunner`, after registration;
`serve_standalone_tcp()` supplies its TCP/Unix session context to the same states.
The runner is available as `exporter.fsm.runner` for current-state queries in either mode.

All states use `run(inputs, resources)`. On failure the runner passes the exception
to the stack's `__aexit__`, then re-raises it regardless of suppression, allowing
exception-aware resource cleanup without hiding lifecycle failures. Successful
exit closes remaining resources before publishing `ExitFSM`.

Both modes reuse existing hook execution and session transports. Standalone mode
shares hook sequencing and resource ownership with controller mode, removing the
remaining hook-orchestration wrappers and duplicated `LeaseContext` phase flags.

## Design Decisions

### DD-1: Reuse a small typed runner, not a framework

**Alternatives considered:**

1. **Keep the runner exporter-only:** minimize the initial API surface, but tie
   otherwise reusable scheduling and observability to lease code.
2. **Use generic protocols and a small runner:** type snapshots and successors
   while leaving policy and input publication to each component.
3. **Adopt a third-party FSM framework:** gain dispatch/diagram facilities at the
   cost of another dependency and framework-specific state orchestration.

**Decision:** Generic protocols and a runner in `jumpstarter.fsm`.

**Rationale:** The reference needs only the standard library and existing AnyIO.
It supports independently typed input sources without a transition table,
registration API, state persistence, or observer framework.

### DD-2: Keep waiting in the runner and activities in states

**Alternatives considered:**

1. **Wait inside each state:** each state owns a complete wait loop, repeating
   notification handling and exposing scheduling machinery to lifecycle code.
2. **Return decisions to a single waiting runner:** `None` waits, a successor
   advances immediately, and an explicit marker terminates.
3. **Dispatch queued events through handlers/tables:** represent hook completion
   as events and coordinate separate activity tasks.

**Decision:** A single runner owns waiting; states await their own activities.

**Rationale:** State methods show the allowed successors directly. Awaited hook
outcomes select successors without completion events or deferred-event replay.
Concrete return unions make invalid payloads and successors type-checkable.

### DD-3: Retain observations and intent, rather than replay every update

**Alternatives considered:**

1. **Queue every observation/request:** preserve all intermediate assignments but
   accumulate obsolete work while a hook is blocked.
2. **Keep only the latest lease name:** coalesce work, but lose release boundaries
   such as `A → unleased → A`.
3. **Keep latest observations with generations and persistent requests:** coalesce
   obsolete assignments while retaining boundaries and shutdown/EndSession intent.

**Decision:** Latest assignment plus generation, persistent shutdown, and
lease-local EndSession requests, with one coalescing wakeup.

**Rationale:** The lifecycle can close A and admit the latest C without replaying
B. Generations distinguish a replacement A from the original instance. An
EndSession request belongs to its session's lease object, never a replacement.

### DD-4: State-directed resources with a runner-owned exit stack

**Alternatives considered:**

1. **Maintain an active/completed registry in `InputBuffer`:** validate ownership, but
   duplicate facts already represented by the current state.
2. **Carry resource references with manual context entry/exit:** preserve a
   session across phases, but rely on reaching the cleanup state after every
   error. A context opened lexically inside one `run()` closes too early on return.
3. **Register state-acquired resources with a runner-owned `AsyncExitStack`:**
   preserve contexts across state calls, close normally in `ClosingLease`, and
   unwind remaining resources when the runner exits unexpectedly.
4. **Use independent phase tasks and completion messages:** keep a responsive
   dispatcher but introduce another set of lifetime and teardown boundaries.

**Decision:** The runner creates one standard-library exit stack and passes it to
`run(inputs, resources)`. States own acquisition, use, and normal release;
`InputBuffer` only stores inputs and notifies the runner.

**Rationale:** The session survives transitions without a custom resource-owner
framework. A state exception cannot bypass registered resource unwinding merely
because `ClosingLease` was not reached. The old slot is not free until teardown
actually completes. Async cleanup remains the resource context manager's
responsibility; an exit stack does not make arbitrary cleanup cancellation-safe.

### DD-5: Defer ordinary control requests to activity boundaries

**Alternatives considered:**

1. **Cancel the task group immediately:** match the current immediate-stop path,
   but potentially interrupt a hook or cleanup operation.
2. **Retain requests until the awaited activity completes:** honor configured
   timeouts and act on current intent at the next state boundary.

**Decision:** Defer ordinary control requests, with explicit bounded shielding
where activities/cleanup must finish during exporter integration.

**Rationale:** Before/after ordering remains sequential and understandable.
This is a deliberate behavior change, not a claim that current hooks are always
shielded. The generic runner itself does not shield, swallow cancellation, or
define hook timeouts. Forced process termination remains outside this guarantee.

## Design Details

### Current state, tracing, and type checking

`runner.current_state` is read-only and returns the actual state object. It is
the initial object before startup, remains the current state during waits and
activities, and becomes `ExitFSM` on successful termination. Observers must not
mutate it. Exceptions/cancellation propagate and leave the last state queryable;
the runner rejects concurrent or repeated execution.

Retain the last active state while final unwinding runs and publish the terminal
`ExitFSM` state only after successful
closure. A cleanup error must propagate rather than look like successful exit.
Resource context managers must not suppress lifecycle exceptions/cancellation.

DEBUG logs under `jumpstarter.fsm` include the configured runner name and
distinguish logical state entry/exit from individual `run()` evaluations.
Returning `None` does not produce a false exit/reentry. Each evaluation records
its `perf_counter()` duration, including awaited work and aborted evaluations,
but excluding the runner's subsequent input wait.

Exporter inputs log effective publications and wakeups under
`jumpstarter.exporter.fsm`. Logs omit raw snapshots/state representations,
client names, assignment metadata, and shutdown reason text. Wakeup reasons are
short diagnostic labels such as `EndSession`, not user payloads.

Use the existing `ty` checker and narrow concrete successor unions. Protocols
alone do not prove exhaustive transitions, teardown order, or race freedom;
behavioral checks establish those properties.

### Exporter inputs and ownership

| Fact or request | Owner / representation |
| --- | --- |
| Controller lease assignment | Immutable latest `LeaseAssignment` with lease key, client name, and copied read-only string metadata |
| Lease key | Lease name and publisher-assigned local generation |
| Shutdown | Persistent mode, optional exit code, reason, and controller-cleanup policy |
| Active lease/resources | Current state's lease reference, not a global input registry |
| Registered resource lifetime | Runner-owned `AsyncExitStack`, shared across state calls |
| Completed assignment | `Available.last_completed`, updated only after teardown |
| EndSession | Retained `LeaseContext.end_session_requested` boolean on that session's lease |
| Notification | One coalescing AnyIO event, carrying no lifecycle payload |

“Lifecycle owner” means the task executing `FSMRunner.run()` and its current
state's `run()`. It is not another owner/manager class. Producers update facts
and requests, never run transitions or open/close the lifecycle's resources.

Generation advances when leased/unleased state or lease identity changes.
Duplicate observations and client/context-only changes do not advance it.
`A → unleased → A` invalidates the original A even if all observations arrive
during a hook. A stream interruption is not an unleased observation: retain the
last assignment through retries. Invalid assigned-lease names are rejected at
publication; static types do not validate string contents.

The session adapter calls `lease.request_end_session()`, which sets that lease's
boolean and calls `InputBuffer.wake("EndSession")` synchronously, without an
intervening await. Duplicate requests are idempotent. An old session can at most
cause an extra reevaluation; it cannot alter or clear the new lease's request.
Never reuse a lease object for another assignment.

`jumpstarter.exporter.fsm.lease_context.LeaseContext` retains only lease identity, session/socket/client references,
EndSession intent, and a wake callback. It holds no events or lifecycle/status
flags. Hook progress and cleanup eligibility follow from the current state and
its successors; release follows from assignment generations. The session already
owns protocol status, its message, and status-change notification. EndSession
cannot be inferred from a phase: it is external intent that must survive an
activity until a successor handles it.

### Race-safe waiting

The runner captures snapshot and event together, without an await, **before**
calling the current state. A publisher stores its new snapshot, creates the next
notification event, and sets the previous event. Lease-local requests set their
own flag and wake the runner even though the global snapshot is unchanged.

```{mermaid}
sequenceDiagram
    participant Runner
    participant Buffer as InputBuffer
    participant State as Current state
    participant Producer
    Runner->>Buffer: snapshot_and_watch()
    Buffer-->>Runner: inputs, event E1
    Runner->>State: run(inputs, resources)
    Producer->>Buffer: Publish changed observation or wake after lease-local request
    Note over Buffer: Create E2 and set E1
    State-->>Runner: None
    Note over Runner: Waiting on E1 returns immediately
    Runner->>Buffer: snapshot_and_watch()
    Buffer-->>Runner: Latest inputs, event E2
    Runner->>State: run(latest inputs, resources)
```

This is event-loop-only synchronization. Thread callbacks must be marshalled
onto that loop. No timer messages accumulate during a hook. Add a bounded wait
and monotonic deadline only when a concrete waiting state needs them; retries
must retain backoff rather than repeatedly returning to immediate admission.

### Exporter states and resource lifetime

```{mermaid}
stateDiagram-v2
    [*] --> Available
    Available --> BeforeLeaseHook: Assignment admitted and session prepared
    Available --> Stopping: Shutdown requested
    BeforeLeaseHook --> LeaseReady: Success or warning
    LeaseReady --> AfterLeaseHook: Session needs finishing
    AfterLeaseHook --> AwaitingLeaseRelease: Cleanup hook completed
    AwaitingLeaseRelease --> ClosingLease: Released, reassigned, or stop-now
    ClosingLease --> Available: Teardown complete, continue serving
    ClosingLease --> Stopping: Teardown complete, shutdown required
    Stopping --> ExitFSM: Exporter cleanup complete
    ExitFSM --> [*]
```

The diagram shows the normal path; hook failure states retain the lease and
failure outcome and own their policy-specific successors.

- **Available:** honor shutdown before admission. Reject the completed assignment
  key; otherwise prepare the session for the current assignment. Return `None`
  when there is nothing admissible.
- **BeforeLeaseHook:** await the existing execution-only hook method and select
  `LeaseReady` or `BeforeLeaseHookFailed` directly from its outcome. Check the
  latest assignment after session setup before starting Listen and hook work;
  skip both hooks and close if setup completed for a revoked assignment.
- **LeaseReady:** inspect shutdown mode, revocation/reassignment, and this lease's
  EndSession request. Return `None` while normal service continues.
- **AfterLeaseHook:** run eligible cleanup once, then select
  `AwaitingLeaseRelease` or `AfterLeaseHookFailed`.
- **AwaitingLeaseRelease:** retain reservation, status, and final-log access after
  hook completion. Return `None` until controller release/reassignment or
  stop-now; proceed immediately if one already applies.
- **ClosingLease:** await actual connection, session, and driver teardown before
  returning `Available(last_completed=key)` or `Stopping`. Stop and await lease
  I/O tasks, then await `resources.aclose()`; a failed close does not free the slot.
- **Stopping:** publish `OFFLINE`, finish applicable controller unregistration,
  telemetry, and runtime-sidecar shutdown, then return `ExitFSM()`.

`ReleasingLease` is reached only for an explicit `endLease` hook failure. It
preserves the existing one-second failure-observation window, then attempts named
release before local closure. Before-hook `exit`/`endLease` failures bypass the
after-hook; successful or partially executed setup can require cleanup even when
no client name was assigned.

`EndSession` does **not** release the reservation. Ordinary cleanup never requests
controller release. Production lifecycle tests verify that repeated EndSession
requests run cleanup once and preserve the session until release or stop-now.
Hook completion is not transport completion.

The session and hook socket must exist before the before-hook and remain alive
through after-hook execution and any release wait. Register `session_for_lease()`
with the runner-provided stack, keeping context entry/exit in the same lifecycle
task, including exceptional cleanup if setup or a state raises. Listen,
connection bridging, gRPC serving, and log delivery remain concurrent I/O tasks.
Both hook phases retain driver-call access so hooks can use `j`.
Remote Dial requests stay buffered while the session status is `BEFORE_LEASE_HOOK`,
using its existing `_status_update_event` to wait for the published outcome. This
keeps local hook access from accidentally enabling remote driver calls during
setup without a duplicate lease-local event. Failure status is published before
opening that routing gate.

`ExporterStatus` is an output projection, not the entire state set. Both
`Available` and `AwaitingLeaseRelease` may report `AVAILABLE`, but only the former
admits another lease. Centralize status/message publication; existing background
delivery can keep controller RPC retries from stalling the lifecycle. Preserve
warning/failure visibility for polling clients rather than immediately
overwriting a failed state and assuming it was observed.

### Resource acquisition, normal closure, and fallback

`FSMRunner.run()` creates and owns an `AsyncExitStack` around its entire state
loop. Each state receives that same stack. For example, session preparation uses:

```python
# Inside the admitting state's run(inputs, resources):
session, main_path, hook_path = await resources.enter_async_context(
    self.exporter.session_for_lease()
)
# Retain these references on the lease carried by subsequent states.
```

Do not wrap a persistent session in `async with` inside a single state evaluation:
returning the successor would close it. Short-lived resources that are needed
only during one evaluation can still use a local context normally.

For this exporter FSM, the stack contains **lease-scoped session/socket/driver
resources**. `ClosingLease` explicitly awaits its closure before recording
completion or returning to `Available`. The emptied stack is reusable for the
next lease. Exporter-wide services, telemetry, and supervisor task groups have
their own explicit scopes; they must not be closed by a per-lease `aclose()`.

On terminal exit or an exception, the runner unwinds whatever remains on the
stack in reverse acquisition order. Normal closure removes callbacks so the
final unwind does not close them twice. This is resource release, not an implicit
execution of skipped hook states: expected failure/cleanup policy still needs
explicit state transitions.

### Cancellation-safe cleanup and task-group nesting

`AsyncExitStack` guarantees invocation and ordering of registered cleanup, not
successful completion. Under AnyIO's level cancellation, a `finally` block can
start and its next cancellable await can immediately raise again. Protect awaited
close operations **inside the individual resource context manager**, with a
resource-appropriate bound:

```python
# Inside a resource context manager's finally block:
with anyio.fail_after(close_timeout, shield=True):
    await connection.aclose()
```

The shield blocks outer cancellation; the local timeout can still interrupt
cooperative async cleanup. It cannot interrupt a blocking synchronous driver
operation, which needs its own bounded operation or suitable execution strategy.
Do not suppress a close timeout and proceed to `Available`: incomplete teardown
must abort the lifecycle and prevent replacement admission. Preserve the original
failure as context if cleanup also fails.

The stack removes a callback before invoking it, so an interrupted/failed close
is not automatically retried by a later `aclose()`. A resource helper owns any
required termination fallback. Similarly, failed `__aenter__` must clean its own
partial allocations: the stack only registers contexts that entered successfully.

Session server cleanup is shielded and bounded (five seconds, with an immediate
gRPC stop fallback on timeout). Lease I/O cancellation/join is bounded to ten
seconds. Driver shutdown and close attempt sibling cleanup and propagate collected
failures; logging an error cannot turn incomplete teardown into availability.
Driver lifecycle methods remain synchronous and need driver-specific I/O bounds.

Keep AnyIO task groups and cancellation scopes explicitly nested in their owning
task. They require same-task, reverse-order scope exit. Do not register a task
group and then blindly wrap the entire `resources.aclose()` in a newer shield:
that can attempt to exit the group's cancel scope while the shield is still
active. Local shielding inside ordinary resource cleanup preserves nesting;
shielding the whole runner would also incorrectly defer cancellation of idle waits.

The exporter supervisor must terminate background producers when the runner
finishes; returning from one task does not cancel its siblings automatically:

```python
async with anyio.create_task_group() as background:
    background.start_soon(publish_controller_inputs)
    try:
        await runner.run()  # Includes final resource unwinding.
    finally:
        background.cancel_scope.cancel()
```

On normal shutdown, keep required I/O available through hook and resource cleanup,
then stop exporter-wide producer tasks. A background failure may cancel the runner
earlier, so resource-local cleanup protection is required on that path too.

The exporter keeps its background and lease-I/O groups in separate owning tasks.
Their ordinary resource contexts cancel and await those tasks, so `Stopping` can
join producers without cancelling the lifecycle task itself. Final status and
unregistration run after the status-report worker has stopped; an old retry cannot
overwrite the final report. The same exporter-wide cleanup is used on exceptions.

### Hook failure, shutdown, and stream policy

Reuse `execute_before_lease_hook()` and `execute_after_lease_hook()`, which return
a warning or raise. Policy orchestration now lives entirely in states; the
`run_*_hook()` wrappers and their readiness polling have been removed.

- **Success, no hook, or `warn`:** continue and retain warning information.
- **`endLease`:** report failure and request release of that named lease. Check
  identity/generation before each RPC attempt; retain the local-finish fallback
  after the release attempt.
- **`exit`:** retain failure information and request exit code 1, preserving the
  wait-for-lease-exit policy unless a stronger stop request arrives.
- **Unexpected execution/orchestration failure:** retain a failed state and
  respond to EndSession, lease-end, or shutdown according to cleanup eligibility;
  do not invent an `endLease` policy.

Before-hook `endLease`/`exit` failures suppress the after-hook. Assigned client
name alone does not establish cleanup eligibility or encode the before-hook
outcome; successor states make that decision.

**Drain** admits no replacement and stops after the active lease finishes; it
stops immediately when idle. **Stop-now** overrides drain and stops locally at
the next activity boundary without waiting for controller release. Protect only
activities/cleanup that must finish, preserving their internal configured
timeouts; never shield the entire generic runner.

The input merge makes stop-now sticky. A stronger request supplies its explicit
exit code/reason; equal or weaker modes retain the first explicit code/reason.
Controller cleanup cannot be re-enabled once disabled. Thus a terminal stream
stop-now with code 75 overrides a hook's drain request with code 1, while a later
hook failure cannot downgrade that terminal result. CLI signal codes retain their
existing precedence over exporter codes.

Terminal stream adapters publish shutdown intent to honor activity deferral.
The retry helper still raises to its callers; the lifecycle adapter handles
classified terminal failures. Preserve
classification: Status outage exhaustion and authentication/authorization
failures use exit code 75; other fail-fast gRPC errors use 1. Preserve the CLI's
signal exit-code precedence and whether controller cleanup RPCs are appropriate.
`exit_on_lease_end` and sidecar shutdown must honor full lease teardown.

FSM release attempts have a 30-second overall budget, including backoff, and
recheck assignment generation and stop-now before every RPC attempt and after
channel setup. The existing local-finish fallback applies after release failure
or budget exhaustion. Status delivery remains a background operation; hook
activities retain their configured timeouts. These bounds avoid waiting through
the old approximately 18-minute unary-RPC retry sequence at a lease boundary.

### Standalone lifecycle

Standalone TCP mode publishes a single `standalone` lease assignment and supplies its
TCP/Unix session context through `Available.session_context`. It uses the same
states and runner-owned stack, without controller connection tasks or RPCs.
`beforeLease` runs once at startup. EndSession runs eligible after-hook cleanup
once and retains the server for final status/log access until the exporter stops.

There is no controller reservation to drain: standalone stop requests, including
drain requests, become stop-now and take effect after the current activity.
An `endLease` hook failure retains the failed single session until stop; `exit`
stops with code 1. Explicit before-hook failures skip the after-hook in both modes.

### Incremental migration

1. Establish traces from existing exporter, hook, session, and retry tests,
   including the deliberate deferral change.
2. Review the shared runner and exporter-input reference foundation independently
   of production cutover.
3. Implement the revised resource-stack contract and prove context lifetime,
   awaited cleanup, and task-group exit with controlled resources first. Then
   implement the real lifecycle/failure states using existing operations and
   transfer resource lifetime and status publication together.
4. Adapt Status, signals, terminal stream failures, and lease-local EndSession
   producers. Use one lifecycle implementation at a time.
5. Retire replaced coordination only as ownership moves:

   | Existing coordination | Replacement |
   | --- | --- |
   | Pending assignment replay | Latest observation plus generation |
   | Completed-lease suppression | `Available.last_completed` after teardown |
   | Scattered stop flags/checks | Persistent shutdown intent |
    | Hook completion messages and racing cleanup paths | Sequential activity awaits and successors |
    | `before_lease_hook`, `after_lease_hook_started`, `after_lease_hook_done`, `skip_after_lease_hook` | Hook states, successors, and session-status routing gate |
    | `lease_ended` | Assignment generation checks and `ClosingLease` |
    | `LeaseContext.current_status` / `status_message` | Session-owned status and message |
   | `LeaseFinished` | Awaited full resource teardown by the lifecycle task |
   | Multiple lifecycle status writers | One state/outcome publisher |

6. Reuse the lifecycle in standalone mode, remove its obsolete wrappers/events,
   and run relevant regressions and package/full type checks. A watchdog warning
   must never be used as evidence that teardown completed.

## Test Plan

### Unit tests and static verification

Shared-runner cases use an independent boolean source alongside exporter-input
checks. They use existing pytest, AnyIO event barriers, and bounded waits instead
of sleep-based ordering.

Coverage includes current-state queries, read-only access, duplicate execution,
logical transitions versus reevaluation, named logs, deterministic run timing,
error/cancellation propagation and cleanup, input-payload omission, wakeup races,
coalescing, generation boundaries, completed-assignment suppression, shutdown
escalation, lease-local requests, and blocked teardown before replacement.
Immediate-transition checks cover already-pending cancellation and producer
progress with fresh snapshots through states that never yield internally.
Resource tests enter actual async contexts and await their cleanup. The production
lifecycle suite additionally uses real gRPC session sockets, exercises driver
calls during both hook phases, and verifies producer and connection-task shutdown.

### Resource-stack verification

- A context entered by one state remains usable across successors and a `None`
  input wait; acquisition and closure occur in the runner's task.
- Explicit lease closure completes before replacement admission; final runner
  unwind does not repeat it, and the stack can serve a second lease.
- State failure and cancellation during both activities and input waits invoke
  actual awaited cleanup. Protected close finishes before `runner.run()` exits.
- If later setup fails, previously registered resources close; the failing
  context cleans its own partially acquired resources.
- Resource contexts receive the original failure for exception-aware cleanup,
  but cannot suppress it and turn the lifecycle into successful completion.
- Close failure/timeout propagates, retains diagnostic state, and never reports
  successful `ExitFSM`, completion, or availability for another lease.
- Task groups exit without cancel-scope nesting errors; normal terminal exit and
  background-task failure both leave no running producer tasks.
- Update typed state methods and positive/negative probes for the added
  `AsyncExitStack` argument.

### Static verification

Positive `assert_type()` checks pass for Python 3.12 and 3.14 targets. Eight
temporary negative probes reject invalid state/successor returns, shutdown mode,
metadata types, writes to `current_state`, and mismatched state/input-source
input types, including legacy state methods missing the resource-stack argument.
Each diagnostic points to its intended statement.

From `python/`:

```sh
uv run --isolated --directory packages/jumpstarter pytest -q jumpstarter/fsm_test.py jumpstarter/exporter/fsm_test.py
make pkg-ty-jumpstarter
uv run ruff check packages/jumpstarter/jumpstarter/fsm{,_test}.py packages/jumpstarter/jumpstarter/exporter/fsm{,_test}.py
```

The new files pass independently on macOS; the existing package-wide native
macOS type check encounters `errno.ENOMEDIUM` in `jumpstarter/common/storage.py`.
Package typing with `--python-platform linux` passes. This proposal does not
change unrelated code to bypass that platform issue.

### Integration tests

The real lifecycle migration must verify:

- Release during the before-hook finishes before cleanup begins; no replacement
  starts before complete old-session teardown.
- `A → B → C` while blocked admits only the latest valid assignment after A
  closes; `A → unleased → A` invalidates the original instance.
- EndSession runs the after-hook once while preserving reservation, logs, and
  status access; subsequent release does not rerun cleanup.
- Hook policies, failure visibility, before-failure suppression, and driver-call
  access work during both hook phases.
- Drain-to-stop-now escalation, `exit_on_lease_end`, and sidecar shutdown preserve
  cleanup ordering and prevent replacement admission.
- Retryable stream outages retain the lease; terminal failures retain exit-code
  and controller-cleanup policy.
- Setup failure and exceptional cancellation close resources, and retries cannot
  become an immediate admission/failure loop.
- Standalone mode uses the same states, defers requests during hooks, runs cleanup
  once, retains final-status access after EndSession, and makes no controller RPCs.

The integration suite checks these production states with controlled hooks and
controller operations. Obsolete control-loop, `LeaseFinished`, and hook-wrapper
tests are replaced by lifecycle tests for their observable guarantees. Hook
execution, session, unary/stream retry, and sidecar-operation regressions remain.
Run these and CLI regressions plus affected-package and full `make ty` checks.

Verification on 2026-10-28 after standalone and `LeaseContext` cleanup: the core
package suite (including doctests) has 1,083 passing tests and 20 existing XPASS
cases; the exporter/shared-runner suite has 269 passing tests and the same 20 XPASS.
CLI run/signal checks have 13 passing cases. All 63 focused FSM/input/lifecycle/
LeaseContext checks pass. Core and CLI package typing pass for Linux. Native full
`make ty` remains blocked by the macOS `errno.ENOMEDIUM` diagnostic noted above.
The project's `--capture=no` setting is needed by shell tests that pass stdin to
subprocesses.

### Hardware-in-the-loop and manual verification

No hardware is required for the reference runner or input checks. Before
merge, exercise a lab exporter with before/after hooks that
perform driver calls, hold a hook while releasing/reassigning the lease, and send
drain and stop-now signals. Verify real device/sidecar lifetime and final-log
access rather than relying only on synthetic state traces. Hardware availability
and automation in the project's lab CI must be confirmed for that migration.

Reviewers can run the reference suites and enable DEBUG for `jumpstarter.fsm`
and `jumpstarter.exporter.fsm` to inspect evaluation timing and named transitions.

## Acceptance Criteria

- [x] A domain-independent typed runner and exporter-input specialization are
  provided and used by both exporter modes.
- [x] Independent input sources, external state queries, safe debug tracing,
  behavioral checks, and positive/negative typing verification work.
- [x] The runner-owned `AsyncExitStack` is passed to states; context lifetime,
  same-task LIFO teardown, bounded cancellation-safe closing, and supervisor exit
  pass the resource-stack checks above.
- [x] Real lifecycle/failure states own hook sequencing, status publication, and
  resource teardown with one coherent exporter cutover.
- [x] Reservation/final-log behavior, driver gating, shutdown deferral, and stream
  failure classification pass exporter/CLI integration regressions.
- [x] Standalone shares the lifecycle; `LeaseContext` has no phase events or
  duplicated status, and only external EndSession intent remains lease-local.
- [x] Operational documentation covers hook/shutdown deferral in both modes.
- [ ] Complete hardware-backed verification before merge.

## Backward Compatibility

The implementation changes exporter orchestration without changing gRPC
messages, CRDs, CLI flags, configuration, or exported driver methods. The Python
interfaces and lifecycle policy remain subject to JEP review. Standalone TCP mode
now uses the same FSM, retaining its single-session lifetime and local hook access.

Adding `resources: AsyncExitStack` changes the reference state method signature
from `run(snapshot)` to `run(inputs, resources)`. Update all reference states and
type examples together; the implementation and checks use the revised signature.

The migration deliberately changes ordinary stop handling in both modes
during activities: requests wait for a bounded activity boundary rather than
immediately cancelling the task group. This may leave old connection handling
alive longer after revocation or finish setup for an already revoked assignment.
Validate admission, driver access, and cleanup ordering explicitly. Preserve
external status, failure policies, stream exit codes, and CLI signal precedence.
No operator or wire-protocol upgrade is required.

Session/driver teardown errors now propagate after remaining cleanup is attempted,
rather than merely being logged. This intentionally prevents successful exit or
replacement admission when a driver reports incomplete teardown.

## Consequences

### Positive

- Lifecycle decisions, awaited work, and allowed successors are readable together.
- A reusable runner provides queries and consistent debug timing without forcing
  exporter policy on other components.
- Sequential ownership removes coordination paths rather than hiding them in
  another event framework.
- Typed contracts and deterministic checks make invalid payloads and ordering
  regressions easier to detect.

### Negative

- A shared Python interface must be maintained, even though it intentionally
  omits persistence, hierarchical states, generated diagrams, and timers.
- Awaited activities delay ordinary control requests and depend on correct
  timeouts and exceptional cleanup.
- Resource lifetime and outward status remain domain concerns; the runner cannot
  prove safety merely because a type check succeeds.
- The foundation is not a drop-in exporter replacement; migration still requires
  cross-component regression and hardware-backed verification.

## Rejected Alternatives

- **Only document the existing flags:** useful short-term, but retains scattered
  lifecycle decisions and competing cleanup paths.
- **Migrate before verifying the foundation:** makes design review depend on a
  large behavioral cutover. The implementation establishes the foundation and
  resource-lifetime checks before integrating the real exporter.
- **Force every component onto the shared runner:** reuse is optional; simple
  existing sequential code does not need conversion without a concrete benefit.

The event framework, per-state wait loops, bare latest-name coalescing, and global
input ownership registry alternatives are covered in DD-1 through DD-4.

## Unresolved Questions

- Confirm the activity-boundary behavior and resource-close bounds with a lab
  exporter, including hardware drivers whose synchronous cleanup needs its own
  I/O timeout. Automated coverage verifies simulated controller/hook scheduling
  and real local sockets, not physical-device timing.
- Confirm lab/CI coverage for hardware-backed migration checks before merge.

## Implementation History

- 2026-10-26: Converted the exporter FSM design plan into this proposal and
  included the typed shared runner, exporter-input reference, and 19 checked
  behavioral cases in [PR #1250](https://github.com/jumpstarter-dev/jumpstarter/pull/1250).
  That increment left production lifecycle integration pending.
- 2026-10-28: Specify a runner-owned `AsyncExitStack` passed to state methods,
  explicit per-lease closure, resource-local bounded/shielded cleanup, and scoped
  supervisor shutdown, initially as a design-only revision.
- 2026-10-28: Add a runner checkpoint before snapshot capture and two regression
  cases for cancellation and producer progress through immediate transitions.
- 2026-10-28: Implement the resource-stack contract and migrate controller-mode
  orchestration to typed lifecycle/failure states. Replace pending-assignment
  replay and `LeaseFinished` coordination; add real-session integration checks,
  bounded release attempts, terminal-stream precedence, and strict teardown.
- 2026-10-28: Name state data `Inputs` and its publishing/coalescing source
  `InputBuffer`; state methods read `run(inputs: Inputs, resources: AsyncExitStack)`.
- 2026-10-28: Reuse the lifecycle for standalone TCP mode; remove hook wrappers,
  readiness polling, and all `LeaseContext` events and duplicated status. Retain
  EndSession as a boolean request; use session-status notification for routing.

## References

- [JEP-0000: Jumpstarter Enhancement Proposal Process](https://github.com/jumpstarter-dev/jumpstarter/blob/main/docs/source/contributing/jeps/JEP-0000-jep-process.md)
- Reference modules and tests listed in the Proposal section, included in this
  proposal's pull request.
- Existing exporter integration points:
  `python/packages/jumpstarter/jumpstarter/exporter/{exporter,hooks,session,lease_context}.py`
  and `python/packages/jumpstarter-cli/jumpstarter_cli/run.py`.

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
