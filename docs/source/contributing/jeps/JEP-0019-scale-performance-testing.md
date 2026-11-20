# JEP-0019: Scale and Performance Testing

| Field         | Value                                                     |
| ------------- | --------------------------------------------------------- |
| **JEP**       | 0019                                                      |
| **Title**     | Scale and Performance Testing                             |
| **Author(s)** | @mangelajo (Miguel Angel Ajo Pelayo <miguelangel@ajo.es>) |
| **Status**    | Draft                                                     |
| **Type**      | Standards Track                                           |
| **Created**   | 2026-11-19                                                |
| **Requires**  | None                                                      |

## Abstract

Introduce **jumpstarter-overdrive**, a Python tool for repeatable scale and
performance tests against existing or disposable Jumpstarter clusters. It creates
and destroys clients, exporters, and leases, and runs mock exporters using real
Jumpstarter connections without physical hardware. Configurable scenarios sweep
load levels and produce measurements that help locate bottlenecks in the
controller and routers. The working name is
`jumpstarter-overdrive`; `jumpstarter-tempest` is an alternative.

## Motivation

Correctness tests do not tell us how lease latency changes as concurrent demand
grows, how quickly exporter pools recover from churn, or when router throughput
stops scaling. Ad hoc scripts also make comparisons between versions and cluster
sizes difficult. We need repeatable workloads that distinguish slow scheduling
with available exporters from time spent provisioning exporters or establishing
connections.

## Proposal

Add an optional package at `python/packages/jumpstarter-overdrive`, with a CLI and
a Python API sharing the same runner. The runner accepts an explicit Kubernetes
context and namespace, uses existing Jumpstarter APIs, and can run on a workstation
or as a job inside a cluster. Cluster provisioning remains outside the tool.

A **scenario** is a Python class with setup, load-step, and cleanup methods. A
**run** selects scenarios and executes each scenario's own load sweep,
recording raw samples and a summary. Scenarios run sequentially by default so
their loads do not interfere.

### Implementation scope: Phase 1

This JEP defines the standalone Python tool only. It requires **no changes to the
controller, operator, CRDs, or provisioner framework**. The runner manages mock
exporters directly through existing APIs; these are not controller-managed pools.

Implement and review it in small increments:

1. Runner, configuration, cleanup, and `lease-scheduling`, with raw timings and a
   basic summary working end to end on a small cluster.
2. Independent client/exporter churn and router-traffic scenarios, reusing those
   helpers rather than building a second runner.
3. Resource-metric collection and the readable reports described below.

**Phase 2 is separate extension work:**
[JEP-0020: Mock ExporterSet Performance Testing](JEP-0020-mock-exporterset-performance-testing.md)
adds a mock provisioner and ExporterSet scenarios. It is not a prerequisite or an
acceptance criterion for this JEP. No Phase 2 scaffolding is required in Phase 1.

### Initial scenarios

| Scenario           | Workload                                                                                                                              | Primary measurements                                                     |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `lease-scheduling` | Repeatedly acquire, briefly hold, release, and delete leases against a ready mock-exporter pool.                                      | Assignment, lease-ready, and release latency, leases/s, errors.          |
| `client-churn`     | Repeatedly create and delete clients, without creating exporters or leases.                                                           | Client creation/deletion latency, cycles/s, errors.                      |
| `exporter-churn`   | Repeatedly create exporters, connect mock runtimes, wait until available, then disconnect and delete them, without requesting leases. | Exporter creation/deletion latency, time to available, cycles/s, errors. |
| `router-traffic`   | Open leased sessions and exchange fixed-size echo payloads through routers.                                                           | Connection latency, round-trip latency, bytes/s, failures.               |

`lease-scheduling` also covers lease churn, reusing clients and exporters created
during setup. Keeping client and exporter churn separate isolates their lifecycle
costs from lease scheduling.

Mock exporters are real exporter runtimes with lightweight, hardware-free drivers
and distinct credentials. They connect to the cluster's controller and routers as
normal exporters; the tool does not fake online status or write lease assignments.


### Example configuration and CLI

Each scenario owns its `load` configuration: resource counts, concurrency, timing,
and repetitions. Numeric load dimensions accept a fixed value or a
`{min, max, step}` range (inclusive upper bound when reached). Ranges advance
together, not as a Cartesian product; fixed values apply to every step. All ranges
within a scenario must produce the same number of steps, with positive increments
and valid bounds. This keeps the initial sweep model simple.

For example, `lease-scheduling` below tests `(exporters, clients, concurrent_leases)`
at `(20, 10, 10)`, `(40, 20, 20)`, through `(100, 50, 50)`. Keep a dimension fixed
to isolate the effect of changing another. Timing defaults, when omitted, are
`warmup: 10s`, `duration: 60s`, `repetitions: 3`, and `operation_timeout: 30s`.

```yaml
cluster:
  context: kind-jumpstarter
  namespace: jumpstarter-perf

scenarios:
  lease-scheduling:
    load:
      exporters: {min: 20, max: 100, step: 20}
      clients: {min: 10, max: 50, step: 10}
      concurrent_leases: {min: 10, max: 50, step: 10}
      hold: 1s
      warmup: 10s
      duration: 60s             # measured time per step
      repetitions: 3
      operation_timeout: 30s
  client-churn:
    load:
      concurrent_clients: {min: 10, max: 100, step: 10}
      duration: 30s
  exporter-churn:
    load:
      concurrent_exporters: {min: 5, max: 25, step: 5}
      duration: 60s

  router-traffic:
    load:
      exporters: 60
      clients: 50
      concurrent_sessions: {min: 10, max: 50, step: 10}
      payload_bytes: 4096

output: ./results
```

```shell
jumpstarter-overdrive list-scenarios
jumpstarter-overdrive run --config perf.yaml --include lease-scheduling
jumpstarter-overdrive run --config perf.yaml --include client-churn
jumpstarter-overdrive run --config perf.yaml --exclude exporter-churn
jumpstarter-overdrive cleanup --config perf.yaml --run-id <run-id>
```

Without filters, all configured scenarios run. Include/exclude filters match
scenario names; exclusions win, and unknown names or an empty selection are
configuration errors. Unsupported selected scenarios fail preflight explicitly
rather than silently disappearing from results.

## Design Decisions

### DD-1: Python scenarios, declarative run configuration

**Alternatives considered:** Python classes with YAML parameters; a YAML workload
language; shell scripts invoking the CLI.

**Decision:** Use Python classes and a small shared configuration model.

**Rationale:** Python can reuse Jumpstarter libraries and express scenario-specific
behavior without designing another programming language. YAML keeps common runs
reproducible without requiring code changes.

### DD-2: Real protocol paths, simulated hardware

**Alternatives considered:** Real runtimes with mock drivers; direct CRD/status
mutation; physical exporter fleets.

**Decision:** Simulate hardware only, keeping authentication, exporter connections,
lease requests, and routed traffic real.

**Rationale:** Bypassing those paths would hide the bottlenecks being measured;
physical hardware would make large tests costly and less repeatable.

## Design Details

```{mermaid}
flowchart TD
    Config[Configuration and filters] --> Runner[Python scenario runner]
    Runner --> API[Kubernetes API: test resources]
    Runner --> Clients[Test clients]
    Runner --> Mocks[Mock exporter runtimes]

    Clients --> Controller[Controller and scheduler]
    Mocks --> Controller

    Clients <--> Routers[Routers]
    Routers <--> Mocks

    Runner --> Results[Samples and summaries]
```

### Scenario lifecycle

The runner provides resource helpers, bounded asynchronous workers, deadlines,
and a sample recorder. Scenario methods have a small interface:

```python
class LeaseScheduling(Scenario):
    name = "lease-scheduling"

    async def setup(self, ctx, step):
        await ctx.create_clients_and_mock_exporters(
            clients=step.clients, exporters=step.exporters
        )
        await ctx.wait_for_available_exporters()

    async def run_step(self, ctx, step):
        await ctx.run_lease_workers(step.concurrent_leases, hold=step.hold)

    async def cleanup(self, ctx):
        await ctx.delete_owned_resources()
```

This is illustrative, not a new stable public API. Helpers record individual
operations and track ownership; cleanup is invoked even after partial setup.

The runner expands each scenario's ranges into concrete step configurations.
For each step, each repetition performs setup, warmup, measurement, draining, and
cleanup before the next repetition or step. Setup and cleanup are outside the
measurement window, so changing pool sizes does not contaminate scheduling
latency. Scenario-specific operations, such as exporter creation in churn tests,
remain inside the measured workload.

Workers repeatedly perform one operation cycle at a time: the scenario's
concurrency bounds in-flight lease cycles, client/exporter lifecycles, or routed
sessions, not a promised request rate. Record achieved concurrency and throughput;
a slowing system will reduce request rate in this closed-loop model.
Fixed-arrival-rate tests are future work.

For `lease-scheduling`, validate that every step has at least as many exporters
and clients as concurrent leases. Setup must confirm enough matching, online,
available exporters and authorized clients for that step. Selectors include
the run identity so unrelated exporters cannot satisfy requests. Warmup samples
are excluded. Loss of capacity during measurement is reported as a capacity
shortfall, not silently attributed to scheduler latency.

### Measurements and bottleneck analysis

Use monotonic clocks for durations and UTC timestamps for correlation. Record
these boundaries separately:

- **Assignment:** immediately before sending a lease request to observing its
  exporter assignment.
- **Lease ready:** the same start to observing the lease's ready condition.
- **Session ready:** lease ready to the first successful routed driver call.
- **Release:** release request to observing the exporter available again.

These are client-observed durations, including network and observation overhead,
not pure scheduler execution times. Use watches where supported and record the
observation method. Churn and router traffic have their own scenario-specific
samples.

Write JSON Lines samples and a JSON summary containing counts, p50/p95/p99,
throughput, errors, and timeouts per scenario, step, and repetition. Failed and
timed-out operations remain visible alongside successful latency percentiles.
Include resolved configuration, run/resource IDs, software versions, cluster
component replicas/resources where readable, and runner CPU/memory usage. Never
include credentials in artifacts.

### Controller and router resource metrics

During each run, collect resource metrics for the running controllers and routers,
including all replicas:

| Metric  | Samples                            | Report summary per load step                        |
| ------- | ---------------------------------- | --------------------------------------------------- |
| CPU     | Usage in cores.                    | Mean and peak usage.                                |
| Memory  | Working set in bytes.              | Mean and peak working set.                          |
| Network | Receive/transmit bytes per second. | Mean and peak rates, total bytes in each direction. |

Query an existing Prometheus-compatible monitoring service using a configurable
endpoint, sampling interval, and component namespace/label selectors. Component
namespaces may differ from the test namespace. Use Kubernetes container/pod
metrics (for example, kubelet/cAdvisor metrics) for resource usage; application
metrics alone do not necessarily expose network or container memory usage. This
does not require installing a new telemetry service or granting the runner
privileged node access.

Store timestamped resource samples alongside workload samples, with component,
pod identity, and container identity where applicable. Reports include time
series and per-step summaries, both per replica and aggregated by component,
aligned with scenario, concurrency, repetition, latency, and throughput. Count
pod-level network metrics once per pod, not once per container, and handle
restarts and counter resets without producing false spikes. Preserve warmup
boundaries and report sampling gaps or unavailable metrics explicitly, never as
zero usage. Monitoring access uses read-only credentials kept out of artifacts.

Correlate these measurements with controller work queues/reconciliation and
router connections using observability from
[JEP-0013](JEP-0013-observability-telemetry-logs.md). On clusters without monitoring,
workload-only runs remain supported, but reports clearly mark resource metrics as
unavailable. Shared-component metrics include other cluster activity and cannot
alone prove causation. Runner saturation must also be considered before
interpreting a plateau as a cluster limit.

### Preliminary result examples

The following formats are provisional, and **all values are illustrative, not
benchmark results**. Each run writes a small artifact bundle:

```text
results/overdrive-example/
  config.yaml         # resolved configuration, without credentials
  operations.jsonl    # individual operation timings and outcomes
  resources.jsonl     # timestamped controller/router resource samples
  summary.json        # run metadata and per-step/per-repetition summaries
  report.md           # readable tables and links to time-series data
```

Example console/report excerpt for `lease-scheduling`, repetition 1 (steps 3 and 4
omitted here). Latency percentiles describe successful lease-ready operations;
errors and timeouts are separate counts, not included in those percentiles.

```text
Step  Exporters  Clients  Leases  Completed/s  Ready p50/p95/p99 (ms)  Errors  Timeouts
1     20         10       10      8.4          55 / 110 / 160          0       0
2     40         20       20      16.3         60 / 125 / 185          0       0
5     100        50       50      30.0         210 / 680 / 940         4       2
```

Resource summary for step 1 over the same measurement window, summed across each
component's replicas before computing mean/peak values:

| Component  | CPU mean/peak (cores) | Memory mean/peak (MiB) | Network RX/TX mean (KiB/s) |
| ---------- | --------------------- | ---------------------- | -------------------------- |
| Controller | 0.30 / 0.52           | 180 / 205              | 85 / 62                    |
| Router     | 0.08 / 0.14           | 95 / 102               | 40 / 39                    |

The full report also includes per-replica values, peak network rates, byte totals,
and metric coverage. If collection is unavailable, show `N/A` with the reason
rather than an empty or zero-valued resource table.

Example entry from `summary.json` (other timings and resource summaries omitted):

```json
{
  "run_id": "overdrive-example",
  "scenario": "lease-scheduling",
  "step": 1,
  "repetition": 1,
  "load": {"exporters": 20, "clients": 10, "concurrent_leases": 10},
  "measurement_seconds": 60,
  "completed_cycles": 504,
  "errors": 0,
  "timeouts": 0,
  "completed_cycles_per_second": 8.4,
  "lease_ready_ms": {"p50": 55, "p95": 110, "p99": 160},
  "resource_metrics": {"status": "complete", "samples_file": "resources.jsonl"}
}
```

Store the full resolved load dimensions with each summary, not just a step number,
so runs with different ranges can be compared. Other scenarios use the same
structure with their own dimensions and measurements, such as client lifecycle
latency or router throughput.

### Isolation and failures

Require an explicit target context/namespace, validate permissions and resource
limits before generating load, and display the selected workload. Use a dedicated
test namespace where possible, run-specific names/labels, and selectors restricted
to owned exporters. Existing clusters are supported, but shared controllers and
routers can still be disrupted: high-load runs belong on disposable clusters or
in an agreed maintenance window.

Give every operation a deadline and every lease a bounded lifetime. On error or
interrupt, stop new work, preserve partial results, release leases, stop mock
exporters, and delete only run-owned resources. Track owned resource identities
persistently so explicit cleanup can recover after a crash; report cleanup
failures and exit nonzero. Do not delete a pre-existing namespace or unrelated
resources. Any retries are bounded and recorded rather than hiding failures.

## Test Plan

- **Unit:** Per-scenario range expansion (including fixed values and mismatched
  range lengths), capacity validation, filters, lifecycle cancellation, timing and
  aggregation, ownership checks, and partial-result handling. Cover resource
  metric aggregation, counter resets, missing samples, and load-step alignment.
- **Integration:** Small sweeps on a disposable cluster, covering all scenarios,
  unavailable exporters, deadlines, disconnects, and interrupted-run cleanup.
  Check controller/router resource collection
  with monitoring enabled and explicit missing-data reporting when unavailable.
- **Hardware-in-the-loop:** Not required; all scenarios use simulated hardware.
- **Manual:** Compare repeated baselines, then constrain controller or router
  resources and verify that timings plus telemetry expose the affected path.

## Acceptance Criteria

- [ ] A filtered run independently expands and executes each scenario's load ranges
      and repetitions, recording the resolved dimensions for every result.
- [ ] Scheduling tests verify ready capacity before measuring lease latency.
- [ ] Client and exporter churn run independently, without generating lease load.
- [ ] Router traffic can be measured independently of churn and scheduling tests.
- [ ] Artifacts include raw samples, percentiles, throughput, failures, and run metadata,
      plus a readable report with workload and resource summaries.
- [ ] With monitoring configured, reports include controller and router
      CPU/memory/network time series and summaries aligned to load steps;
      missing metrics are explicitly identified.
- [ ] Normal and interrupted runs clean up; crash leftovers can be explicitly removed
      without touching unrelated resources.
- [ ] The same configuration works against existing and disposable compatible clusters,
      without ExporterSet support or controller/operator changes.

## Backward Compatibility

This is an opt-in tool using existing APIs; no protocol, controller, operator, or
existing CLI changes are required. None of its scenarios require ExporterSet
support. A monitoring stack is optional. No migration is required for existing
deployments, and the tool can ship independently of Phase 2.

## Consequences

**Positive:** Repeatable capacity curves, comparable version-to-version results,
and targeted workloads for investigating controller and router
bottlenecks without a hardware fleet.

**Negative:** The tool and mock workloads need maintenance. Results depend on the
cluster, network, and generator capacity; simulated hardware does not represent
all real driver behavior. Load tests can disrupt other users on shared clusters.

## Rejected Alternatives

- **Only extend correctness tests:** Useful for smoke coverage, but not a substitute
  for configurable sweeps and reusable performance artifacts.
- **Start with a distributed load-testing platform:** Defer until a single runner
  demonstrably limits useful scale; keep the first implementation small.
- Protocol shortcuts and physical fleets are covered by DD-2.

## References

- [JEP-0013: Metrics, Tracing, and Log Observability](JEP-0013-observability-telemetry-logs.md)
- [JEP-0020: Mock ExporterSet Performance Testing (Phase 2)](JEP-0020-mock-exporterset-performance-testing.md)

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
