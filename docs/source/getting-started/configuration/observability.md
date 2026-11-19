# Observability Integration

Jumpstarter exposes metrics and structured logs that can be consumed by the
standard Kubernetes observability stack. This page explains how to enable the
integration, what the operator creates, and how to validate it. For Perses
dashboards and datasource prerequisites, see
[Dashboards and Alerting](dashboards-alerting.md).

Prometheus autodiscovery is configured under `spec.telemetry`:

| Capability | Field | Default | Requires |
| --- | --- | --- | --- |
| Prometheus autodiscovery | `metrics.serviceMonitor` | `true` | Prometheus Operator CRDs |
| Starter alert rules | `metrics.prometheusRules` | `false` | Prometheus Operator CRDs |

> **A cluster log shipper is required to see Controller and Router logs in
> Loki.** Jumpstarter does not run one and does not push Controller/Router logs
> to Loki directly. Controller and Router pods emit structured JSON to stdout
> and carry discovery labels; the examples below show how to configure a
> cluster shipper independently. The optional Telemetry service is the only
> Jumpstarter component that pushes to Loki, and it handles exporter/client log
> ingestion.

## Prometheus autodiscovery

When `metrics.serviceMonitor` is `true` (the default) the operator:

- creates metrics-only Services `<name>-metrics-controller` and
  `<name>-metrics-router`;
- reuses the existing telemetry Service when the telemetry service is enabled;
- creates one `ServiceMonitor` per running workload **if** the
  `monitoring.coreos.com/v1` CRD is installed.

Every generated `ServiceMonitor` carries the stable selector label
`jumpstarter.dev/metrics: "true"`. The Prometheus `serviceMonitorSelector` must
select that label, either directly or by using `serviceMonitorLabels` below.

```yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: jumpstarter-lab
spec:
  telemetry:
    metrics:
      serviceMonitor: true
      serviceMonitorLabels:
        release: kube-prometheus-stack
```

`serviceMonitorLabels` are added to the `ServiceMonitor` metadata so the
objects match your Prometheus `serviceMonitorSelector`. kube-prometheus-stack
usually selects a Helm release label such as `release: kube-prometheus-stack`;
if your Prometheus uses a different selector, set the matching labels here.
Operator labels take precedence: a key the operator already sets
(`app.kubernetes.io/*` or `jumpstarter.dev/metrics`) keeps its generated value,
so the monitors stay selectable and cannot contradict the Services they
select. The labels do not change the metrics Service selector. The `controller-kind`
target-health test installs a minimal Prometheus Operator and Prometheus
instance and asserts that the generated ServiceMonitors produce `up` targets
for controller and router within 120 seconds, with no additional scrape
configuration. The 120-second bound covers Prometheus Operator discovery and
configuration reload; scrape freshness is measured separately, and once a
target is discovered its last scrape is no older than two configured scrape
intervals. That interval comes from the Prometheus CR (the test configures five
seconds), so a slower scrape configuration relaxes the freshness check.

If the Prometheus Operator is not installed, reconciliation still succeeds: the
metrics Services are created, ServiceMonitors are skipped, and the
`ServiceMonitorReady` condition reports `CRDNotFound`. Installing the CRD later
does not require changing the CR: the operator watches CustomResourceDefinitions
and reconciles as soon as the ServiceMonitor kind is served. If the API cannot be
reached, the failed reconcile is retried with exponential backoff.

Disable autodiscovery with `serviceMonitor: false`; the Services and
ServiceMonitors are removed.

## Starter alert rules

Set `metrics.prometheusRules` to `true` to have the operator create one
`PrometheusRule` named `<name>-starter-alerts` when the
`monitoring.coreos.com/v1` PrometheusRule CRD is installed. The rules are
opt-in because thresholds depend on the environment.

```yaml
spec:
  telemetry:
    metrics:
      prometheusRules: true
      ruleLabels:
        release: kube-prometheus-stack
```

The starter set implements the JEP alert table:

| Alert | Scope | Starter threshold |
| --- | --- | --- |
| `JumpstarterOperationFailureRate` | Operations | Failure rate above 20% over 15 minutes per exporter and operation |
| `JumpstarterOperationDurationHigh` | Operations | p95 duration above 60 seconds per operation |
| `JumpstarterOperationErrors` | Operations | Recurring errors (5-minute window held for 15 minutes), grouped by exporter and `error_type` |
| `JumpstarterExporterIdle` | Operations | No active session for 30 minutes after operations in the last hour |
| `JumpstarterLeaseAcquisitionFailureRate` | Operations | Failure rate above 10% over 15 minutes |
| `JumpstarterTelemetryDropped` | Telemetry | Any dropped log entry over 5 minutes |
| `JumpstarterScrapeTimeouts` | Telemetry | More than three timeouts per exporter over 15 minutes |

Telemetry-pipeline alerts use `critical`; operation and lease alerts use
`warning`. Edit the generated `PrometheusRule` to tune thresholds, or set
`prometheusRules: false` to remove the owned rule.

`ruleLabels` are added to the `PrometheusRule` metadata so the object matches
your Prometheus `ruleSelector`. The stable label
`jumpstarter.dev/prometheus-rules: "true"` is always set and cannot be
overridden. If the CRD is absent, reconciliation succeeds, no rule is created,
and `PrometheusRulesReady` reports `CRDNotFound`.

## Cluster log shipper integration

Controller and Router write JSON lines to stdout and never hold Loki
credentials. This includes klog output and Gin request/recovery logs, which are
routed through the same structured logger with the `component` field. The
destination shipper must handle any unexpected third-party text.

Jumpstarter does not generate a shipper ConfigMap or deploy a shipper. Configure
the shipper through your existing Alloy, Vector, Promtail, or equivalent
deployment. The code examples below show only parts of each configuration, such
as the Loki destination. The required pod discovery and stream-label rules are
stated in each shipper's section; adapt the examples to the chart or manifest
you already operate.

The Controller/Router pods carry:

```text
jumpstarter.dev/log-scrape="true"
jumpstarter.dev/log-format="json"
```

The shipper configuration should:

- select pods labeled `jumpstarter.dev/log-scrape="true"` in the CR namespace;
- promote `component`, `exporter`, and `namespace` to Loki stream labels;
  Vector uses a separate sink so `exporter` is present only when the JSON line
  contains it;
- keep `client`, `lease_id`, and lease `spec.context` keys inside the JSON body
  so they can be queried with `| json | lease_id="..."`;
- forward the original JSON line, so multi-line stack traces remain one record;
- obtain Loki credentials from the shipper's own Secret.

Grafana Alloy, Vector, and Promtail are the supported examples. Promtail is
pinned to release `v3.6.0`, the last release with a published Linux binary; it
is deprecated upstream in favor of Alloy, so new deployments should prefer
Alloy or Vector.

### Grafana Alloy

The example Alloy configuration is validated against Grafana Alloy release
`v1.21.3` (revision `f2108cd`, 2026-10-28).

The example:

- filters Kubernetes discovery to pods labeled
  `jumpstarter.dev/log-scrape="true"`;
- assumes the Alloy Helm chart DaemonSet and uses a `spec.nodeName` field
  selector, so each Alloy pod tails only the Controller/Router pods on its node;
- enforces the JEP-0013 stream-label set with `stage.label_keep`. This also
  removes Alloy's default `job` label;
- keeps `client`, `lease_id`, and lease `spec.context` fields in the JSON body.

For a single-replica Alloy Deployment, remove `selectors.field`; otherwise it
only collects pods on its own node. For clustered Alloy, enable
`loki.source.kubernetes.clustering` instead of node filtering.

Alloy needs `get`, `list`, and `watch` on `pods`, `pods/log`, and `namespaces`.
The Alloy Helm chart's default RBAC grants these. If you use
`remote.kubernetes.secret` for Loki authentication, Alloy also needs `get` on
`secrets`, which the chart does not grant by default. Prefer mounting the
shipper's Secret and using file references:

```hcl
loki.write "default" {
  endpoint {
    url = "https://loki.example/loki/api/v1/push"

    basic_auth {
      username      = "jumpstarter"
      password_file = "/etc/alloy/loki-password"
    }

    tls_config {
      ca_file = "/etc/alloy/loki-ca.crt"
    }
  }
}
```

The `remote.kubernetes.secret` alternative is also valid, but requires the extra
Secret RBAC. Configure credentials in the shipper's own Secret or through your
existing secret-injection mechanism.

### Vector

The example Vector configuration is validated against Vector release
`v0.61.0` with `vector validate`.

The example:

- selects pods labeled `jumpstarter.dev/log-scrape="true"` in the CR namespace;
- uses the `kubernetes_logs` source, which is DaemonSet-oriented and reads
  `/var/log/pods` through a hostPath volume;
- relies on the Vector Helm chart setting `VECTOR_SELF_NODE_NAME` from
  `spec.nodeName`, so each DaemonSet pod reads only its node's pods;
- routes events through two Loki sinks so the `exporter` stream label appears
  only when the JSON line contains it;
- promotes `component`, `exporter`, and `namespace`; the namespace value comes
  from `kubernetes.pod_namespace`;
- falls back to the pod's `component` label when the JSON line has no usable
  `component`, then to `"unknown"` when the label is also missing or empty;
- keeps `client`, `lease_id`, and lease `spec.context` keys in the original
  JSON body.

Vector's remap transform resolves the `component` stream label in three steps:
a valid, non-empty JSON `component` field is used when present; otherwise the
pod's `component` label is used; otherwise `"unknown"` is used. Non-JSON lines,
and JSON lines whose `component` is missing, empty, `null`, or not a string, are
forwarded unchanged with that fallback. Only `component`, `exporter`, and
`namespace` are stream labels; no high-cardinality pod or container labels are
promoted.

Split the Loki push URL into the Vector sink `endpoint` and
`/loki/api/v1/push` path. Do not pass the complete push URL as `endpoint`:
Vector appends `path`, which would duplicate `/loki/api/v1/push`.

The example uses `compression: none` and the `text` codec, which produces an
uncompressed JSON push containing the original JSON line. If your Loki ingress
requires snappy/protobuf, change the compression and validate the resulting
sink configuration with `vector validate`.

Vector's `kubernetes_logs` source requires the Helm chart's hostPath mount for
`/var/log/pods` and read access to pods and namespaces. The Vector Helm chart
provides the expected RBAC. Configure Loki authentication in the sink's `auth`
block using values from the shipper's own Secret:

```yaml
sinks:
  jumpstarter_loki_with_exporter:
    type: loki
    endpoint: "https://loki.example:3100"
    path: "/loki/api/v1/push"
    auth:
      strategy: basic
      user: "jumpstarter"
      password: "${LOKI_PASSWORD}"
```

Use `VECTOR_SELF_NODE_NAME` and `LOKI_PASSWORD` from the Vector process
environment or an external secret injection mechanism. A single-replica Vector
Deployment is not a supported substitute for the DaemonSet because the source
reads node-local pod log files.

### Promtail

The example Promtail configuration is validated with
`promtail -check-syntax` against Promtail release `v3.6.0`.

The example:

- selects pods labeled `jumpstarter.dev/log-scrape="true"` in the CR namespace;
- resolves the pod log path with `__path__ =
  /var/log/pods/*<pod-uid>/<container>/*.log`;
- adds a `cri` stage before JSON parsing so Kubernetes CRI log prefixes are
  stripped;
- promotes `component`, `exporter`, and `namespace`;
- drops the default `job`, `filename`, and `stream` labels, so the stream-label
  set is exact;
- keeps `client`, `lease_id`, and lease `spec.context` keys in the JSON body.

Promtail automatically restricts `kubernetes_sd_configs` pod discovery using
the `HOSTNAME` environment variable as a `spec.nodeName` selector. Set
`HOSTNAME` from the pod's `spec.nodeName` and run Promtail with
`-config.expand-env`; the example relabel rule uses `${HOSTNAME}` as a second
safety check. The Helm chart DaemonSet must mount `/var/log/pods` read-only and
`/run/promtail` for positions, and grant `get`, `list`, and `watch` on pods,
namespaces, and nodes.

Configure Loki authentication in the Promtail `clients` entry from the
shipper's own Secret.

## Validate

```sh
kubectl -n jumpstarter-lab get svc -l jumpstarter.dev/metrics=true
kubectl -n jumpstarter-lab get servicemonitors.monitoring.coreos.com
```

Validate your shipper configuration with the shipper's own tool. The examples
above are intended to be adapted into the shipper's existing configuration:

```sh
HOSTNAME=test-node promtail \
  -config.file=/etc/promtail/config.yml \
  -config.expand-env \
  -check-syntax

alloy validate /etc/alloy/config.alloy
alloy fmt --test /etc/alloy/config.alloy

vector validate --no-environment /etc/vector/vector.yaml
```

`alloy validate` checks components, required properties, and unknown
properties; `alloy fmt --test` checks formatting only.

The snippets in this page are documentation fixtures. The operator no longer
generates a shipper ConfigMap, and the cluster administrator owns shipper
deployment, credentials, and validation.

## Conditions

| Condition | True reason | False reasons |
| --- | --- | --- |
| `ServiceMonitorReady` | `Created` | `Disabled`, `CRDNotFound`, `Failed` |
| `PrometheusRulesReady` | `Created` | `Disabled`, `CRDNotFound`, `Failed` |

Both conditions are informational and do not affect the aggregate `Ready`
condition: a cluster without the Prometheus Operator is a supported
configuration.

## Notes

- Only Controller and Router pods carry the discovery labels. Telemetry logs are
  pushed by the Telemetry service and are not scraped from stdout.
- Vector uses two Loki sinks so lines without an `exporter` field are sent
  without that label; Alloy and Promtail omit the label through their native
  pipelines. Vector also falls back to the pod's `component` label, then
  `"unknown"`, when a line has no usable JSON `component`.
- The feature does not deploy, operate, or manage Prometheus, Loki, or any
  shipper; it only labels workloads and documents integration requirements, and
  does not manage shipper credentials.
