# JEP-0015: Dynamic Exporter Labels for Device Software State

| Field             | Value                                                          |
| ----------------- | -------------------------------------------------------------- |
| **JEP**           | 0015                                                           |
| **Title**         | Dynamic exporter labels for device software state              |
| **Author(s)**     | @kirkbrauer (Kirk Brauer \<kirkebrauer@gmail.com\>)            |
| **Status**        | Draft                                                          |
| **Type**          | Standards Track                                                |
| **Created**       | 2026-08-19                                                     |
| **Updated**       | 2026-08-19                                                     |
| **Discussion**    | *TBD*                                                          |
| **Requires**      | —                                                              |
| **Supersedes**    | —                                                              |
| **Superseded-By** | —                                                              |

---

## Abstract *(mandatory)*

Exporter labels are fixed at registration: they describe what a device *is*
(`board`, `arch`, `virtual`), never what is *on* it. This JEP adds a
controller RPC that lets an exporter update a **reported** set of its own
labels at runtime, stored in the Exporter's status and merged into lease
selector matching. The primary use is recording the software version a device
is verifiably running — set by the exporter's own post-flash hook only after
the flash is verified, cleared before every flash — so that a lease selector
like `board=headunit,software-version=1.42.0` always lands on a device
already running exactly that build. This turns overnight fleet-reflash jobs
into a fail-closed reconciliation loop and enables warm/cold two-tier device
scheduling.

## Motivation *(mandatory)*

A lease selector today can only express identity, not state. Two workflows
suffer concretely:

1. **Fleet reflash.** Labs commonly reflash benches on a schedule (nightly,
   after a release) so that daytime test jobs start on a known build. With
   static labels there is no way to record whether last night's flash on a
   given bench *succeeded*. A job that leases `board=headunit` the next
   morning may get a bench running yesterday's build — or a half-flashed one
   — and every consumer must defensively re-flash, wasting the nightly work
   and minutes of every lease.

2. **Warm scheduling.** When several exporters match a selector, the
   controller cannot prefer one already running the requested software. Every
   lease pays the full flash cost even when a matching device sits idle. For
   virtual device fleets (QEMU pools, Cuttlefish) the same gap prevents
   "give me a device already provisioned with image X".

The current workaround — encoding the version into the static label set and
restarting the exporter after every flash — is a restart of the exporter
process per flash, races the lease it is serving, and loses the essential
property that the label should only appear after the new software is
*verified*.

Notably, labels are already self-reported: `RegisterRequest.labels` is sent
by the exporter over its authenticated channel. The exporter already owns its
label set; it just cannot change it without re-registering. This JEP removes
that restriction without changing the trust model.

### User Stories *(optional)*

- **As a** lab operator, **I want** a nightly job to reflash every bench and
  record the new version on success, **so that** morning CI leases select
  `software-version=<new>` and automatically route around any bench whose
  flash failed — which is also exactly my morning repair list.
- **As a** CI pipeline author, **I want to** lease a device by software
  version, **so that** my test run starts in seconds on a pre-flashed device
  instead of minutes behind a flash.
- **As a** driver author, **I want** my flasher driver to clear the version
  label before writing and set it only after verification, **so that** a
  device that loses power mid-flash is never selected by version.

## Proposal *(mandatory)*

An exporter gains the ability to replace its **reported labels** — a second
label set alongside the static labels it registered with — by calling a new
controller RPC at any time. The controller stores reported labels in the
Exporter resource's **status** and matches lease selectors against the
**union** of static and reported labels, with static labels winning any key
conflict.

From a driver or hook, the exporter API is one call:

```python
# in a flasher driver or a hook
self.exporter.set_reported_labels({})                    # clear before flash
flash_and_verify(image)
self.exporter.set_reported_labels({
    "software-version": "1.42.0",
})                                                       # earn it after verify
```

From the CLI, reported labels appear in `jmp get exporters -o wide` and are
selectable transparently:

```console
$ jmp create lease -l board=headunit -l software-version=1.42.0 --duration 1h
```

The fleet-reflash flow composes with the `enabled` field and `allowDisabled`
(JEP-0014 Phase 1):

```text
nightly job, per bench:
  1. disable exporter          (no new production leases — cordon)
  2. lease with allowDisabled  (maintenance lease reaches it anyway)
  3. clear reported labels     (crash-safe: unknown state = unlabeled)
  4. flash new image
  5. verify (boot gate / smoke)
  6. set software-version      (the label is EARNED, not assumed)
  7. release lease, re-enable exporter
```

A bench that dies at step 4 or fails step 5 carries no version label: it is
never selected by version, remains eligible for reflash, and is visible as a
straggler. The state machine is fail-closed by construction.

### API / Protocol Changes *(if applicable)*

One additive RPC on the exporter-facing `ControllerService`:

```proto
service ControllerService {
  // ...existing RPCs...

  // Replace this exporter's reported labels. The full reported set is
  // replaced atomically; an empty map clears it. Static labels from
  // registration are unaffected. Older controllers return UNIMPLEMENTED;
  // callers must surface that as "not supported by this controller".
  rpc UpdateReportedLabels(UpdateReportedLabelsRequest)
      returns (UpdateReportedLabelsResponse);
}

message UpdateReportedLabelsRequest {
  map<string, string> reported_labels = 1; // Full replacement set.
}

message UpdateReportedLabelsResponse {}
```

Controller CRD change (additive): `Exporter.status.reportedLabels
(map[string]string)`. Lease selector matching changes from
`match(selector, exporter.labels)` to
`match(selector, merge(status.reportedLabels, exporter.labels))` where
`exporter.labels` (static) wins key conflicts. Keys and values are validated
with the same syntax rules as Kubernetes labels; keys under the
`jumpstarter.dev/` namespace are rejected.

Both changes are backward compatible: old exporters never call the RPC and
behave exactly as today; old controllers return `UNIMPLEMENTED`, which the
client library reports cleanly.

### Hardware Considerations *(if applicable)*

The feature exists largely *because* of hardware: a physical bench persists
across flashes, so its software state must be tracked rather than assumed
(virtual fleets can destroy-and-replace instead — see JEP-0014's
`ExitAndReplace`). Behavior in degraded states is the core of the design: a
device that fails or loses power mid-flash ends up unlabeled, which is the
safe state — unselectable by version, eligible for repair. No new privileged
access is required; the RPC rides the exporter's existing authenticated
controller connection.

## Design Decisions *(mandatory for Standards Track)*

### DD-1: Reported labels live in Exporter status, not metadata

**Alternatives considered:**

1. **Patch `metadata.labels`** on the Exporter resource directly.
2. **A `status.reportedLabels` field**, merged at selector-match time.

**Decision:** Option 2.

**Rationale:** Exporter resources are increasingly declared by reconcilers —
GitOps tools and the JEP-0014 ExporterSet controller both own the resources
they create. A runtime patch to `metadata.labels` would be reverted on the
next reconcile (or produce permanent diff noise). Status is the established
Kubernetes home for "observed truth reported by the workload", it is ignored
by declarative owners, and it keeps a clean provenance split: spec/metadata =
what the operator declared, status = what the device reported.

### DD-2: Full replacement of the reported set, not incremental patch

**Alternatives considered:**

1. **Patch semantics** — a map of upserts plus a list of removals.
2. **Full replacement** — the request carries the entire reported set.

**Decision:** Option 2.

**Rationale:** Replacement is idempotent, which is what the crash-safety
ordering needs: a hook that re-sends after a retry converges to the same
state, and "clear" is simply the empty map rather than a tombstone protocol.
Reported label sets are small; the bandwidth argument for patching does not
apply.

### DD-3: Static labels win conflicts; reported labels cannot shadow them

**Alternatives considered:**

1. **Last-writer-wins** across both sets.
2. **Static wins**; a reported key that collides with a static key is
   ignored for matching (and surfaced as a warning condition).

**Decision:** Option 2.

**Rationale:** Static labels are the exporter's operator-declared identity
(`board`, `virtual`, pool membership). Allowing runtime reports to shadow
them would let a misbehaving exporter re-home itself into a different pool's
selector space mid-flight. Deterministic precedence keeps identity stable
and confines the dynamic set to state.

### DD-4: The RPC is exporter-authenticated; there is no client path

**Alternatives considered:**

1. **Allow lease-holding clients** to label the exporter they hold.
2. **Exporter identity only** (plus, as today, anyone with Kubernetes RBAC
   on the CRD).

**Decision:** Option 2.

**Rationale:** Labels already originate exclusively from the exporter's
authenticated identity at `Register`; this JEP deliberately preserves that
trust boundary — it changes *when* labels can be set, not *who* can set
them. A client path would let any tenant holding a short lease forge state
that outlives the lease (e.g. claim a version that was never flashed). A
client that legitimately drives a flash does it through a driver call, so
the label update still executes — and is verified — on the exporter side.

### DD-5: A dedicated RPC rather than re-calling Register

**Alternatives considered:**

1. **Make `Register` idempotent** and have exporters re-register with new
   labels.
2. **A dedicated `UpdateReportedLabels` RPC.**

**Decision:** Option 2.

**Rationale:** `Register` carries the full driver-instance report and
participates in session/identity bookkeeping; overloading it for a label
change during an active lease risks disturbing state that has nothing to do
with labels. A small additive RPC is trivially backward compatible (the
`GetServiceEndpoints` precedent already establishes the
UNIMPLEMENTED-means-absent convention) and keeps `Register`'s semantics
untouched.

## Design Details *(mandatory for Standards Track)*

**Data flow.** The exporter-side client library exposes
`set_reported_labels(dict)`; it sends `UpdateReportedLabelsRequest` on the
existing authenticated channel. The controller validates syntax (Kubernetes
label rules; reserved `jumpstarter.dev/` namespace rejected; bounded count
and size), writes `status.reportedLabels` via the status subresource, and
records an event. Selector matching in lease acquisition reads the merged
view; no lease-side API changes.

**Crash safety.** The contract consumers rely on is ordering, not
transactionality: *clear before mutating the device, set only after
verification*. The controller does not interpret label semantics; the
fail-closed property emerges from the ordering plus the fact that an absent
label never matches a version selector. Driver authors are the audience for
this contract, and the flasher driver documentation carries it.

**Concurrency.** Updates from one exporter serialize on the status write
(optimistic concurrency via resourceVersion, retried in the controller). A
label update during an active lease is legal and immediate — it affects
future selector matching only, never the current lease.

**Failure modes.** Controller unreachable: the call fails; hooks treat a
failed *set* as a failed flash step (the device stays unlabeled — safe), and
a failed *clear* as fatal for the maintenance job (do not flash a device you
could not mark unknown). Old controller: `UNIMPLEMENTED` is surfaced as
"reported labels unsupported"; nothing silently degrades.

**Security.** No new trust surface (DD-4). The validation limits bound the
damage of a compromised exporter to its own status field, which it could
already influence at registration; it still cannot shadow its declared
identity (DD-3).

## Test Plan *(mandatory for Standards Track)*

### Unit Tests

Selector matching over merged label sets, including conflict precedence
(DD-3), empty reported sets, and reserved-namespace rejection. Client
library: replacement semantics, UNIMPLEMENTED surfacing.

### Integration Tests

Controller e2e: register exporter → `UpdateReportedLabels` → lease by
reported label succeeds; clear → same lease request queues/fails; static
shadow attempt is ignored and warned; status survives exporter reconnect;
GitOps-style reapply of the Exporter resource does not clobber status.

### Hardware-in-the-Loop Tests

A scripted maintenance cycle against a real (or QEMU-pool) exporter:
disable → allowDisabled lease → clear → flash → verify → set → enable,
plus the negative path — kill the flash mid-write and assert the exporter
ends unlabeled and a version-selecting lease does not match it.

### Manual Verification

`jmp get exporters -o wide` shows reported labels distinctly from static
ones; the events stream shows label transitions with timestamps.

## Acceptance Criteria *(mandatory for Standards Track)*

- [ ] `UpdateReportedLabels` implemented in controller and Python exporter
      library, with the RPC documented in the protocol reference.
- [ ] `Exporter.status.reportedLabels` populated and visible in `jmp get
      exporters`; selector matching uses the merged view with static
      precedence.
- [ ] Old-controller interaction returns a clear "unsupported" error, and an
      exporter that never calls the RPC behaves byte-identically to today.
- [ ] The disable → flash → verify → label → enable cycle passes in e2e,
      including the mid-flash-kill negative path leaving the device
      unlabeled.
- [ ] Driver-author documentation states the clear-before/set-after-verify
      contract.

## Backward Compatibility *(mandatory for Standards Track)*

All changes are additive: a new RPC (old controllers answer `UNIMPLEMENTED`,
the established convention for optional services), a new status field (old
CRD readers ignore it), and unchanged matching behavior for exporters with
an empty reported set. Wire format, existing RPCs, and the `Register`
message are untouched. Mixed-version fleets work: an old exporter under a
new controller simply has no reported labels; a new exporter under an old
controller gets a clean error from the one new call.

## Consequences *(mandatory)*

### Positive

- Lease selectors can express software state, enabling
  lease-by-version, warm/cold two-tier scheduling, and fleet reflash as a
  fail-closed reconciliation loop (desired version in the operator's config
  management; the earned label as observed status; the nightly job as
  reconciler).
- Failed flashes become self-quarantining and self-reporting: unlabeled
  devices are both unselectable and the repair list.
- The mechanism is generic: reported labels can carry any observed state
  (installed test harness version, calibration date), not only software
  version.

### Negative

- A second label set adds a concept: operators must learn the
  static-vs-reported split and its precedence rule, and tooling must render
  both.
- The fail-closed property depends on driver authors honoring the ordering
  contract; the controller cannot enforce semantics it does not interpret.

### Risks *(optional)*

- Label cardinality creep (per-build hashes as label values) could bloat
  selector indexes; bounded set size and documentation of "short version in
  the label, full digest elsewhere" mitigate.

## Rejected Alternatives *(mandatory)*

Per-decision alternatives are in DD-1…DD-5. Higher-level alternatives:

- **Doing nothing / external inventory:** track versions in a database beside
  Jumpstarter and pre-resolve exporter names in job scripts. Rejected: it
  bypasses the controller's scheduling (name-pinned leases weld jobs to
  devices), races reflash jobs, and duplicates state the lease plane must
  agree on anyway.
- **Modeling state as a separate CRD** (e.g. a DeviceState resource joined at
  lease time): heavier API surface for the same matching semantics; labels
  are already the established selection mechanism and the smallest change
  that composes with every existing selector consumer.

## Prior Art *(optional)*

- **Kubernetes node self-labeling:** kubelets report labels for their own
  Node, with the NodeRestriction admission plugin bounding what they may
  claim — the same self-report-with-bounded-authority shape as DD-3/DD-4.
- **LAVA health checks:** LAVA runs periodic health-check jobs per device and
  only schedules onto devices whose last health check passed — the
  earned-state-gates-scheduling pattern this JEP generalizes; here the
  "health" is a verified software version and the gate is a label selector.
- **Node feature discovery (NFD):** runtime-detected hardware/software
  features published as node labels for scheduler consumption.

## Unresolved Questions *(optional)*

- Should reported-label changes be recorded as Kubernetes events, a status
  condition with transition time, or both? (Leaning both; decidable at
  implementation.)
- Whether `jmp` should grow a convenience verb (`jmp exporter label …`) for
  operators, in addition to the exporter-library API.

## Future Possibilities *(optional)*

Explicitly not part of this proposal:

- **Two-tier scheduling sugar:** a client-side "prefer
  `software-version=X`, else any `board` match + flash-on-lease" policy.
- **Gang lease acquisition:** N matching leases granted atomically or not at
  all, with a deadline — today the N-th request queues indefinitely, which
  makes parallel fan-out fragile.
- **Lease duration ceilings** (`maxLeaseDuration`) to bound fleet-rollout
  drain time.
- **ExporterSet rollout strategies** (surge/blue-green over template
  revisions) for virtual pools — the natural JEP-0014 follow-up, with
  reported labels supplying the observed-version half for mixed fleets.

## Implementation History

- 2026-08-19: Initial draft.

## References

- JEP-0014 Phase 1: `enabled` on Exporter and `allowDisabled` on Lease
  (jumpstarter-dev/jumpstarter#863, #969) — the cordon half of the
  maintenance flow.
- `GetServiceEndpoints` (`protocol/proto/jumpstarter/v1/jumpstarter.proto`)
  — the UNIMPLEMENTED-means-absent convention this JEP reuses.
- Kubernetes NodeRestriction admission; LAVA device health checks.

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
