# JEP-0012: Explicit Lease Lifecycle State Machine for Exporter

| Field              | Value |
|--------------------|-------|
| **JEP**            | 0012 |
| **Title**          | Explicit Lease Lifecycle State Machine for Exporter |
| **Author(s)**      | @mangelajo |
| **Status**         | Discussion |
| **Type**           | Standards Track |
| **Created**        | 2026-04-17 |
| **Updated**        | 2026-04-17 |
| **Discussion**     | Pending PR creation |

---

## Abstract

This JEP proposes replacing the exporter's implicit lease lifecycle
coordination with an explicit per-lease state machine.

Today the exporter coordinates lease setup and teardown through a mix of
`LeaseContext` events, exporter status values, hook callbacks, and task
cancellation timing. That design works for the common path, but recent
fixes around premature lease end and unused lease timeout have shown that
the current model is difficult to reason about and easy to break when new
paths are added.

The proposal introduces a small explicit lease lifecycle state machine,
owned by the exporter, that becomes the single source of truth for:

- when connections may start
- when cleanup may begin
- when after-lease work is allowed to run
- when a lease is fully complete and the next lease may start

This is an internal refactor only. It does not change the gRPC protocol
or the external `ExporterStatus` API.

## Motivation

The current exporter lease lifecycle is already an implicit state
machine, but its state is spread across multiple mechanisms:

- `LeaseContext.before_lease_hook`
- `LeaseContext.after_lease_hook_started`
- `LeaseContext.after_lease_hook_done`
- `LeaseContext.lease_ended`
- `LeaseContext.skip_after_lease_hook`
- `ExporterStatus` transitions such as `BEFORE_LEASE_HOOK`,
  `LEASE_READY`, `AFTER_LEASE_HOOK`, and `AVAILABLE`
- cancellation timing across `serve()`, `handle_lease()`,
  `_handle_end_session()`, and the hook runner

This makes the lifecycle hard to audit because the answer to "what state
is this lease in?" depends on several fields at once.

Recent fixes exposed the problem:

- the no-hook deadlock fixed in PR #569
- unused-lease cleanup behavior adjusted in PR #598
- the follow-up semantic bug around `before_lease_hook` meaning both
  "beforeLease is done" and "cleanup must not deadlock", addressed in
  PR #614

These changes were individually correct, but they all touched the same
underlying issue: the exporter does not have one explicit lifecycle model
for leases.

### User Stories

- **As an exporter maintainer**, I want one authoritative lease state so
  that I can change setup and cleanup logic without reasoning about
  several loosely-coupled events.
- **As a contributor adding hook behavior**, I want the rules for
  readiness and cleanup to be explicit so that adding a new edge case
  does not create another race.
- **As an exporter user**, I want leases to transition reliably through
  setup and teardown so that short-lived leases, unused leases, and hook
  failures do not leave the exporter stuck.

### Constraints

- No gRPC protocol changes
- No change to the externally visible meaning of `ExporterStatus`
- Keep `HookExecutor` focused on executing hooks, not owning exporter
  lifecycle policy
- Preserve current behavior for no-hook, before-hook, after-hook, and
  standalone exporters
- The refactor must remain compatible with anyio task cancellation
  semantics

## Proposal

Introduce a dedicated per-lease lifecycle controller, tentatively named
`LeaseLifecycle`, owned by the exporter and attached to each
`LeaseContext`.

The lifecycle controller defines an explicit state enum and a small set
of validated transition methods. Instead of using the same `Event` to
mean multiple things, the exporter will move the lease through named
states and expose wait points with dedicated semantics.

### Proposed Internal States

The initial state set should stay intentionally small:

- **`CREATED`** - Lease context exists, but session setup has not
  completed.
- **`STARTING`** - Session sockets and Listen stream are being prepared.
- **`BEFORE_LEASE`** - A configured `before_lease` hook is running.
- **`READY`** - Connections may be routed and driver calls are allowed.
- **`ENDING`** - Lease end has been requested; no new work should begin.
- **`AFTER_LEASE`** - The after-lease phase is running, or being
  intentionally skipped according to policy.
- **`RELEASING`** - The exporter is releasing the lease and finalizing
  cleanup.
- **`DONE`** - Lease cleanup is complete; the exporter may accept the
  next lease.
- **`FAILED`** - Terminal local failure in lifecycle orchestration.

The exporter may still track orthogonal flags such as `has_client`,
`stop_requested`, or `skip_after_lease_hook`, but these flags should not
replace the lifecycle state itself.

### Proposed Wait Points

The FSM replaces overloaded wait semantics with dedicated signals:

- **connections ready** - set exactly when the lifecycle enters `READY`
- **cleanup complete** - set exactly when the lifecycle enters `DONE` or
  `FAILED`

These signals replace the current overloading of
`before_lease_hook`/`after_lease_hook_done` as generic coordination
primitives.

### Proposed Ownership Boundaries

- `Exporter` owns lifecycle state and transition policy
- `LeaseLifecycle` validates and records transitions
- `LeaseContext` holds lease resources and lifecycle references
- `HookExecutor` only executes hook bodies and reports results back to
  the exporter

In particular, `HookExecutor` should not set lifecycle completion flags
directly. Instead, the exporter should observe hook completion and apply
the appropriate transition.

## API / Protocol Changes

No gRPC protocol changes are proposed.

No user-visible YAML configuration changes are proposed.

This JEP changes only internal exporter orchestration.

## Design Decisions

### DD-1: Use an explicit internal FSM instead of adding more event checks

**Alternatives considered:**

1. **Keep the current event-based model** and continue tightening edge
   cases with narrower condition checks and more tests.
2. **Introduce an explicit internal FSM** with validated transitions and
   named wait points.

**Decision:** Introduce an explicit internal FSM.

**Rationale:** The exporter is already acting as a state machine, just
without a single source of truth. The recent bugs were not caused by
lack of tests alone; they were caused by the same event representing
different phases depending on context. An explicit FSM addresses that
root cause directly.

### DD-2: Keep `ExporterStatus` separate from internal lifecycle state

**Alternatives considered:**

1. **Reuse `ExporterStatus` as the lifecycle state.**
2. **Keep a separate internal lifecycle enum and derive outward status
   from it.**

**Decision:** Keep a separate internal lifecycle enum.

**Rationale:** `ExporterStatus` is a client-visible API and carries
compatibility expectations. It is not detailed enough to represent every
internal transition, and one outward status may correspond to different
internal phases. The FSM should be the internal source of truth, while
`ExporterStatus` remains the public projection.

### DD-3: Model readiness and completion as separate wait conditions

**Alternatives considered:**

1. **One shared event** that means both "ready enough to proceed" and
   "safe to clean up".
2. **Separate wait conditions** for readiness and cleanup completion.

**Decision:** Use separate wait conditions.

**Rationale:** The current design's biggest problem is semantic
overloading. Readiness for client routing and completion of hook/setup
work are not the same thing. Explicit separate wait points make this
difference impossible to encode incorrectly.

### DD-4: Keep hook execution separate from lifecycle orchestration

**Alternatives considered:**

1. **Let `HookExecutor` own more lifecycle signaling** for no-hook and
   post-hook paths.
2. **Keep `HookExecutor` as a pure executor** while `Exporter`
   orchestrates state changes.

**Decision:** Keep `HookExecutor` as a pure executor.

**Rationale:** Hook execution is only one input into the lease
lifecycle. The exporter also has to coordinate session readiness,
connection routing, client-driven end-session, controller-driven lease
end, release requests, and shutdown. Those concerns belong in the
exporter, not the hook runner.

### DD-5: Migrate incrementally instead of rewriting the exporter in one change

**Alternatives considered:**

1. **One large rewrite** replacing `LeaseContext` coordination in a
   single PR.
2. **Incremental migration** introducing the lifecycle controller first,
   then moving individual coordination points to it.

**Decision:** Migrate incrementally.

**Rationale:** The exporter has several subtle edge cases already covered
by focused tests. An incremental approach allows the project to preserve
those behavioral guarantees while moving one orchestration concern at a
time behind the FSM.

## Design Details

### Suggested Structure

This JEP proposes adding a small internal module such as
`jumpstarter/exporter/lease_lifecycle.py` containing:

- `LeaseLifecycleState` enum
- `LeaseLifecycle` controller object
- transition validation helpers
- two named wait primitives for readiness and completion

`LeaseContext` would then hold:

- lease identity and session/socket references
- client metadata
- status message cache
- lifecycle controller reference

The goal is to reduce `LeaseContext` from a bag of coordination events to
a clearer resource holder plus lifecycle object.

### Suggested Transition Flow

The expected happy path becomes:

1. `CREATED` when the controller assigns a lease
2. `STARTING` when `handle_lease()` begins session and Listen setup
3. `BEFORE_LEASE` if a configured `before_lease` hook exists
4. `READY` when routing and driver calls may proceed
5. `ENDING` when the controller ends the lease or the client requests
   end-session
6. `AFTER_LEASE` if after-lease work must run
7. `RELEASING` while the exporter asks the controller to free the lease
8. `DONE` when cleanup completes

For no-before-hook or after-lease-only exporters, the lifecycle skips the
`BEFORE_LEASE` state entirely. For no-after-hook exporters, the lifecycle
may transition from `ENDING` directly to `RELEASING`.

### Early-End During Before-Hook

One of the hardest current paths is:

1. session and Listen startup begin
2. a real `before_lease` hook starts
3. the lease ends before the hook finishes
4. cleanup must not start `after_lease` early
5. the exporter must still eventually complete cleanup

Under the FSM, this is expressed as:

- lifecycle stays in `BEFORE_LEASE`
- an `end_requested` intent is recorded
- when the before-hook completes, the lifecycle transitions to `ENDING`
  instead of `READY`
- cleanup then proceeds according to normal end-of-lease policy

This keeps the lifecycle explicit without requiring one overloaded event
to mean both "before hook finished" and "cleanup should not deadlock".

### After-Lease-Only Configuration

A current pain point is the `after_lease`-only configuration, where a
`hook_executor` exists but no `before_lease` hook is configured. The FSM
must make this path unambiguous:

- do not schedule a before-hook phase
- do not wait for synthetic before-hook completion
- enter `READY` once session setup and Listen startup are complete
- still run the configured after-lease phase on teardown

### ExporterStatus Projection

The exporter should continue reporting the same outward statuses, but
from explicit lifecycle transitions:

- `BEFORE_LEASE_HOOK` while in `BEFORE_LEASE`
- `LEASE_READY` on entry to `READY`
- `AFTER_LEASE_HOOK` while in `AFTER_LEASE`
- `AVAILABLE` once teardown has completed and the exporter is ready for a
  new lease
- failure statuses derived from the current phase and failure reason

This keeps the wire-level contract stable while making the internal
behavior clearer.

## Acceptance Criteria

- [ ] The implementation introduces a single explicit internal lease
      lifecycle enum and validated transition API.
- [ ] The exporter no longer uses one shared event to mean both
      "beforeLease completed" and "cleanup must not deadlock".
- [ ] `after_lease`-only configurations do not schedule a synthetic
      before-hook phase.
- [ ] When a lease ends during a real `before_lease` hook, `after_lease`
      cannot start until the before-hook has actually completed.
- [ ] When a no-hook lease ends before becoming ready, the exporter does
      not deadlock and still reaches cleanup completion.
- [ ] The next lease is not accepted until the previous lifecycle reaches
      `DONE`.
- [ ] Existing outward `ExporterStatus` values and gRPC protocol behavior
      remain backward compatible.

## Test Plan

### Unit Tests

- Transition validation for every allowed and disallowed lifecycle move
- Wait helpers for `READY` and `DONE`
- State projection to outward `ExporterStatus`
- End-request behavior while in `BEFORE_LEASE`

### Integration Tests

- no-hook lease that ends before ready does not deadlock
- `after_lease`-only exporter becomes ready without a synthetic
  before-hook phase
- real before-hook + early lease end waits for actual hook completion
- unused lease timeout still releases the lease correctly
- client `EndSession` and controller lease-end both converge on the same
  terminal lifecycle state

### Regression Tests

Preserve focused regressions for the scenarios addressed by:

- PR #569
- PR #598
- PR #614

## Backward Compatibility

This JEP proposes an internal refactor only.

There are no changes to:

- gRPC protocol definitions
- exporter configuration schema
- hook configuration schema
- client-visible exporter status names

Existing tests should continue to pass with the lifecycle FSM
implementation in place.

## Consequences

### Positive

- One explicit source of truth for per-lease lifecycle state
- Easier reasoning about readiness, teardown, and hook ordering
- Cleaner ownership boundaries between exporter orchestration and hook
  execution
- Better basis for transition logging and future metrics

### Negative

- More internal structure than the current event-based design
- Initial refactor cost is non-trivial because several methods currently
  coordinate implicitly
- Contributors will need to learn a new internal lifecycle abstraction

### Risks

- An FSM that is too large or too generic could become harder to
  maintain than the current code
- Partial migration could temporarily create duplicated logic if the
  project does not complete the transition
- Incorrect status projection from internal state could create subtle
  regressions even if the core transition model is sound

## Rejected Alternatives

The main rejected alternative is to continue the current design and fix
each newly discovered race with narrower condition checks plus more test
coverage.

That approach can solve individual failures, but it does not address the
root design problem that multiple events and status fields currently
encode overlapping notions of readiness, cleanup, and completion.

Another rejected alternative is to introduce a generic third-party FSM
framework. The exporter does not need a full workflow engine; it needs a
small explicit lifecycle model that is easy to read and debug in this
codebase.

## Prior Art

- The exporter lifecycle fixes in PR #569, PR #598, and PR #614 all
  demonstrate how much coordination logic is currently encoded through
  ad hoc events and cancellation timing.
- The existing `ExporterStatus` values already represent a partial
  external state model; this JEP proposes making the internal state model
  equally explicit.
- State-oriented coordination is a common pattern in protocol servers and
  schedulers where cancellation, readiness, and teardown can overlap.

## Future Possibilities

- structured transition logging for each lease
- lifecycle metrics such as time spent in setup, before-hook, ready, and
  teardown phases
- a lightweight debug endpoint or status dump for current internal lease
  state
- simplification of `LeaseContext` and associated tests once the old
  event fields are fully removed

## Implementation History

- 2026-04-17: JEP proposed

## References

- [PR #423: Add Jumpstarter Enhancement Proposal (JEP) Process and Issue Template](https://github.com/jumpstarter-dev/jumpstarter/pull/423)
- [PR #569: Fix exporter deadlock when lease ends before before_lease_hook is set](https://github.com/jumpstarter-dev/jumpstarter/pull/569)
- [PR #598: fix: release lease on unused timeout when hooks are configured](https://github.com/jumpstarter-dev/jumpstarter/pull/598)
- [PR #614: Fix beforeLease gating for afterLease-only exporters](https://github.com/jumpstarter-dev/jumpstarter/pull/614)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
