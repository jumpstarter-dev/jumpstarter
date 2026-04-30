# JEP-0011: Metrics, Tracing, and Log Observability

| Field             | Value                                                                 |
| ----------------- | --------------------------------------------------------------------- |
| **JEP**           | 0011                                                                  |
| **Title**         | Metrics, Tracing, and Log Observability                               |
| **Author(s)**     | @mangelajo (Miguel Angel Ajo Pelayo <miguelangel@ajo.es>              |
| **Status**        | Discussion                                                            |
| **Type**          | Standards Track                                                       |
| **Created**       | 2026-04-23                                                            |
| **Updated**       | 2026-04-29                                                            |
| **Discussion**    | https://github.com/jumpstarter-dev/jumpstarter/pull/631               |
| **Requires**      | —                                                                     |
| **Supersedes**    | —                                                                     |
| **Superseded-By** | —                                                                     |

---

## Abstract

This JEP defines an optional, cross-component observability model for
Jumpstarter covering lease context metadata, structured operational events,
exporter/driver metrics, and standardized logging. It targets direct integration
with Prometheus (scrape), Loki (log aggregation), and Perses (dashboards) —
without mandating OpenTelemetry — and introduces an optional in-cluster
Jumpstarter Telemetry service that aggregates data from exporters and clients so
that edge processes never need Loki or cluster-scrape credentials.
Implementation is expected to land in phases; this JEP describes the end state
and compatibility rules.

### Phases

| Phase | Scope | Key deliverables |
| ----- | ----- | ---------------- |
| 1 | Structured logging + lease context | `spec.context` CRD field; JSON structured logs for all long-running services; correlation fields (`lease_id`, `exporter`, `operation`, `result`) in every log line. |
| 2 | Metrics endpoints | `/metrics` scrape endpoints on Controller and Router; exporter counter/histogram/gauge metrics with `driver_type`; Prometheus exemplars for high-cardinality context. |
| 3 | Telemetry service | Optional `jumpstarter-telemetry` Deployment managed by the operator; exporter and client data aggregation; Loki push for edge-originated logs and events. |
| 4 | In-cluster log scraping | Operator configures log shipper integration (Promtail, Grafana Alloy, Vector) for Controller/Router pod logs; `ServiceMonitor` CRDs for Prometheus autodiscovery. |
| 5 | Dashboards + alerting | Perses CRD dashboards; starter alert rules; documentation and operator integration. |

Each phase is independently useful and builds on the previous ones.
Phase 1 can ship without any later phase; operators who only need
structured logs benefit immediately. Phase 2 adds scrape-ready metrics
without requiring the Telemetry service.

## Motivation

Today, operators and CI maintainers need to answer questions that raw Kubernetes
objects and ad hoc text logs do not always answer in one place:
- *Which pipeline or image was being tested on this lease?*
- *How often do flashes fail on this exporter?*
- *What lease or user correlates a controller line with a failure on the client?* 

The `Lease` API already models scheduling and assignment; it does
not yet provide a first-class, documented place for run metadata or a standard
for lease-scoped operational events (beyond generic `conditions`).

Exporters expose work to drivers, but there is no shared model for driver- or
exporter-level metrics that a monitoring stack can scrape or receive.

### User Stories

- **As a** lab operator, **I want to** see flash success/failure rates per
  exporter in a Prometheus dashboard, **so that** I can spot failing hardware
  before CI teams notice.
- **As a** CI pipeline author, **I want to** attach my build ID and image
  digest to a lease, **so that** post-mortem queries in Loki can filter all
  logs for one pipeline run across controller, exporter, and client.
- **As a** platform engineer, **I want** exporter processes to send telemetry
  without holding Loki or Prometheus credentials, **so that** I do not have to
  distribute and rotate secrets on every lab machine.
- **As an** AI agent orchestrating CI, **I want** machine-readable structured
  logs and metric exemplars with lease context, **so that** I can
  programmatically identify failing exporters and correlate test results
  without parsing free-form text.

## Proposal

### Concepts

- **Lease context** — Identifiers and labels supplied by a client or CI and
  associated for the life of a lease, propagated where safe so metrics, logs,
  and traces can be filtered and joined.
- **Lease events** (or *operations*) — Annotated, structured log entries
  recording significant actions (for example *flash started*, *flash failed*,
  *image reference*) with typed fields, queryable in **Loki** alongside
  regular logs and distinct from higher-frequency debug output (see **DD-2**).
- **Exporter metrics** — Counters (operations, bytes), histograms (operation
  duration), and gauges (active sessions) exposed from the exporter and
  enriched by individual drivers via the `driver_type` label. Each driver
  selects a category from a predefined set in jumpstarter core (e.g.
  `storage`, `power`, `network`, `serial`, `console`, `video`).
  Composite drivers (e.g. Renode, QEMU) that bundle multiple sub-drivers
  do not emit a single top-level category for delegated work. Instead,
  each sub-driver emits its own `driver_type` when it performs an
  operation — a Renode storage sub-driver emits `driver_type="storage"`,
  its power sub-driver emits `driver_type="power"`, and so on. Any
  top-level methods on the composite driver itself (e.g. VM lifecycle)
  emit `driver_type="composite"`.
- **Jumpstarter Telemetry** (optional) — a dedicated
  component with a well-known ingest path and the same trust
  model (mTLS, ServiceAccount) as Controller/Router;
  it isolates Loki/series work from the reconciler hot path (see
  **DD-7**). Multi-replica HA and PromQL `sum` aggregation are
  covered in **DD-8**; best-effort idempotency for informative metrics in
  **DD-9**.

### What users see

- When creating a lease, clients (or their tooling) can attach metadata via
  CRD fields and/or `spec.context` using documented
  keys and size limits. Example keys might include a build / pipeline
  identifier, image digest, or VCS.
- The controller and/or data plane write structured, annotated log events
  (see **DD-2**) for significant operations such as flash attempts and outcomes.
- Exporters send increments to the Jumpstarter Telemetry
  service over the existing exporter↔control-plane trust boundary;
  the in-cluster side then POSTs to Loki and exposes `/metrics`
  for scrape (see **DD-3**, **DD-7**), with cluster credentials, avoiding
  per-exporter Loki and metrics secrets. The same path can carry operator-chosen structured log lines
  and events (not unbounded default client chatter — see *Control-plane
  aggregation* below).
- The `jmp` CLI output remains human-readable, but when a Telemetry
  endpoint is available, `jmp` also pushes structured JSON logs to the
  Jumpstarter Telemetry service for Loki ingest.

### API / Protocol Changes

#### CRD (Lease)

Additive changes only for the `spec.context` field. Backwards compatibility
by making this field empty by default.

#### gRPC: Telemetry endpoint discovery (`jumpstarter.proto`)

A new RPC on the existing `ControllerService` lets both exporters and
clients discover the optional Telemetry endpoint:

```protobuf
// Added to ControllerService
rpc GetServiceEndpoints(GetServiceEndpointsRequest)
    returns (GetServiceEndpointsResponse);

message GetServiceEndpointsRequest {}

message GetServiceEndpointsResponse {
  // Empty when telemetry is not enabled.
  repeated TelemetryEndpoint telemetry_endpoints = 1;
}

message TelemetryEndpoint {
  string endpoint = 1;           // gRPC address (host:port)
  string certificate = 2;        // Optional CA cert for the endpoint
}
```

Exporters call `GetServiceEndpoints` after `Register`; clients call it
after authentication. An empty `telemetry_endpoints` list means telemetry
is not deployed — callers skip all telemetry RPCs. Older controllers
that do not implement the method return `UNIMPLEMENTED`, which callers
treat identically to an empty list.

#### gRPC: Telemetry service (`telemetry.proto` — new file)

A new `protocol/proto/jumpstarter/v1/telemetry.proto` defines the
`TelemetryService` implemented by `jumpstarter-telemetry`. It has two
RPCs: one for metrics (reverse scrape) and one for log push.

##### Metrics: reverse scrape via `MetricsStream`

Exporters maintain a local `prometheus_client.CollectorRegistry` with
counters, histograms, and gauges. Rather than pushing increments, the
exporter opens a persistent bidirectional stream to the Telemetry
service; the Telemetry service periodically sends a scrape request
and the exporter responds with the output of
`prometheus_client.generate_latest()` in OpenMetrics text format.

```protobuf
service TelemetryService {
  // Persistent bidirectional stream: telemetry sends scrape requests,
  // exporter responds with full metric snapshots.
  rpc MetricsStream(stream MetricsStreamRequest)
      returns (stream MetricsStreamResponse);

  // Structured log / event push (used by both exporters and clients).
  rpc PushLogs(PushLogsRequest) returns (PushLogsResponse);
}

// Exporter → Telemetry
message MetricsStreamRequest {
  oneof msg {
    MetricsRegister register = 1;          // First message: identify this exporter
    MetricsScrapeResponse scrape_response = 2; // Subsequent: reply to a scrape
  }
}

message MetricsRegister {
  string identity = 1;              // Exporter CRD name (verified against mTLS and auth token by server)
}

message MetricsScrapeResponse {
  bytes metrics_text = 1;           // generate_latest() OpenMetrics output
  google.protobuf.Timestamp timestamp = 2;
}

// Telemetry → Exporter
message MetricsStreamResponse {
  oneof msg {
    MetricsScrapeRequest scrape_request = 1;
  }
}

message MetricsScrapeRequest {}       // "send your /metrics now"
```

The stream lifecycle:

1. Exporter opens the stream and sends `MetricsRegister`, the jumpstarter-telemetry
   service authenticates the exporter identity and labels from cluster information.
2. When Prometheus (or any scraper) hits the Telemetry service's
   `/metrics` endpoint, Telemetry fans out `MetricsScrapeRequest`
   to all connected exporters.
3. Each exporter calls `generate_latest(registry)` and replies with
   `MetricsScrapeResponse`.
4. Telemetry merges the responses and serves the combined result,
   adds and filters any necessary labels or exemplars from data.
   This on-demand approach avoids stale data and unnecessary
   background traffic; it can be changed to periodic pre-fetching
   later if scrape latency became problematic.

**Client-side metrics are not collected.** All metrically-interesting
operations are observable from the exporter side: `DriverCall` methods
run on the exporter and can be instrumented there. Client-side drivers
that orchestrate complex workflows (e.g. serial-console-driven
flashing) report outcomes back to the exporter via regular
`DriverCall` methods, keeping the exporter as the single source of
truth for metrics.

##### Logs: push via `PushLogs`

Both exporters and clients push structured log entries to the
Telemetry service for Loki ingest:

```protobuf
message PushLogsRequest {
  repeated LogEntry entries = 1;
}

message PushLogsResponse {
  uint32 accepted = 1;  // Entries accepted
  uint32 dropped = 2;   // Entries dropped (backpressure)
}

message LogEntry {
  google.protobuf.Timestamp timestamp = 1;
  string severity = 2;        // debug, info, warn, error
  string message = 3;
  string component = 4;       // Log stream label: cli, exporter
  string exporter = 5;        // Log stream label: exporter CRD name
  string lease_id = 6;        // High-cardinality, log body only
  string client = 7;          // High-cardinality, log body only
  string operation = 8;       // flash, power, etc.
  string result = 9;          // success, failure
  string driver_type = 10;    // storage, power, network, etc.
  map<string, string> extra_fields = 11;   // Driver-specific structured data
}
```

The Telemetry service maps `component` and `exporter` to Loki stream
labels and everything else into the JSON body, following the
cardinality rules in *Cardinality guidelines*. The `exporter` and
`client` fields are verified server-side with the authenticated
identity to prevent impersonation. Empty fields or details 
that can be obtained from lease_id are incorporated into the log.

#### gRPC: `AuditStream` removal (`jumpstarter.proto`)

The existing `AuditStream` RPC on `ControllerService` and its
`AuditStreamRequest` message are removed. Analysis of the codebase
shows this is dead code:

- The Go controller has no implementation — calls fall through to
  `UnimplementedControllerServiceServer` which returns
  `codes.Unimplemented`.
- No Python code (exporter or client) calls the RPC.
- No tests exercise it beyond generated stubs.

Its intended purpose (tracking exporter activity) is fully superseded
by `TelemetryService.PushLogs` with a richer, properly-designed
message format.

#### gRPC: `LogStreamResponse` enrichment (`jumpstarter.proto`)

The existing `LogStream` RPC on `ExporterService` is kept — it serves
a fundamentally different purpose (real-time session logs from
exporter to connected client) from the Telemetry log push. However,
the `LogStreamResponse` message is enriched with optional additive
fields to support richer client-side display and optional dual-path
forwarding to telemetry:

```protobuf
message LogStreamResponse {
  string uuid = 1;
  string severity = 2;
  string message = 3;
  optional LogSource source = 4;
  // New additive fields:
  optional string driver_type = 5;     // Category when source=DRIVER
  optional string operation = 6;       // When the log is part of a known operation
  optional google.protobuf.Timestamp timestamp = 7;
  map<string, string> structured_fields = 8;
}
```

These fields are optional and backward compatible — older clients
ignore unknown fields; older exporters simply do not set them.

#### Tracing scope

This JEP covers *correlation only* — `lease_id`, `trace_id`,
and `span_id` are propagated as log fields and Prometheus exemplar keys so that
metrics, logs, and (future) traces can be joined. Full distributed tracing
(span creation, sampling policies, trace storage and visualization) is deferred
to a future JEP. Optional propagation of `traceparent` and lease
identifiers in gRPC metadata remains backward compatible (unknown
metadata ignored by older servers).

### Hardware Considerations

- No hardware considerations.

## Design Decisions

### DD-1: How lease-scoped *context* metadata is stored

**Scope:** This decision is about where to store generic metadata on a
`Lease` that describes *why* a run exists or *where* it came from — for example
an external build id, pipeline id, VCS revision, or other
operator-defined keys (team, environment), within the cardinality and
size limits defined in *Cardinality guidelines*. The same stored context
is the intended source to propagate (where safe) into metric series
labels and into log line fields for emissions that occur during the
lease and for logs produced during client access to the platform
(for example `jmp`) or during exporter and control-plane handling, so
Prometheus and Loki can correlate on one lease-level
identity without re-typing it on every line.

**Alternatives considered:**

1. **Annotation and label only** on the `Lease` object — Kube-native, no spec
   change; limited size for annotations; labels for select queries only.
2. **Typed subfields under `spec`** (for example `observability` or `context`)
   — easier validation, clearer API, migration path in CRD.
3. **Only client-side** (environment / local config) — no cluster visibility;
   hard for operators to audit; no stable object-level link to per-lease
   metrics and server logs in the cluster.

**Decision:** **(2)** — a typed `spec.context` map under the Lease CRD for
first-class, validated context. **(1)** (labels/annotations) remains allowed
for integration with generic tooling that only understands Kubernetes metadata
or benefits from lease label filtering.

**Rationale:** Typed fields make validation and documentation clear; labels
are still useful for selection and for tools that only understand metadata.

### DD-2: Where operational events (flash, image) live

**Alternatives considered:**

1. **Kubernetes `Event` objects** — built-in, TTL-limited, good for
   "what happened" in `kubectl get events` but not long-term history by default.
2. **`Lease.status.conditions` only** — compact but poor for a sequence of
   operations with payloads (image id, size).
3. **Dedicated CRD** (for example per-event or a single stream object) — more
   design and RBAC, better long-term retention and querying if backed properly.
4. **Annotated log events** Provides a lightweight alternative that can be traced
   and filtered along logs.

**Decision:** (4), since the other alternatives add additional pressure to the cluster
   etcd via CRDs, annotated logs still provide the same level of functionality and can
   be browsed together with logs.

**Rationale:** Annotated log events naturally flow through the Loki
  pipeline this JEP already establishes (**DD-5**, **DD-7**), so operational
  records (flash started, flash failed, image reference) are queryable,
  filterable, and correlated with surrounding exporter and controller logs
  using the same correlation fields (`lease_id`, `exporter`, `result`, …)
  without a second query domain. Kubernetes `Event` objects **(1)** have a short
  default TTL (~1 h) and still write to etcd on every occurrence;
  `status.conditions` **(2)** is a poor fit for a sequence of operations with
  variable payloads (image digest, byte count, duration); a dedicated CRD
  **(3)** adds schema versioning, RBAC surface, and per-event etcd writes
  that scale with flash volume — all pressure the cluster does not need
  for data whose primary consumers are dashboards and post-mortem
  queries, not reconciliation loops. Structured log events carry arbitrary
  fields without CRD migration, support configurable retention in Loki,
  and keep the etcd write budget reserved for scheduling and assignment
  where it matters most.

### DD-3: Metrics: Prometheus scrape of `/metrics` as the reference path

**Alternatives considered:**

1. **HTTP `GET /metrics` in Prometheus text format** (pull) — the default
   for in-cluster Prometheus in scrape mode; works
   with the Prometheus Operator (`ServiceMonitor`), `kube-prometheus`, and
   self-hosted jobs. The optional Jumpstarter Telemetry service exposes
   this for aggregated counters it holds after receiving +1 / +N
   from exporters.
2. **Prometheus remote write** (or a Mimir / Cortex receiver)
   from a Jumpstarter component — useful in advanced topologies; not
   part of the reference implementation in this JEP; operators can add a
   federation or `remote_write` from Prometheus to long-term
   storage without the application pushing to Prometheus.
3. **Both** — **(1)** is required for the documented path; **(2)** is
   optional infrastructure behind Prometheus, not a second
   required app protocol.
4. **Reverse scrape via gRPC** — exporters maintain a local
   `prometheus_client.CollectorRegistry` and connect to the Telemetry
   service via a persistent bidirectional gRPC stream (`MetricsStream`).
   When Prometheus scrapes the Telemetry service's `/metrics` endpoint,
   Telemetry fans out scrape requests to all connected exporters, merges
   the `generate_latest()` responses, and serves the combined result.
   Controller and Router still expose `/metrics` directly for Prometheus
   scrape (no change). This avoids push-increment complexity on the wire
   and keeps full counter state on the exporter at all times.

**Decision:** **(4)** — exporter-originated metrics are reverse-scraped
  through the Telemetry service via `MetricsStream`.

**Rationale:** Exporters are often behind NAT or firewalls and cannot
  be directly scraped by Prometheus. The reverse-scrape model **(4)**
  solves this: the exporter initiates an outbound gRPC stream
  (NAT-friendly, same direction as the existing controller connection),
  the Telemetry service requests metric snapshots on demand, and full
  counter state remains on the exporter at all times — eliminating
  lost-increment concerns (see **DD-9**). The exporter uses standard
  `prometheus_client` primitives locally, so driver authors instrument
  with familiar counters and histograms. The OpenMetrics exposition
  format natively carries exemplars, enabling high-cardinality context
  (`client`, `lease_id`, and `trace_id` when present) on individual
  samples without additional infrastructure. See **DD-6** (no OTel),
  **DD-7** (Telemetry Deployment), **DD-8** (HA replicas).

**Exemplar trade-offs and details:**

- **Wire format.** On the OpenMetrics `/metrics` endpoint an exemplar is
  appended after the sample value:

  ```text
  jumpstarter_operations_total{exporter="lab-01",operation="flash",result="success"} 42 # {client="ci-bot",lease_id="abc123",build_id="nightly-42"} 1.0 1625000000.000
  ```

  The `# {key=value,...} value timestamp` suffix is the exemplar. Grafana
  (≥ 7.4) renders these as clickable dots on metric panels; clicking a dot
  reveals the attached keys and can link to a Loki log query (filtered by
  `lease_id`) or a trace view (filtered by `trace_id`).

- **Size limit.** The [OpenMetrics 1.0 spec](https://prometheus.io/docs/specs/om/open_metrics_spec)
  imposes a **128 UTF-8 character** limit on the combined length of
  exemplar label names and values per exemplar.
  [OpenMetrics 2.0](https://github.com/prometheus/docs/blob/main/docs/specs/om/open_metrics_spec_2_0.md)
  (experimental, 2026) relaxes this to a soft cap measured in bytes.
  The exemplar key budget is discussed further in *Exemplars for
  high-cardinality context*.

- **Sampling.** Client libraries rate-limit exemplar updates internally;
  the last-seen exemplar per series is served on each scrape, not one
  per data point. For the Jumpstarter use case this is sufficient:
  the most recent `lease_id` / `trace_id` on a counter is the value
  operators need when investigating a spike.

- **Library support.** Go client support is mature
  (`prometheus/client_golang` ≥ 1.16). The Python `prometheus_client`
  library is used on the exporter side to maintain local registries
  and produce `generate_latest()` output for the reverse-scrape path
  (see *API / Protocol Changes*). Exemplar support in the Python
  library is functional but less complete than Go; if limitations
  arise, exemplar data can be sent as a sidecar field in
  `MetricsScrapeResponse` for the Telemetry service to merge
  server-side.

- **Infrastructure requirements.** Prometheus ≥ 2.26 with
  `--enable-feature=exemplar-storage` and
  `--storage.tsdb.max-exemplars` (e.g. 100 000). Grafana ≥ 7.4 for
  exemplar visualization. Perses does not yet support exemplar
  rendering; until it does, operators who want exemplar click-through
  can use Grafana alongside Perses or wait for upstream support.

  These limitations are acceptable for the correlation use case this JEP
  targets.

### DD-4: Log format for services vs CLI

**Alternatives considered:**

1. **JSON always** for every process — best for machines; hard for humans.
2. **Human text default for `jmp`**, **JSON for long-running services** and a
   CLI push via the Telemetry ingest endpoint in JSON format (in addition to the
   human-friendly output)
3. **Single format** with a pretty-printer in front of developers — more moving
   parts.

**Decision:** **(2)**. Long-running services (`jumpstarter-controller`,
  `jumpstarter-router`, `jumpstarter-telemetry`, Exporter) emit
  structured JSON to stdout. The Controller and Router do not
  push logs directly to Loki; instead, a cluster-level log shipper
  (Promtail, Grafana Alloy, Vector, or equivalent DaemonSet) scrapes their
  pod logs and delivers them to Loki. Only `jumpstarter-telemetry` writes
  to Loki directly (push API) because the exporter/client data it
  aggregates does not originate as any pod's stdout.

**Rationale:** Matches the requirement that *clients* stay human-readable, and at
  the same time all services get parseable, joinable log lines. Writing JSON
  to stdout and relying on the cluster log shipper for Loki delivery
  decouples the Controller reconciler and Router session handling from
  Loki availability — a Loki outage does not affect lease operations.
  The Telemetry service retains a direct Loki-push because it is an
  isolated workload (**DD-7**) whose core job is Loki ingest.

**Format:** JSONL (one JSON object per line), produced by setting
  `--zap-encoder=json` on the existing `controller-runtime` / Zap logger
  (no changes to log call sites — existing `logr` structured fields become
  JSON keys automatically). The `ts`, `level`, and `msg` fields follow
  Zap's default JSON encoder output; application code adds domain fields
  via the standard `logr` `WithValues` / `Info` / `Error` API.

  Base fields present in every log line:

| Field         | Format                                                              | Loki label | Description                               |
| ------------- | ------------------------------------------------------------------- | :--------: | ----------------------------------------- |
| `ts`          | ISO-8601 (`2026-04-28T10:15:30.123Z`)                               |     no     | Timestamp (Zap default).                  |
| `level`       | Lower-case string (`debug`, `info`, `warn`, `error`)                |     no     | Log severity (Zap default).               |
| `msg`         | Free-form string                                                    |     no     | Human-readable message (Zap default).     |
| `component`   | Fixed enum (`cli`, `controller`, `router`, `telemetry`, `exporter`) |   **yes**  | Emitting service.                         |
| `exporter`    | CRD name (when applicable)                                          |   **yes**  | Exporter CRD name; bounded by cluster size.|
| `lease_id`    | UID string (when applicable)                                        |     no     | Lease UID (high cardinality).             |
| `operation`   | String (when applicable)                                            |     no     | Operation name (flash, power, …).         |
| `result`      | String (when applicable)                                            |     no     | Outcome (success, failure, …).            |
| `driver_type` | Category from predefined set (when applicable)                      |     no     | Driver category (storage, power, …).      |
| `client`      | CRD name (when applicable)                                          |     no     | Client CRD name (high cardinality).       |
| *`spec.context` keys* | User-defined strings (during active lease)                  |     no     | All `lease.spec.context` entries (e.g. `build_id`, `image_digest`, VCS ref) added as JSON fields. High cardinality, never stream labels. |
| *`exporterLabels` keys* | Values from Exporter CRD labels (when configured)         |     no     | Operator-defined exporter labels (e.g. `board-type`); see `spec.telemetry.exporterLabels`. |

  `namespace` is **not** emitted by the application. Log shippers
  (Promtail, Grafana Alloy, Vector) automatically inject `namespace`
  (and `pod`, `container`) from Kubernetes pod metadata via service
  discovery, so it is available as a Loki stream label without
  application-level awareness.

  Fields marked as **Loki stream labels** are extracted by the log shipper
  and used as indexed stream selectors. They must be low-cardinality to
  keep the active stream count manageable (Grafana recommends < 100 k
  active streams per tenant). With the labels above, a deployment with
  200 exporters across 5 namespaces produces roughly 1 000 streams —
  well within budget. High-cardinality fields like `client` or
  `lease_id` must stay in the JSON body: promoting `client` to a
  stream label in a 1 000-client, 200-exporter cluster would create
  up to 1 000 000 streams, overwhelming the Loki ingester. These fields
  are instead queried with `| json | client="value"` filter
  expressions after selecting the relevant streams.

  Multi-line content (e.g. stack traces) is embedded as an escaped string
  within the JSON value (typically in a `stacktrace` or `error` field),
  never as bare multi-line text, so each physical line is always one
  complete JSON object.

### DD-5: Where Loki and Prometheus (or remote-write) credentials live

**Alternatives considered:**

1. **Each exporter and edge host** holds credentials (or a sidecar) to push
   directly to Loki and to Prometheus (or a metrics gateway) — maximum
   flexibility; maximum secret distribution and rotation burden on lab and
   remote sites.
2. **Jumpstarter Controller and/or Router** receive metrics and structured
   events from exporters and (optionally) from client traffic they already
   handle, and forward to the Loki push API and to
   Prometheus-compatible sinks (scrape registration)
   with in-cluster auth — one
   credential surface; enriched with lease, exporter, and client context
   in one place; must be non-blocking, bounded, and optional so the
   control path does not depend on Loki or Prometheus availability.
3. **Hybrid** — generic in-cluster collectors for raw pod logs and scrape;
   (2) for lease-scoped events and aggregated exporter metrics the
   platform understands.
4. **Dedicated Jumpstarter Telemetry Deployment** (see **DD-7**)
   instead of folding everything into the Controller — only
   Telemetry holds Loki-push credentials; isolated failure domain
   and scaling for high-volume increments. Router and Controller
   write structured JSON to stdout (see **DD-4**) and expose `/metrics`
   for Prometheus scrape; a cluster log shipper delivers their pod logs
   to Loki without Jumpstarter-specific Loki credentials.

**Decision:** (4)

**Rationale:** The goal is to avoid propagating Loki- and
  cluster-ingest authentication
  to every exporter process while still attaching Jumpstarter-specific
  context. Among Jumpstarter components, only `jumpstarter-telemetry`
  holds Loki-push credentials — the Controller and Router have no Loki
  client dependency (see **DD-4**); their pod logs reach Loki via the
  cluster's existing log shipping infrastructure. Generic in-cluster
  collectors solve *credentials* but not *semantic* correlation unless
  integrated; the hub (2) reuses the existing trust model
  (exporter→controller) and can inject labels and tenant context in one place. A separate Deployment (**4** /
  **DD-7**) is preferable to overloading the main reconciler when
  load or residency of counters matters.

### DD-6: OpenTelemetry (OTLP / Collector) as a *mandated* layer

**Alternatives considered:**

1. **Adopt OpenTelemetry** — instrument Controller, Router, Exporter, and
   clients with the OTel SDK, export OTLP to a cluster-local
   OpenTelemetry Collector, and let the Collector fan out to Loki, Prometheus
   (remote write), and Tempo.
2. **Integrate directly** with each backend: Loki HTTP `POST /loki/api/v1/push` or
   gRPC; Prometheus text on `/metrics`; structured JSON
   (or logfmt) logs to stdout for shippers; optional W3C `traceparent` in
   gRPC metadata for correlation *without* shipping full distributed
   traces in the first iteration. If traces are ever needed, use Tempo
   ingest where practical, *or* a thin sender — still
   without a project-wide requirement on the OTel SDK in every binary.
3. **Hybrid (OTel in one language, direct in another)** — lowest common
   implementation cost but inconsistent contributor experience and two
   operational models.

**Decision:** **(2).** This JEP does not make OpenTelemetry (SDK or
  Collector) part of the required reference architecture. Vendors and
  operators who already run an OpenTelemetry Collector may scrape the
  same `/metrics`, receive logs shipped by existing agents, or
  receive the Loki body the hub would have sent — compatibility
  is welcome; dependency is not mandatory.

**Rationale:**

- **Complexity** — the Collector is another versioned, configured service; dual
  OTel stacks (Go, Python) add version drift and test matrix.
- **Fit** — most Jumpstarter metrics and lease events map cleanly to
  Prometheus and Loki wire protocols operators already use.
- **Narrow scope** — full three-pillar OTel (unified logs via OTLP) is
  *optional product territory*; this JEP optimizes for low ceremony and
  direct integration.

The proposed Jumpstarter Telemetry service (**DD-7**) is itself a
non-trivial component (metric aggregation, Loki forwarding, multi-replica
HA). The distinction is that it is *purpose-built* for Jumpstarter's
narrow scope: a single Go binary with a single config surface, no
separate version matrix, and no generic pipeline DSL to learn. An OTel
Collector serves many use cases but requires operator familiarity with
its configuration model, receivers, processors, and exporters — overhead
that is not justified when the data paths are known in advance.
Additionally, the Telemetry service operates inside Jumpstarter's
existing authentication and trust domain (mTLS, registered client and
exporter identities). It can validate that an incoming increment
actually originates from the claimed exporter or client — preventing
impersonation or label injection — without requiring a separate
auth layer. A generic OTel Collector has no awareness of Jumpstarter
identities and would need external policy to achieve the same guarantee.

**Future extension:** the Telemetry service's ingest endpoint could
accept OTLP in a future iteration, enabling operators who run OTel
Collectors on exporter hosts (e.g. for host-level stats) to route data
through the same trust boundary without a second credential set. This
is additive and does not require adopting OTel as a project dependency.

### DD-7: Optional Jumpstarter Telemetry service (dedicated Deployment vs. Controller/Router only)

**Alternatives considered:**

1. **In-process** in the Controller (and Router) reconciler — few
  moving parts; risk of CPU / GC pressure and stronger coupling
  between leases and high-volume increments or Loki writes.
2. A **dedicated** in-cluster Service and Deployment (working name
   `jumpstarter-telemetry`, TBD) that: receives gRPC/HTTP increments from
   exporters and clients, applies them to counters in memory,
   POSTs to Loki, exposes `/metrics`, and uses the same K8s
   ServiceAccount / mTLS as other control-plane binaries.
3. **Split** into separate sidecars (Loki-only, metrics-only) — more images to
   build and version.
4. **Dedicated Deployment with reverse-scrape for metrics and push for
   logs** — same dedicated `jumpstarter-telemetry` Deployment as **(2)**,
   but instead of receiving increment RPCs the service reverse-scrapes
   connected exporters via `MetricsStream` (see *API / Protocol
   Changes*). Exporters maintain local `prometheus_client` registries;
   the Telemetry service requests `generate_latest()` snapshots on
   demand when its `/metrics` endpoint is hit, merges the results, and
   serves them to Prometheus. Logs and events are still pushed by
   exporters and clients via `PushLogs`. Client-side metrics are not
   collected — all metrically-interesting operations are observable
   from the exporter side.

**Decision:** Prefer **(4)** for the optional aggregated-metrics + Loki
  path at scale; allow **(1)** in small or dev clusters; **(3)** only
  if review shows a need. Could still offer a centralized log/event source when
  Loki is not available by using the pod logs, this could be helpful for testing.

**Rationale:** A dedicated workload can scale and restart independently;
  Loki spikes and ingest load cannot starve lease reconciliation in the
  controller. The reverse-scrape model **(4)** is preferred over the
  increment-push model **(2)** because full counter state stays on the
  exporter — no metrics are lost when the Telemetry service restarts or
  is temporarily unavailable, and idempotency concerns are eliminated
  (see **DD-9**).

**Identity enforcement:** The Telemetry service validates the source
  identity of every `MetricsStream` connection and `PushLogs` RPC from
  the mTLS certificate or ServiceAccount token. The `exporter` and
  `client` labels on incoming data are enforced server-side to match the
  authenticated identity — a compromised or misconfigured exporter
  cannot submit metrics under another exporter's name or inject
  arbitrary labels.

**Failure modes:**

| Scenario                        | Behavior                                                                                                                                                                                       |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Telemetry service unavailable   | Exporters keep counting locally; no metrics are lost. When the exporter reconnects, the next scrape returns the full current counter state. Log push RPCs are fire-and-forget with bounded retry; log entries may be lost but device operations are unaffected. |
| Telemetry pod restart           | Metric state is rebuilt on the next scrape from each connected exporter — no permanent data loss. Prometheus `rate()` and `increase()` handle the apparent counter reset transparently. |
| Loki unreachable                | The Telemetry service buffers log entries in a bounded queue (see *Backpressure* in the control-plane section). On overflow, entries are dropped and `jumpstarter_telemetry_dropped_total` incremented. |
| Prometheus scrape fails         | No data loss — the next successful scrape triggers a fresh fan-out to connected exporters and returns current values. |

  The Telemetry service exposes `/healthz` (liveness) and `/readyz`
  (readiness, gated on Loki reachability and at least one connected
  exporter) endpoints for Kubernetes probes.

**Scrape fan-out:** When Prometheus hits `/metrics`, the Telemetry
  service fans out `MetricsScrapeRequest` to **all connected exporters in
  parallel** and waits up to `spec.telemetry.metrics.scrapeTimeout`
  (default: 7 s) for responses. **Only metrics received during the
  current fan-out are included in the response.** Exporters that do not
  respond in time are omitted entirely — no cached or stale data is
  ever served. This eliminates any risk of double-counting from stale
  connections where the exporter may have already migrated to another
  replica (see **DD-8**).

**Memory budget:** During a scrape fan-out the Telemetry service
  temporarily holds metric snapshots from responding exporters until the
  merged response is written to Prometheus. With 200 exporters each
  producing ~50 series (bounded by `{operation, result, driver_type}`
  label combinations), the peak is ~10 000 series at ~200–300 bytes
  each, costing ~2–3 MB. Snapshots are discarded as soon as the
  `/metrics` response is flushed — no metric data is retained between
  scrapes.

### DD-8: Multiple Telemetry replicas (HA) and exporter-sticky connections

**Context:** With the reverse-scrape model (see **DD-3** alternative 4
and *API / Protocol Changes*), the Telemetry service does not hold
authoritative counter state — exporters maintain their own local
`prometheus_client` registries. The Telemetry service only caches the
latest metric snapshot per exporter. Each exporter opens a single
long-lived `MetricsStream` to one Telemetry replica.

**Alternatives considered:**

1. **Single replica** for Telemetry — no cross-pod `sum` issue; SPOF for
  ingest and scrape of that `Service`.
2. **Multiple replicas** behind a load balancer; each RPC updates one
  pod, which only advances its partial counters for the label
  sets it has seen. Prometheus scrapes all pods (or separate
  `PodMonitor` targets). In PromQL,
  `sum by (exporter, operation, result, driver_type) (…)` after dropping
  `pod` / `instance` matches the global total, as long as each real
  event is applied at most once in the system (counters are
  additive; increments are partitioned by traffic).
3. **Strong consistency** (Raft, Redis as source of truth for
  counters) — higher operating cost than this JEP’s v1 scope.
4. **Multiple replicas with exporter-sticky connections** — each exporter
   opens a single `MetricsStream` to one replica (sticky by stream).
   Each replica only caches metric snapshots for its connected
   exporters. Prometheus scrapes all replicas (via `PodMonitor`);
   `sum by (exporter, operation, result, driver_type) (…)` after
   dropping `pod` / `instance` yields the exact global total with no
   double-counting, because each exporter’s metrics appear on exactly
   one replica’s `/metrics` output. On replica failure the exporter
   reconnects to a survivor and the next scrape returns its full
   current counter state — no data is lost.

**Decision:** **(4)**

**Rationale:** Exporter-sticky connections naturally partition metric
  snapshots across replicas with no overlap, so `sum` across replicas
  is exact and double-counting is impossible. Full counter state lives
  on the exporter, not on the Telemetry service, so replica restarts
  or failovers cause no data loss. Loki log pushes (`PushLogs`) are
  naturally per-replica as well and do not require deduplication.
  Alternative (3) adds operational complexity with no benefit given
  the reverse-scrape model.

### DD-9: Idempotency vs. best-effort

**Context:** With the reverse-scrape model, metrics idempotency is a
non-issue — each scrape returns the full current counter state from the
exporter, so there are no increments to deduplicate or double-count.
The only remaining idempotency concern is for `PushLogs` RPCs, where
a retry could result in duplicate log entries in Loki.

**Alternatives considered:**

1. **Idempotent** log pushes (deduplication keys per `LogEntry`) —
  appropriate for billing- or SLO-sensitive log pipelines; requires
  a dedup store or Loki-side dedup.
2. **Best effort** (at-least-once) for `PushLogs` without global
  deduplication — simpler; rare duplicate log entries on retries.
3. **Metrics idempotency** (dedup keys on metric increments) — no
  longer applicable; the reverse-scrape model returns full state,
  making increment deduplication moot.

**Decision:** (2) for `PushLogs`; metrics idempotency is not needed.

**Rationale:** Duplicate log entries from occasional retries are
  acceptable for informative/diagnostic logs. Loki queries are
  tolerant of rare duplicates. No global dedup store is needed in v1;
  operators treat these logs as diagnostic signals, not audit trails.

### DD-10: Perses over Grafana for dashboarding

**Alternatives considered:**

1. **Grafana** — mature, widely deployed, massive plugin and datasource
   ecosystem; governed by Grafana Labs (commercial); AGPL v3 license;
   custom JSON dashboard format; external to Kubernetes architecture.
2. **Perses** — CNCF project (vendor-neutral governance); Apache 2.0
   license; standardized dashboard spec (CUE/JSON) with built-in static
   validation and SDKs for GitOps; Kubernetes-native (CRD support for
   dashboards-as-code); data-source focus on Prometheus, Loki, and
   Tempo — exactly the backends this JEP targets.

**Decision:** **(2)**

**Rationale:**

- **License alignment** — Jumpstarter is Apache 2.0; recommending an
  AGPL-licensed dashboard layer introduces license friction for downstream
  distributors and embedders.
- **CNCF governance** — vendor-neutral stewardship matches the project's
  open-source posture; no single-vendor control over the dashboard layer.
- **Kubernetes-native CRDs** — dashboards can be managed as K8s resources,
  fitting the same declarative, reconciler-driven model Jumpstarter already
  uses for Leases, Exporters, and the optional Telemetry Deployment.
- **GitOps and validation** — CUE-based specs with static validation and SDKs
  enable dashboard-as-code in CI pipelines, consistent with the JEP's emphasis
  on automation and CI integration.
- **Backend focus** — Perses targets Prometheus, Loki, and Tempo — exactly the
  three backends this JEP standardizes on — without carrying the cost of a
  broad plugin ecosystem the project does not need.

**Perses vs Grafana — practical comparison:**

| Aspect               | Perses                                  | Grafana                                    |
| -------------------- | --------------------------------------- | ------------------------------------------ |
| License              | Apache 2.0                              | AGPL v3                                    |
| Governance           | CNCF (vendor-neutral)                   | Grafana Labs (commercial)                  |
| Dashboard-as-code    | CUE/JSON spec, static validation, SDKs  | JSON export, no built-in validation        |
| K8s-native CRDs      | Yes                                     | Via third-party operator (grafana-operator)|
| Exemplar rendering   | Not yet (upstream roadmap)              | Yes (>= 7.4)                               |
| Data-source scope    | Prometheus, Loki, Tempo                 | Broad plugin ecosystem                     |
| Maturity / ecosystem | Early (CNCF sandbox/incubating)         | Mature, widely deployed                    |

The main Perses gap today is exemplar visualization. Operators who need
exemplar overlays on dashboards should use Grafana alongside Perses or
wait for upstream support. Grafana remains fully compatible — all
`/metrics` and Loki endpoints are standard — so the choice is
non-exclusive.

Operators who prefer Grafana can still point it at the same `/metrics` and Loki
endpoints; this DD only governs the *recommended* dashboard experience.

## Design Details

### Correlation and fields

*Subject to review — names and cardinality rules should be fixed before
"Implemented".*

| Field / label                    | Prom label | Prom exemplar | Loki stream | Log line | Notes                                               |
| -------------------------------- | :--------: | :-----------: | :---------: | :------: | --------------------------------------------------- |
| `exporter`                       | yes        | —             | yes         | yes      | CRD name; bounded by cluster size.                  |
| `operation`                      | yes        | —             | no          | yes      | Small fixed enum (flash, power, …).                 |
| `result`                         | yes        | —             | no          | yes      | Small fixed enum (success, failure, …).             |
| `driver_type`                    | yes        | —             | no          | yes      | Category from a predefined set in core (storage, power, …). |
| `error_type`                     | yes        | —             | no          | yes      | Failure class (timeout, device_error, …); on errors. |
| `direction`                      | yes        | —             | no          | yes      | tx / rx; for byte-counter and stream metrics only.  |
| `component`                      | no         | —             | yes         | yes      | Fixed set (cli, controller, router, telemetry, exporter).|
| `namespace`                      | no         | —             | yes         | yes      | K8s namespace; bounded.                             |
| `lease_id`                       | **no**     | yes           | **no**      | yes      | Unbounded; exemplar for drill-down.                 |
| `client`                         | **no**     | yes           | **no**      | yes      | CRD name; exemplar for client identity.             |
| `image_digest`, `build_id`, etc. | **no**     | yes           | **no**      | yes      | From `spec.context`; included when listed in `exemplarKeys`. |
| `trace_id` / `span_id`           | **no**     | yes           | **no**      | yes      | W3C; links metrics to traces via exemplars.         |
| *`exporterLabels` keys*          | **no**     | yes           | **no**      | yes      | From Exporter CRD labels; included when listed in `exemplarKeys`. |

Additional `lease.spec.context` correlation fields can be added at runtime;
they appear as structured log line fields and, when listed in the operator's
`exemplarKeys` allowlist, as Prometheus exemplar keys (see *Exemplars for
high-cardinality context* below and *Operator configuration*).

### Cardinality guidelines

Unbounded identifiers (`lease_id`, `client`, `image_digest`, `trace_id`, and
any operator-defined `spec.context` keys) must not be used as Prometheus metric
labels or Loki stream labels. They belong inside structured log line JSON
and Prometheus exemplars (see below), where Loki filter expressions
(`| json | lease_id = "…"`) and dashboard exemplar overlays can surface them
without inflating the label index or TSDB series count.

Rules of thumb for this JEP:

- **Prometheus labels**: each metric label dimension should have < 100 distinct
  values per scrape target. The label set for Jumpstarter metrics is
  `{exporter, operation, result, driver_type}` — all bounded enums.
  `error_type` is added on failure-path metrics and `direction` on
  byte-counter metrics. High-cardinality context is carried via exemplars,
  not labels.
- **Loki**: stream labels should be a small fixed set (`{component, exporter,
  namespace}`) to keep active stream count per tenant manageable (Grafana's
  guidance: < 100 k active streams). High-cardinality fields go inside the log
  line body.
- **Lease context fields** from `spec.context` are propagated into log line
  JSON and, when listed in `exemplarKeys`, into Prometheus exemplars. They
  never become Prometheus labels or Loki stream labels.

#### Exemplars for high-cardinality context

Prometheus exemplars attach arbitrary key-value pairs to individual counter
increments and histogram observations without creating new time series. This
is the primary mechanism this JEP uses to surface per-request context
(`client`, `lease_id`, and `trace_id` when present) on metrics while keeping series cardinality
flat.

Default exemplar keys emitted on every counter/histogram observation:

| Key        | Source                | Purpose                                         |
| ---------- | --------------------- | ----------------------------------------------- |
| `client`   | Client CRD name       | "Which client caused this spike?"               |
| `lease_id` | Lease UID             | Correlate a metric sample with lease logs.      |
| `trace_id` | W3C `traceparent`     | Included **only when present** in gRPC metadata.|

`trace_id` is not synthesized by Jumpstarter — it is included only when
an external caller (CI pipeline, user code) propagates a `traceparent`.
Full distributed tracing (spans, storage, visualization) is deferred to
a future JEP; when it lands, `trace_id` becomes a default key. Until
then, omitting it saves ~45 characters of exemplar budget.

`spec.context` keys (e.g. `build_id`, `image_digest`) are included as
exemplar keys when listed in the operator's `exemplarKeys` allowlist (see
*Operator configuration*). Because exemplars are per-observation metadata —
not label dimensions — they have zero impact on series cardinality regardless
of how many distinct values appear.

**Exemplar size budget:** The OpenMetrics 1.0 limit is 128 UTF-8
characters for the combined key-value pairs in a single exemplar.
The two default keys (`client`, `lease_id`) consume roughly 30–50
characters, leaving ~80–100 characters for `spec.context` entries
(or more when `trace_id` is absent). To stay within budget:

1. Default keys (`client`, `lease_id`) are always included first.
   `trace_id` is added when present in the request context.
2. `spec.context` keys are added in alphabetical order until the 128-char
   limit is reached; remaining keys are silently dropped from the
   exemplar (they remain available in structured log lines).
3. The `Lease` CRD validates `spec.context` at admission time: key names
   are limited to 32 characters, values to 64 characters, and the total
   number of entries to 8. This prevents accidental budget exhaustion and
   ensures exemplar truncation is rare in practice.

**Dashboard visualization**: when exemplars are enabled on a Prometheus data
source, metric panels render clickable dots on each sample that carries
exemplar data. Clicking a dot reveals the attached keys and can link to
Loki log queries (filtered by `lease_id`) or a Tempo trace view (filtered
by `trace_id`).

Per-client analysis remains available via LogQL for operators who do not
use exemplars:
`sum by (client) (count_over_time({component="exporter"} | json | operation="flash" [5m]))`.

### Proposed metrics

*Names are illustrative; final naming should follow
[Prometheus naming conventions](https://prometheus.io/docs/practices/naming/)
and be fixed before "Implemented".*

| Metric name                                  | Type      | Labels                                       | Description                               |
| -------------------------------------------- | --------- | -------------------------------------------- | ----------------------------------------- |
| `jumpstarter_operations_total`               | counter   | `exporter`, `operation`, `result`, `driver_type`  | Total operations performed.               |
| `jumpstarter_operation_duration_seconds`      | histogram | `exporter`, `operation`, `result`, `driver_type`  | Duration of each operation.               |
| `jumpstarter_operation_errors_total`          | counter   | `exporter`, `operation`, `driver_type`, `error_type` | Errors by class (timeout, device, …).  |
| `jumpstarter_stream_bytes_total`             | counter   | `exporter`, `driver_type`, `direction`            | Bytes transferred (tx/rx) on streams.     |
| `jumpstarter_active_sessions`                | gauge     | `exporter`                                   | Currently active lease sessions.          |
| `jumpstarter_lease_acquisitions_total`        | counter   | `result`                                     | Lease acquire attempts (controller).      |
| `jumpstarter_scrape_timeouts_total`          | counter   | `exporter`                                   | Scrape fan-out timeouts per exporter (Telemetry-side). |

All counters and histograms carry exemplar keys from the operator's
`exemplarKeys` allowlist (by default `client` and `lease_id`; `trace_id`
when present; `spec.context` and `exporterLabels` entries when listed)
on every observation.

### Metric usage and alerting

| Metric                                       | Primary use | Alert? | Starter threshold                              |
| -------------------------------------------- | ----------- | :----: | ---------------------------------------------- |
| `jumpstarter_operations_total`               | Dashboard   |  yes   | Failure rate > 20 % over 15 min per exporter.  |
| `jumpstarter_operation_duration_seconds`      | Dashboard   |  yes   | p95 > 60 s per operation type.                 |
| `jumpstarter_operation_errors_total`          | Dashboard   |  yes   | Error rate rising; group by `error_type`.       |
| `jumpstarter_stream_bytes_total`             | Dashboard   |   no   | —                                              |
| `jumpstarter_active_sessions`                | Dashboard   |  yes   | 0 sessions for > 30 min (possible exporter issue). |
| `jumpstarter_lease_acquisitions_total`        | Dashboard   |  yes   | Failure rate > 10 % over 15 min.               |
| `jumpstarter_telemetry_dropped_total`        | Alerting    |  yes   | Any increment (telemetry pipeline saturated).   |
| `jumpstarter_scrape_timeouts_total`          | Alerting    |  yes   | Repeated timeouts for same exporter (connectivity or load issue). |

Thresholds are suggestions; operators should tune them to their
environment. The operator should ship a set of example `PrometheusRule`
CRDs based on the table above that operators can enable and customize.
These rules are opt-in and disabled by default to avoid noise in
environments with different baselines.

**High-frequency byte counters:** `jumpstarter_stream_bytes_total` can
be incremented at very high rates on serial and video streams. Exporters
must pre-aggregate byte counts locally and flush a single `+N` increment
to the Telemetry service at a configurable interval (default: every 5 s
or every 64 KiB, whichever comes first) rather than sending a per-read
or per-write RPC. This bounds telemetry RPC volume independently of
stream throughput.

### Example queries

#### PromQL (Prometheus)

**Flash failure rate per exporter:**

```promql
sum by (exporter) (rate(jumpstarter_operations_total{operation="flash", result="failure"}[5m]))
/
sum by (exporter) (rate(jumpstarter_operations_total{operation="flash"}[5m]))
```

**p95 flash duration per driver type:**

```promql
histogram_quantile(0.95,
  sum by (driver_type, le) (rate(jumpstarter_operation_duration_seconds_bucket{operation="flash"}[5m]))
)
```

**Top 5 busiest exporters (all operations, 1 h window):**

```promql
topk(5, sum by (exporter) (rate(jumpstarter_operations_total[1h])))
```

**Alert: exporter flash failure rate > 20% over 15 min:**

```promql
(
  sum by (exporter) (rate(jumpstarter_operations_total{operation="flash", result="failure"}[15m]))
  /
  sum by (exporter) (rate(jumpstarter_operations_total{operation="flash"}[15m]))
) > 0.2
```

**Error breakdown by class for a specific driver:**

```promql
sum by (error_type) (rate(jumpstarter_operation_errors_total{driver_type="storage"}[1h]))
```

**Bytes per second by exporter and direction:**

```promql
sum by (exporter, direction) (rate(jumpstarter_stream_bytes_total[5m]))
```

**Exporters with repeated scrape timeouts (last 30 min):**

```promql
topk(10, sum by (exporter) (increase(jumpstarter_scrape_timeouts_total[30m])))
```

**HA Telemetry: aggregate across replicas (drop pod/instance):**

```promql
sum by (exporter, operation, result, driver_type) (rate(jumpstarter_operations_total[5m]))
```

#### LogQL (Loki)

**All flash events for a specific lease:**

```text
{component="exporter"} | json | operation="flash" | lease_id="<uid>"
```

**Flash failures per client over 5 min (log-based, no exemplars needed):**

```text
sum by (client) (
  count_over_time({component="exporter"} | json | operation="flash" | result="failure" [5m])
)
```

**Controller logs for a specific lease (post-mortem):**

```text
{component="controller"} | json | lease_id="<uid>"
```

**Error events across all exporters in a namespace:**

```text
{component="exporter", namespace="production"} | json | result="failure"
```

**Telemetry service health (its own operational logs):**

```text
{component="telemetry"} | json | level="error"
```

### Control-plane aggregation (Controller / Router / optional Telemetry)

When this mode is enabled in a deployment:

- Exporters maintain local `prometheus_client` registries and open a
  `MetricsStream` to the optional `jumpstarter-telemetry` service
  (**DD-7**). On each Prometheus scrape the Telemetry service fans out
  `MetricsScrapeRequest` to all connected exporters in parallel, merges
  the responses, and serves the combined output on `/metrics`
  (**DD-3**). HA (multiple replicas with exporter-sticky connections)
  uses `sum` in PromQL (**DD-8**). Exporter and edge processes never
  need Loki or cluster-scrape credentials directly (**DD-5**).
- Exporters and clients (`jmp`) push structured log entries to the
  Telemetry service via `PushLogs`. The Telemetry service forwards
  these to Loki. Best-effort duplicate tolerance applies (**DD-9**).
- Controller and Router emit structured JSON logs to stdout
  (see **DD-4**). They do not push logs directly to Loki; a cluster-level
  log shipper (Promtail, Grafana Alloy, Vector, or equivalent) scrapes
  their pod logs and delivers them to Loki. This decouples the reconciler
  and session-handling hot paths from Loki availability.
- **Backpressure:** The Telemetry service uses a bounded ring buffer
  for the Loki log push path with a configurable depth
  (default: 10 000 entries, see `spec.telemetry.backpressure.queueDepth`).
  On overflow, dropped entries are replaced by a single **drop marker**
  — a synthetic log entry recording the count of dropped entries and the
  time window. Subsequent drops while the buffer is still full
  accumulate into the same marker rather than adding new entries, so the
  queue always retains one slot for the current drop summary. When the
  buffer drains and the marker is flushed, the downstream log contains
  an explicit record such as
  `{"level":"warn","msg":"entries dropped","count":142,"window_seconds":12}`.
  A `jumpstarter_telemetry_dropped_total` counter (partitioned by
  `destination={loki}`) is also incremented on `/metrics` for alerting.
  Metrics do not need backpressure — the reverse-scrape model is
  pull-based and transient (no buffering between scrapes).
  Because the Controller and Router do not push to Loki, their
  lease/session operations are inherently isolated from Loki slowdowns.
- **Multi-tenancy:** write-side tenant scoping (e.g. namespace-based
  separation in Loki and Prometheus) is a deployment concern handled by
  the log shipper and Prometheus configuration. Read-side access control
  (who can query which metrics or logs) is likewise a deployment concern
  and out of scope for this JEP.
- Metric facts originate on the exporter (local `prometheus_client`
  counters/histograms); the Telemetry service is a transparent
  scrape-aggregation proxy. Controller and Router expose their own
  `/metrics` for Prometheus scrape and rely on the log shipper for
  their stdout logs.

### High-level data flow

#### Client (`jmp`)

```{mermaid}
flowchart LR
  jmp([jmp CLI]) -->|session gRPC| exp[Exporter]
  jmp -->|structured logs| tel[jumpstarter-telemetry]
```

The CLI connects to the Exporter for device sessions and sends structured
logs to the Telemetry service for Loki ingest (see **DD-4**).

#### Exporter

```{mermaid}
flowchart LR
  ctrl[jumpstarter-controller] -->|lease lifecycle| exp[Exporter]
  drv[Drivers] --> exp
  exp <-->|MetricsStream| tel[jumpstarter-telemetry]
  exp -->|PushLogs| tel
```

The Controller assigns leases; the Exporter delegates to Drivers and
maintains local `prometheus_client` counters. It opens a `MetricsStream`
to Telemetry for reverse-scrape and pushes structured logs via `PushLogs`
(see **DD-2**, **DD-3**, **DD-5**, **DD-7**).

#### Telemetry to backends

```{mermaid}
flowchart LR
  prom[(Prometheus)] -->|scrape /metrics| tel[jumpstarter-telemetry]
  tel <-->|MetricsStream fan-out| exp[Exporters]
  tel -->|push API| loki[(Loki)]
  tel -->|JSON stdout| shipper[Log shipper]
  shipper -->|pod logs| loki
```

On each Prometheus scrape, Telemetry fans out `MetricsScrapeRequest` to
all connected exporters in parallel, merges responses, and serves the
combined output. Logs received via `PushLogs` are forwarded to Loki
(**DD-3**, **DD-7**, **DD-8**).

#### Controller to backends

```{mermaid}
flowchart LR
  ctrl[jumpstarter-controller] -->|JSON stdout| shipper[Log shipper]
  shipper -->|pod logs| loki[(Loki)]
  ctrl -->|/metrics| prom[(Prometheus)]
```

The Controller writes structured JSON to stdout (see **DD-4**). A
cluster log shipper scrapes pod logs and delivers them to Loki. The
Controller exposes `/metrics` for reconciliation and lease-level counters.

#### Router to backends

```{mermaid}
flowchart LR
  router[jumpstarter-router] -->|JSON stdout| shipper[Log shipper]
  shipper -->|pod logs| loki[(Loki)]
  router -->|/metrics| prom[(Prometheus)]
```

The Router writes structured JSON to stdout (see **DD-4**). A
cluster log shipper scrapes pod logs and delivers them to Loki. The
Router exposes `/metrics` for routing and session-level counters.

The diagrams above summarize the reverse-scrape hub model described in
*Control-plane aggregation*. For credential isolation see **DD-5**; for
the Telemetry Deployment see **DD-7**; for HA with exporter-sticky
connections see **DD-8**; for best-effort log semantics see **DD-9**.
No OpenTelemetry Collector is *required* (see **DD-6**); operators may
run one *alongside* and scrape the same targets if they choose.

### Common open-source backends (direct integration; no mandatory OTel)

This JEP’s target wire protocols and components are Prometheus and
Loki (and, if trace export is ever added, Tempo or Jaeger with
native ingest or HTTP — not OTLP as a *Jumpstarter* requirement; see
**DD-6**). OpenTelemetry is a parallel ecosystem: teams can run a
Collector next to Jumpstarter and still scrape `/metrics` and ship
logs with Promtail-class agents; the reference design does not depend
on the OTel SDK in application code.

- Prometheus for metrics (and Alertmanager for routing alerts): scrape
  the `/metrics` endpoint, remote-write to long-term store if needed, and drive
  dashboards in Perses or self-hosted UIs (see **DD-10**). `kube-state-metrics` and
  the Prometheus Operator are common in Kubernetes; vendors often package
  the same projects, but this JEP refers to the open-source components by name.
- Loki (Grafana Labs, AGPL) for log storage and querying; it pairs with
  Perses (see **DD-10**) for search and with Promtail, Grafana
  Agent, or Grafana Alloy to ship logs, or with application push to Loki’s HTTP API as
  already discussed in the control-plane path.
- Traces (optional, future work) — if adopted, Grafana Tempo and Jaeger
  are typical stores; use W3C Trace Context in RPC metadata for
  correlation even when full trace export is off. OTLP may be
  *only* a convenience for operators; it is not a JEP-0011 core
  dependency.
- A typical Kubernetes integration path: `ServiceMonitor` + Prometheus
  (or a compatible remote-write consumer), a Loki endpoint for logs
  — any EKS, GKE, AKS, self-managed
  Kubernetes, or bare-metal install that runs these same projects can be the
  target; the implementation
  plan should name tested combinations (Prometheus and Loki version
  pairs where relevant) in `Implementation History`, not a single product bundle.

### Operator configuration

The Jumpstarter operator CR controls telemetry behavior cluster-wide.
Observability settings live under `spec.telemetry` so that administrators
can tune metrics, logging, and exemplar behavior without editing code.

**Key configurable fields:**

| Field                                     | Type       | Default                                          | Description                                                                                    |
| ----------------------------------------- | ---------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `spec.telemetry.enabled`                  | `bool`     | `false`                                          | Deploy the optional Telemetry service.                                                         |
| `spec.telemetry.loki.url`                 | `string`   | —                                                | Loki push endpoint; optional — Telemetry can run metrics-only without Loki.                    |
| `spec.telemetry.loki.secretRef`           | `string`   | —                                                | Secret with Loki credentials (see **DD-5**).                                                   |
| `spec.telemetry.loki.tls.caSecretRef`     | `string`   | —                                                | Secret containing a CA bundle (`ca.crt` key) to trust for the Loki endpoint.                   |
| `spec.telemetry.loki.tls.insecureSkipVerify` | `bool`  | `false`                                          | Disable TLS certificate verification (development/testing only).                               |
| `spec.telemetry.exporterLabels`           | `[]string` | `[]`                                             | Exporter-level label keys (e.g. `board-type`) copied from Exporter CRD labels into log JSON fields and exemplar candidates. |
| `spec.telemetry.metrics.exemplarKeys`     | `[]string` | `["client", "lease_id"]`                         | Allowlist of keys to include in exemplars (including `spec.context` and `exporterLabels` keys). Only listed keys are emitted; unlisted keys are omitted even if present. |
| `spec.telemetry.metrics.driverTypeEnum`   | `[]string` | `["power", "storage", "network", "serial", …]`  | Allowed `driver_type` label values. Drivers reporting an unlisted type are mapped to `other`.   |
| `spec.telemetry.metrics.serviceMonitor`   | `bool`     | `true`                                           | Create `ServiceMonitor` CRDs for Prometheus autodiscovery.                                     |
| `spec.telemetry.metrics.prometheusRules`  | `bool`     | `false`                                          | Deploy starter `PrometheusRule` CRDs (opt-in).                                                 |
| `spec.telemetry.metrics.scrapeTimeout`    | `duration` | `7s`                                             | Max time to wait for parallel exporter responses during a `/metrics` fan-out. Must leave headroom within the Prometheus-side `scrape_timeout` |
| `spec.telemetry.backpressure.queueDepth`  | `int`      | `10000`                                          | Ring buffer depth for Loki log push queue.                                                     |

**Example CR snippet:**

```yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
spec:
  telemetry:
    enabled: true
    exporterLabels:
      - board-type
    loki:
      url: "https://loki-gateway.monitoring.svc:3100/loki/api/v1/push"
      secretRef: "loki-credentials"
      tls:
        caSecretRef: "loki-ca-bundle"
    metrics:
      exemplarKeys:
        - client
        - lease_id
        - build_id
        - board-type
      driverTypeEnum:
        - power
        - storage
        - network
        - serial
        - video
        - composite
      serviceMonitor: true
      prometheusRules: true
      scrapeTimeout: "7s"
    backpressure:
      queueDepth: 20000
```

The `driverTypeEnum` list acts as an allowlist: drivers must select a
category from this set (or fall back to `other`). This keeps the
`driver_type` Prometheus label bounded and prevents cardinality
surprises from third-party drivers. Administrators can extend the list
for site-specific driver categories.

The `exporterLabels` list names Exporter CRD label keys whose values
are copied into every log JSON field and made available as exemplar
candidates for operations involving that exporter. For example, setting
`exporterLabels: ["board-type"]` means an Exporter with the label
`board-type: rpi4` will include `"board-type": "rpi4"` in its
structured log lines and in the exemplar candidate pool. The list is
empty by default — no exporter labels are propagated unless the
administrator opts in.

The `exemplarKeys` list is an **allowlist** that controls which keys are
included in Prometheus exemplars. This filters *everything* — built-in
keys (`client`, `lease_id`), `spec.context` keys, and `exporterLabels`
keys alike. Only keys present in `exemplarKeys` are emitted; unlisted
keys are omitted even if available. This gives administrators full
control over exemplar budget usage: adding `board-type` to both
`exporterLabels` and `exemplarKeys` propagates hardware type into
exemplars, while removing `lease_id` frees budget for other entries.

**Loki transport:** During implementation, evaluate whether the Telemetry
service should connect to Loki via the HTTP push API
(`/loki/api/v1/push`) or the gRPC endpoint. gRPC may offer better
throughput and streaming semantics (aligned with Jumpstarter's existing
gRPC infrastructure), while the HTTP API is simpler to debug and more
broadly supported by Loki-compatible backends. The `spec.telemetry.loki.url`
field should accept either scheme (`http://` / `grpc://`) so the choice
remains a deployment decision.

**Loki TLS:** Many deployments terminate Loki behind a TLS endpoint
with an internal or self-signed CA. The `spec.telemetry.loki.tls`
subsection follows the same pattern as the existing operator TLS
configuration: `caSecretRef` names a Kubernetes Secret whose `ca.crt`
key contains the PEM-encoded CA bundle to trust. When set, the
Telemetry service adds this CA to its TLS root pool when connecting to
Loki. `insecureSkipVerify` disables certificate verification entirely
and should only be used in development or testing environments.

## Test Plan

### Unit Tests

- Log field builders and redaction: ensure defaults strip secrets; optional
  fields behind flags.
- Metric registration helpers: label validation and naming conventions.

### Integration Tests

- Operator + exporter: scrape or receive metrics; assert presence of a minimal
  documented set of series after a known operation.
- If the control-plane forward path is implemented: with a test Loki and
  a Prometheus-compatible sink (or mock), assert that records arrive with expected
  correlation fields (`lease_id`, `exporter`, …) and that exporter pods do not require
  Loki or cluster-scrape credentials in their spec.
- If Telemetry runs with >1 replica: one test verifies that
  `sum` by business labels (dropping `pod`/`instance`) matches expected totals after partitioned increments (see **DD-8**).
- Lease with metadata: objects validate; events or status updates match expected
  structure.

### Hardware-in-the-Loop

- Flashing and power paths: at least one driver records an event and/or
  metrics counter on success and failure on real hardware in a lab.
- Serial and stream paths expose tx/rx byte counts.

### Independent testability

Each component must be testable in isolation without deploying the full
stack:

- **Structured logging**: unit tests validate JSON output format, base
  fields, and `spec.context` propagation using an in-memory logger — no
  Loki required.
- **Exporter metrics**: unit tests verify counter/histogram registration,
  label correctness, and exemplar attachment using a local Prometheus
  registry — no Telemetry service required.
- **Telemetry service**: integration tests use mock gRPC clients and a
  mock Loki endpoint to verify ingest, counter aggregation, backpressure
  behavior, and drop markers — no real exporters required.
- **Operator configuration**: unit tests validate CRD admission
  (e.g. `spec.context` size limits) and `ServiceMonitor` generation.

### End-to-end (CI)

The full telemetry pipeline should be exercised in GitHub Actions CI.
Evaluate feasibility of running a minimal Prometheus + Loki stack inside
the CI environment (e.g. single-binary mode containers); if resource
constraints make this impractical, at minimum:

- **Loki mock or single-binary**: a lightweight Loki instance (or a mock
  HTTP/gRPC endpoint that validates the Loki push API contract) receives logs
  from the Telemetry service and asserts expected fields, stream labels,
  and `spec.context` propagation across the full exporter → Telemetry →
  Loki path.
- **Prometheus scrape**: the existing Go/Ginkgo E2E test suite performs
  direct HTTP scrapes of the `/metrics` endpoints on Controller, Router,
  and Telemetry services — no separate Prometheus instance required. The
  test parses the OpenMetrics response and asserts that documented
  series, labels, and exemplars appear after a known operation sequence.
- **Correlation round-trip**: an E2E test runs a lease lifecycle (create →
  flash → power-cycle → release) and verifies that the same `lease_id`
  and `exporter` values appear in both scraped metrics (label or
  exemplar) and ingested log entries, confirming cross-signal
  correlation.

Feasibility of this stack should be evaluated early (Phase 1) so that
all subsequent phases have E2E coverage from the start.

### Manual

- `jmp` default output remains readable; JSON structured logs are only sent
  to jumpstarter-telemetry for general log ingest.

## Acceptance Criteria

- [ ] Exporter (or sidecar) exposes a documented metrics surface; drivers
      can contribute without reimplementing the HTTP server ad hoc in each
      driver.
- [ ] Controller and one data-plane service emit structured logs with a
      documented minimum field set;
- [ ] Operator provides a section to enable metrics, with the right details/secret
      references to integrate with Loki for pushing logs.
- [ ] Operator attempts to auto-configure Prometheus metric scraping on the right
      endpoints.
- [ ] A JSON schema (or equivalent machine-readable specification) is
      published for the structured log format, enabling consumers to
      validate log entries and detect regressions in field names or types.
- [ ] Backward compatibility: existing clients and manifests without the new
      fields continue to work; deployments that do not use hub forwarding
      behave as today.

## Graduation Criteria

### Experimental (first release behind flag or doc-only)

- JEP in Discussion; partial implementation; known gaps listed in
  *Unresolved Questions*.

### Stable

- Acceptance criteria met; SLOs for log volume and metric cardinality
  documented; upgrade notes for the operator and CLI.

## Backward Compatibility

- New CRD fields and labels must be optional; existing lease flows unchanged.
- gRPC: new metadata must be additive; servers tolerate missing trace and
  context fields from older clients; clients ignore unknown fields where
  applicable.
- No removal of current default CLI behavior; JSON logging only when selected.

## Consequences

### Positive

- **Operators** can route logs and metrics to existing Prometheus, Loki,
  and Perses-based stacks (self-hosted or platform-managed under
  the hood) without a mandatory OpenTelemetry Collector in front of
  Jumpstarter (see **DD-6**, **DD-10**).
- **CI** can correlate a failed run to equipment and build metadata.
- **Driver authors** get a single pattern for operation counters and event
  emission.
- **Security-conscious** users can run with minimal log fields and no trace.
- **Operators** can keep Loki, Prometheus, and related API tokens in-cluster
  only; exporters keep a single Jumpstarter trust relationship (**DD-5**).
- The optional Telemetry service isolates Loki/series work from the reconciler
  (**DD-7**, **DD-8**); Controller and Router carry no Loki client dependency,
  so a Loki outage cannot affect lease operations (**DD-4**).

### Negative

- More code paths, dependencies (for example a Prometheus client
  library, Loki HTTP client, and structured log helpers), and
  operability and documentation burden.
- Operators must run a functioning cluster log shipper (Promtail, Grafana
  Alloy, Vector, or equivalent) to see Controller and Router logs in Loki.
  This is near-universal in production Kubernetes but worth documenting for
  minimal or dev clusters.

### Risks

- High-cardinality metadata accidentally promoted to metric *labels* could
  overload TSDB. *Cardinality guidelines* restricts labels to bounded enums
  and routes variable context through exemplars and log line fields instead.
- Exemplars require the OpenMetrics exposition format and Prometheus >= 2.26
  with exemplar storage enabled (on by default since Prometheus 2.39).
  Operators on older Prometheus versions still get full metrics and logs;
  exemplar-based drill-down is unavailable until they upgrade.
- Prometheus / Loki / Perses-stack version drift in the field
  — document tested pairs; W3C Trace Context in gRPC remains
  best-effort across Python and Go (no OTel SDK requirement to
  propagate `traceparent` where needed).

## Rejected Alternatives

- **"All metrics and facts are *generated* only in the controller"** — would
  miss per-exporter and per-driver truth; rejected. *Forwarding*
  exporter-originated series and events *through* the control-plane (with
  stable labels) is not the same and remains in scope (see DD-5).
- *Requiring Loki- and Prometheus-ingest credentials on every exporter
  and edge* as the only supported model — rejected in favor of
  optional hub
  forwarding and of cluster-native collectors that also avoid per-host
  secrets, even though those collectors are not Jumpstarter-specific.
- **"Mandatory OpenTelemetry SDK and Collector"** for all metrics,
  logs, and traces — rejected for the reference architecture;
  rationale in **DD-6** (optional parallel deployment by operators is
  still fine).
- **"Unstructured logs everywhere; parse with regex"** — rejected as
  unscalable for joins with traces and multi-service incidents.
- **"Mandatory full tracing for every command"** — high overhead; rejected; prefer
  sampling and opt-in for heavy paths.

## Prior Art

- [Prometheus](https://prometheus.io/) and [Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/)
  — time-series metrics and alerting; [Prometheus naming and labels](https://prometheus.io/docs/practices/naming/)
  on cardinality and naming; remote write for non-scrape topologies;
  [Exemplars](https://prometheus.io/docs/instrumenting/exposition_formats/#exemplars)
  for attaching high-cardinality context to individual samples.
- [Grafana exemplar support](https://grafana.com/docs/grafana/latest/fundamentals/exemplars/)
  — visualizing exemplars in metric panels and linking to traces or logs.
- [Loki](https://grafana.com/oss/loki/) — log aggregation, label model, and push
  and query APIs; often combined with [Perses](https://perses.dev/) (see
  **DD-10**) and Grafana Agent / Alloy or
  [Promtail](https://grafana.com/docs/loki/latest/send-data/promtail/) for log
  shipping.
- [Grafana Tempo](https://grafana.com/oss/tempo/) or [Jaeger](https://www.jaegertracing.io/) — common trace backends
  (native or HTTP ingest; OTLP where the operator uses it — not a
  Jumpstarter code dependency; see **DD-6**).
- [Perses](https://perses.dev/) — CNCF dashboard project; Apache 2.0;
  Kubernetes-native CRDs; CUE/JSON spec with GitOps SDKs; focused on
  Prometheus, Loki, and Tempo data sources (see **DD-10**).
- [OpenTelemetry](https://opentelemetry.io/) and the
  [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/) —
  relevant as ecosystem and operator-side *optional* plumbing;
  this JEP intentionally does not adopt them in-process by default (**DD-6**).
- Other HiL / test systems often separate "run metadata" (like Jenkins build
  id) from device state; similar separation maps well to this JEP’s lease
  context + events.

## Unresolved Questions

- Event retention: Loki retention policy (per-tenant, per-stream retention
  classes) for annotated log events (**DD-2**); whether Jumpstarter should
  document recommended retention defaults or leave this to operators.

## Future Possibilities

- SLOs and error budgets on lease acquisition time, flash success rate, and
  mean time to recovery of exporters.
- Per-tenant or per-namespace dashboards as samples in the docs.
- *Not* part of this JEP: billing usage metering (could reuse metrics later).

## Implementation History

— JEP-0011 proposed: 2026-04-23
- JEP-0011 updated based on feedback: 2026-04-29

## References

- [JEP-0000 — JEP Process](JEP-0000-jep-process.md)
- [Kubernetes Events](https://kubernetes.io/docs/reference/kubernetes-api/cluster-resources/event-v1/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/) (`traceparent`)
- Upstream project docs for the Prometheus, Loki, and
  Perses versions (and optional Tempo / Jaeger if used) in a
  given deployment; pin versions in release notes
  and integration tests.

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0)*
