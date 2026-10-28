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
and its use to make the controller-mode exporter lifecycle explicit. One task
executes named states sequentially, while background tasks publish observations
and requests without performing transitions. A state awaits its activity and
returns a successor, returns `None` to let the runner wait for changed inputs, or
returns `ExitFSM` to terminate. The accompanying reference implementations of
`jumpstarter.fsm` and `jumpstarter.exporter.fsm` demonstrate the foundation;
migrating the running exporter is a separate, subsequent step.

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


class InputSource[SnapshotT](Protocol):
    def snapshot_and_watch(self) -> tuple[SnapshotT, Event]: ...


class State[SnapshotT](Protocol):
    async def run(
        self, snapshot: SnapshotT, resources: AsyncExitStack
    ) -> State[SnapshotT] | ExitFSM | None: ...


@dataclass(frozen=True)
class ExitFSM:
    """Stop evaluating states; the runner unwinds remaining resources before exit."""
```

The fourth building block, `FSMRunner[SnapshotT]`, owns the current state, the
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
    Capture --> Run["await state.run(snapshot, resources)"]
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

`jumpstarter.exporter.fsm` supplies exporter-specific assignment, shutdown, and
snapshot types. Its `Inputs` class satisfies `InputSource[Snapshot]` structurally;
it need not inherit from the protocol. Its `State` alias specializes the shared
protocol for that snapshot type:

```python
from jumpstarter.fsm import State as FSMState

type State = FSMState[Snapshot]
```

The shared runner knows nothing about leases, gRPC, drivers, or hook policies.
Exporter states retain those decisions, together in one module initially. No
empty exporter runner subclass or factory is required.

### Scope and reference implementations

This proposal includes a working foundation, not a completed exporter cutover:

| Reference file | Purpose |
| --- | --- |
| `python/packages/jumpstarter/jumpstarter/fsm.py` | Generic interfaces, named single-use runner, current-state queries, and debug tracing |
| `python/packages/jumpstarter/jumpstarter/fsm_test.py` | An independent boolean-input example and runner behavior checks |
| `python/packages/jumpstarter/jumpstarter/exporter/fsm.py` | Exporter snapshots, assignment generations, persistent shutdown intent, and input notifications |
| `python/packages/jumpstarter/jumpstarter/exporter/fsm_test.py` | Typed demonstration states and exporter-input checks using existing `LeaseContext` objects |

The demo does not open real sessions or transports. Its compact `Cleanup` state
stands in for completion; the after-hook, reservation wait, and transport-close
separation below remains to be implemented. Neither reference module is wired
into `Exporter.serve()` by this proposal's reference-code increment.

The 2026-10-28 resource-lifetime revision is a planned follow-up: the reference
code still implements `run(snapshot)`. Passing a runner-owned `AsyncExitStack`
and verifying real awaited cleanup have not yet been implemented. The reported
21 passing cases below cover the existing foundation, not this new guarantee.

The controller-mode migration reuses the existing hook execution, transport,
reporting, and supervision facilities. Standalone TCP mode, connection bridging,
and process supervision are not redesigned. Shared helpers must keep their
standalone callers working as controller-mode orchestration moves.

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

1. **Maintain an active/completed registry in `Inputs`:** validate ownership, but
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
`run(snapshot, resources)`. States own acquisition, use, and normal release;
`Inputs` only stores inputs and notifies the runner.

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

With the resource-stack revision, retain the last active state while final
unwinding runs and publish the terminal `ExitFSM` state only after successful
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
| Controller assignment | Immutable latest assignment with lease key, client name, and copied read-only string metadata |
| Lease key | Lease name and publisher-assigned local generation |
| Shutdown | Persistent mode, optional exit code, reason, and controller-cleanup policy |
| Active lease/resources | Current state's lease reference, not a global input registry |
| Registered resource lifetime | Runner-owned `AsyncExitStack`, shared across state calls |
| Completed assignment | `Available.last_completed`, updated only after teardown |
| EndSession | Existing `LeaseContext.end_session_requested` event on that session's lease |
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

The session adapter sets its own lease's EndSession event and calls
`inputs.wake("EndSession")` synchronously, without an intervening await. Duplicate
requests are idempotent. An old session can at most cause an extra reevaluation;
it cannot alter or clear the new lease's request. Never reuse a lease object for
another assignment.

### Race-safe waiting

The runner captures snapshot and event together, without an await, **before**
calling the current state. A publisher stores its new snapshot, creates the next
notification event, and sets the previous event. Lease-local requests set their
own flag and wake the runner even though the global snapshot is unchanged.

```{mermaid}
sequenceDiagram
    participant Runner
    participant Inputs
    participant State as Current state
    participant Producer
    Runner->>Inputs: snapshot_and_watch()
    Inputs-->>Runner: snapshot, event E1
    Runner->>State: run(snapshot, resources)
    Producer->>Inputs: Publish changed observation or wake after lease-local request
    Note over Inputs: Create E2 and set E1
    State-->>Runner: None
    Note over Runner: Waiting on E1 returns immediately
    Runner->>Inputs: snapshot_and_watch()
    Inputs-->>Runner: Latest snapshot, event E2
    Runner->>State: run(latest snapshot, resources)
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
  `LeaseReady` or `BeforeLeaseHookFailed` directly from its outcome.
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

`EndSession` does **not** release the reservation or set `lease_ended`. Ordinary
cleanup never requests controller release. The existing
`TestCleanupDoesNotReleaseActiveLease` regression is part of the compatibility
contract. Hook completion is not transport completion.

The session and hook socket must exist before the before-hook and remain alive
through after-hook execution and any release wait. Register `session_for_lease()`
with the runner-provided stack, keeping context entry/exit in the same lifecycle
task, including exceptional cleanup if setup or a state raises. Listen,
connection bridging, gRPC serving, and log delivery remain concurrent I/O tasks.
Both hook phases retain driver-call access so hooks can use `j`.

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
# Inside the admitting state's run(snapshot, resources):
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

### Hook failure, shutdown, and stream policy

Reuse `execute_before_lease_hook()` and `execute_after_lease_hook()`, which return
a warning or raise. Move policy orchestration out of the `run_*_hook()` wrappers
as their callers migrate; do not run old and new orchestration simultaneously.

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
name alone is not a cleanup-eligibility test: `has_client()` does not mean a
connection occurred and does not encode the before-hook outcome.

**Drain** admits no replacement and stops after the active lease finishes; it
stops immediately when idle. **Stop-now** overrides drain and stops locally at
the next activity boundary without waiting for controller release. Protect only
activities/cleanup that must finish, preserving their internal configured
timeouts; never shield the entire generic runner.

The reference input merge makes stop-now sticky, retains the first explicit
exit code and nonempty reason, and never re-enables controller cleanup once
disabled. This is reference behavior to review before production integration,
particularly when hook failures, signals, and stream failures conflict.

Terminal stream failures currently raise into the task group. Their adapters
must instead publish shutdown intent where required to honor deferral. Preserve
classification: Status outage exhaustion and authentication/authorization
failures use exit code 75; other fail-fast gRPC errors use 1. Preserve the CLI's
signal exit-code precedence and whether controller cleanup RPCs are appropriate.
`exit_on_lease_end` and sidecar shutdown must honor full lease teardown.

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
   | `LeaseFinished` | Awaited full resource teardown by the lifecycle task |
   | Multiple lifecycle status writers | One state/outcome publisher |

6. Run relevant regressions and package/full type checks; adapt shared standalone
   callers before deleting their helpers. A watchdog warning must never be used
   as evidence that teardown completed.

## Test Plan

### Unit tests and static verification

The included reference has 21 passing cases: seven shared-runner cases using a
boolean source, and fourteen exporter-specific cases. They use existing pytest,
AnyIO event barriers, and bounded waits instead of sleep-based ordering.

Coverage includes current-state queries, read-only access, duplicate execution,
logical transitions versus reevaluation, named logs, deterministic run timing,
error/cancellation propagation and cleanup, input-payload omission, wakeup races,
coalescing, generation boundaries, completed-assignment suppression, shutdown
escalation, lease-local requests, and blocked teardown before replacement.
Immediate-transition checks cover already-pending cancellation and producer
progress with fresh snapshots through states that never yield internally.
Existing cleanup assertions use test events; they do not prove cancellation-safe
closing of actual async contexts. The resource-stack revision requires additional
checks before those guarantees are considered verified.

### Resource-stack verification to add

- A context entered by one state remains usable across successors and a `None`
  input wait; acquisition and closure occur in the runner's task.
- Explicit lease closure completes before replacement admission; final runner
  unwind does not repeat it, and the stack can serve a second lease.
- State failure and cancellation during both activities and input waits invoke
  actual awaited cleanup. Protected close finishes before `runner.run()` exits.
- If later setup fails, previously registered resources close; the failing
  context cleans its own partially acquired resources.
- Close failure/timeout propagates, retains diagnostic state, and never reports
  successful `ExitFSM`, completion, or availability for another lease.
- Task groups exit without cancel-scope nesting errors; normal terminal exit and
  background-task failure both leave no running producer tasks.
- Update typed state methods and positive/negative probes for the added
  `AsyncExitStack` argument.

### Static verification of the existing reference

Positive `assert_type()` checks pass for Python 3.12 and 3.14 targets. Seven
temporary negative probes reject invalid state/successor returns, shutdown mode,
metadata types, writes to `current_state`, and mismatched state/input-source
snapshot types. Check intended diagnostics, not unrelated import failures.

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

Run existing exporter, hook, session, retry, standalone, and CLI regressions plus
the affected package and full `make ty` checks. These integration results are not
claimed by the reference foundation.

### Hardware-in-the-loop and manual verification

No hardware is required for the reference runner or input checks. Before
controller-mode cutover, exercise a lab exporter with before/after hooks that
perform driver calls, hold a hook while releasing/reassigning the lease, and send
drain and stop-now signals. Verify real device/sidecar lifetime and final-log
access rather than relying only on synthetic state traces. Hardware availability
and automation in the project's lab CI must be confirmed for that migration.

Reviewers can run the reference suites and enable DEBUG for `jumpstarter.fsm`
and `jumpstarter.exporter.fsm` to inspect evaluation timing and named transitions.

## Acceptance Criteria

- [x] A domain-independent typed runner and exporter-input specialization are
  provided as tested reference code, without production wiring.
- [x] Independent input sources, external state queries, safe debug tracing,
  behavioral checks, and positive/negative typing verification work.
- [ ] The runner-owned `AsyncExitStack` is passed to states; context lifetime,
  same-task LIFO teardown, bounded cancellation-safe closing, and supervisor exit
  pass the resource-stack checks above.
- [ ] Real lifecycle/failure states own hook sequencing, status publication, and
  resource teardown with one coherent exporter cutover.
- [ ] Reservation/final-log behavior, driver gating, shutdown deferral, and stream
  failure classification pass exporter/CLI integration regressions.
- [ ] Standalone callers remain supported and replaced coordination is removed
  only after the lifecycle task assumes its responsibility.
- [ ] Hardware-backed verification and operational documentation cover the
  deliberate hook/shutdown deferral behavior before it becomes the default.

## Backward Compatibility

The reference increment is additive and not invoked by the current exporter.
It does not change gRPC messages, CRDs, CLI flags, configuration, driver APIs, or
current reservation/hook behavior. The proposed Python interfaces remain subject
to JEP review; including a reference does not mean the full lifecycle is accepted
or implemented.

Adding `resources: AsyncExitStack` changes the reference state method signature
from `run(snapshot)` to `run(snapshot, resources)`. Update all reference states and
type examples together before adoption; the change is currently planned only.

The later controller-mode migration deliberately changes ordinary stop handling
during activities: requests wait for a bounded activity boundary rather than
immediately cancelling the task group. This may leave old connection handling
alive longer after revocation or finish setup for an already revoked assignment.
Validate admission, driver access, and cleanup ordering explicitly. Preserve
external status, failure policies, stream exit codes, and CLI signal precedence.
No operator or wire-protocol upgrade is required.

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
- **Replace the full exporter in the proposal PR:** makes design review depend on
  a large behavioral cutover. Review the foundation and compatibility contract
  first, then integrate incrementally.
- **Force every component onto the shared runner:** reuse is optional; simple
  existing sequential code does not need conversion without a concrete benefit.

The event framework, per-state wait loops, bare latest-name coalescing, and global
input ownership registry alternatives are covered in DD-1 through DD-4.

## Unresolved Questions

- Confirm ordinary activity-boundary deferral and its operational bounds before
  the real exporter cutover.
- Finalize conflicting hook/signal/stream exit-code and controller-cleanup
  precedence; the reference's first-explicit-code rule is not evidence that
  existing terminal-stream classification is preserved in every combined case.
- Select concrete runtime dependencies and per-resource close bounds/fallbacks
  for real states, and confirm lab/CI coverage for hardware-backed migration
  checks. The runner-owned exit stack and resource-local cleanup protection are
  decided; their implementation and verification remain pending.

## Implementation History

- 2026-10-26: Converted the exporter FSM design plan into this proposal and
  included the typed shared runner, exporter-input reference, and 19 checked
  behavioral cases in [PR #1250](https://github.com/jumpstarter-dev/jumpstarter/pull/1250).
  Production lifecycle integration remains pending.
- 2026-10-28: Specify a runner-owned `AsyncExitStack` passed to state methods,
  explicit per-lease closure, resource-local bounded/shielded cleanup, and scoped
  supervisor shutdown. This resource-lifetime revision is not yet implemented.
- 2026-10-28: Add a runner checkpoint before snapshot capture and two regression
  cases for cancellation and producer progress through immediate transitions.

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
