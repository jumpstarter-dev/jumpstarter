# JEP-0020: Mock ExporterSet Performance Testing

| Field         | Value                                                     |
| ------------- | --------------------------------------------------------- |
| **JEP**       | 0020                                                      |
| **Title**     | Mock ExporterSet Performance Testing                      |
| **Author(s)** | @mangelajo (Miguel Angel Ajo Pelayo <miguelangel@ajo.es>) |
| **Status**    | Draft                                                     |
| **Type**      | Standards Track                                           |
| **Created**   | 2026-11-20                                                |
| **Requires**  | JEP-0019; ExporterSet support from JEP-0014               |

## Abstract

Extend the scale/performance tool from
[JEP-0019](JEP-0019-scale-performance-testing.md) with a mock ExporterSet provisioner
and an `exporterset-scaling` scenario. This exercises the real ExporterSet
controller framework without provisioning VMs, emulators, or physical targets.
It is **Phase 2, a separate implementation task after the standalone tool**;
JEP-0019 can be completed and shipped without this extension.

## Motivation

Standalone mock exporters exercise scheduling and routed connections, but not
controller-managed pool scaling, recycling, or cleanup. Using real virtual targets
for those tests adds cost and variable startup time. A lightweight test backend
lets us measure the shared ExporterSet framework separately from target-specific
provisioning.

## Proposal

A **mock ExporterSet** is a normal `ExporterSet` referencing a `VirtualTargetClass`
with a test-only provisioner, `mock.jumpstarter.dev`. It is not a new CRD or a mock
of the controller. The provisioner uses the existing provisioner interface to
render lightweight pods running the mock exporter runtime from JEP-0019, without
a target runtime or external service.

Unlike Phase 1, this work includes a new provisioner implementation in the
controller codebase and opt-in deployment configuration. It reuses the Phase 1
Python runner, load ranges, filters, metrics collector, and report formats.
It does not require a second runner or a new general-purpose framework.

Example addition to a JEP-0019 run configuration:

```yaml
scenarios:
  exporterset-scaling:
    provisioner: mock.jumpstarter.dev
    load:
      clients: 50
      concurrent_leases: {min: 10, max: 50, step: 10}
      min_replicas: 0
      max_replicas: 60
      min_available_replicas: 0
      hold: 1s
      warmup: 0s               # preserve initial cold-start measurements
      duration: 60s
      repetitions: 3
      operation_timeout: 120s
```

The mock provisioner must be explicitly enabled by the cluster administrator.
Preflight fails clearly if it is unavailable. This backend is a testing fixture,
not a production target type; it requires no hardware or privileged device access.

## Design Decisions

### DD-1: Extend the real framework after the standalone tool

**Alternatives considered:** Include this in the initial tool; simulate controller
status in Python; add a test provisioner as a follow-up.

**Decision:** Implement a separate follow-up using the existing provisioner interface.

**Rationale:** Phase 1 stays a bounded Python task with no controller changes.
Phase 2 exercises real reconciliation rather than bypassing the behavior under test.

## Design Details

```{mermaid}
flowchart TD
    Runner[Phase 1 runner with scaling scenario] --> Resources[Run-owned VirtualTargetClass and ExporterSet]
    Resources --> Controller[Real ExporterSet controller framework]
    Controller --> Provisioner[Test-only mock provisioner]
    Provisioner --> Pods[Mock exporter pods]
    Runner --> Clients[Lease demand]
    Clients --> Scheduler[Controller and scheduler]
    Pods --> Scheduler
    Runner --> Reports[Existing metrics and reports]
```

For each load step and repetition:

1. Create run-owned clients, a `VirtualTargetClass`, and an `ExporterSet` with the
   configured replica bounds. Let the controller create its child resources.
2. Drive lease demand using the existing lease helpers. Unlike `lease-scheduling`,
   do not require enough ready exporters before starting the measurement: waiting
   for capacity is intentional here.
3. Record pool convergence, request-to-lease-ready latency, and recycling behavior.
   Report initial cold-start samples separately from subsequent lease cycles.
4. Stop demand and release leases. Measure scale-down to the configured idle pool,
   allowing for the controller's cooldown, under a separate bounded observation
   deadline. Then delete run-owned resources and verify child cleanup.

Observe real exporter registration, readiness, and resource deletion; never patch
status or create the controller's children directly. Kubernetes pod scheduling,
image pulls, and exporter connection costs remain part of the measurements, so
these are not pure reconciliation timings.

Extend resource collection to ExporterSet controller replicas using the existing
collector. Reports combine pool size over time, scaling timings, and controller
CPU/memory/network usage with the Phase 1 workload measurements. Preserve partial
results and report missing metrics or convergence timeouts explicitly.

Reuse Phase 1 isolation, deadlines, ownership tracking, and crash cleanup. Stop
lease demand before deleting pools, wait for controller-owned children to be
removed, and report leftover resources rather than deleting unrelated objects.
Never change a pre-existing class, pool, or administrator-owned deployment.

## Test Plan

- **Unit:** Mock provisioner pod/config rendering and scenario parameter validation.
- **Integration:** Small scale-up, lease recycling, scale-down, and deletion tests
  through the real framework; cover unavailable provisioners, pod failures,
  convergence timeouts, and interrupted cleanup.
- **Hardware-in-the-loop:** Not required; the backend has no real target.
- **Manual:** Compare repeated runs and constrain ExporterSet controller resources
  to check that scaling timings and collected metrics reveal the impact.

## Acceptance Criteria

- [ ] The mock provisioner is opt-in and uses existing CRDs and framework interfaces.
- [ ] `exporterset-scaling` runs through the JEP-0019 runner and reports load-step results.
- [ ] Tests exercise scale-up, readiness, recycling, scale-down, and cleanup without
      direct status manipulation or real target provisioning.
- [ ] Reports include cold-start/convergence timings and ExporterSet controller
      resource metrics, or explicit missing-data indicators.
- [ ] Failure and cancellation preserve results and leave only reported leftovers.
- [ ] Phase 1 scenarios still work on clusters without the mock provisioner.

## Backward Compatibility

The provisioner and scenario are additive and opt-in. Existing provisioners and
Phase 1 configurations remain unchanged. No protocol or CRD changes are required;
clusters only enable the extension when testing ExporterSet behavior.

## Consequences

**Positive:** Repeatable framework performance tests without expensive virtual
targets, building on an already working tool.

**Negative:** Additional controller/provisioner maintenance and pod load. Mock
results do not represent real backend startup costs, and shared-cluster load can
affect other users.

## Rejected Alternatives

- **Require real QEMU or cloud targets:** Useful later for backend benchmarks, but
  unnecessarily costly and variable for testing the shared framework.
- **Include in Phase 1 or fake status:** Rejected for scope and fidelity reasons
  described in DD-1.

## References

- [JEP-0019: Scale and Performance Testing (Phase 1)](JEP-0019-scale-performance-testing.md)
- [JEP-0014: Virtual Scalable Exporters](JEP-0014-virtual-scalable-exporters.md)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
