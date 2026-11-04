# Observability Integration

Jumpstarter exposes metrics and structured logs that can be consumed by the
standard Kubernetes observability stack. This page explains how to enable the
integration, what the operator creates, and how to validate it.

Two optional capabilities are configured under `spec.telemetry`:

| Capability | Field | Default | Requires |
| --- | --- | --- | --- |
| Prometheus autodiscovery | `metrics.serviceMonitor` | `true` | Prometheus Operator CRDs |
| Log shipper configuration | `logging.shipper.type` | `none` | A cluster log shipper |

> **A cluster log shipper is required to see Controller and Router logs in
> Loki.** Jumpstarter does not run one and does not push Controller/Router logs
> to Loki directly. The optional Telemetry service is the only Jumpstarter
> component that pushes to Loki, and it handles exporter/client log ingestion.

## Prometheus autodiscovery

When `metrics.serviceMonitor` is `true` (the default) the operator:

- creates metrics-only Services `<name>-controller-metrics` and
  `<name>-router-metrics`;
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
The labels do not change the metrics Service selector. The `controller-kind`
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
`ServiceMonitorReady` condition reports `CRDNotFound`. When the CRD is installed
later, the objects appear within five minutes without changing the CR.

Disable autodiscovery with `serviceMonitor: false`; the Services and
ServiceMonitors are removed.

## Log shipper configuration

Controller and Router write JSON lines to stdout and never hold Loki
credentials. To ship their logs, select a shipper type:

```yaml
spec:
  telemetry:
    logging:
      shipper:
        type: vector     # none | promtail | alloy | vector
    loki:
      url: "https://loki-gateway.monitoring.svc:3100/loki/api/v1/push"
```

The operator publishes `<name>-log-shipper`, a ConfigMap containing exactly one
shipper configuration key plus a `README.md`:

| Type | Key |
| --- | --- |
| `promtail` | `promtail.yaml` |
| `alloy` | `alloy.alloy` |
| `vector` | `vector.yaml` |
| `none` | no ConfigMap |

Grafana Alloy, Vector, and Promtail are the supported Phase 5 shippers. Promtail
is pinned to release `v3.6.0`, the last release with a published Linux binary;
it is deprecated upstream in favor of Alloy, so new deployments should prefer
Alloy or Vector.

Merge the key into your existing shipper deployment. The snippet:

- selects pods labeled `jumpstarter.dev/log-scrape="true"` in the CR namespace;
- promotes `component`, `exporter`, and `namespace` to Loki stream labels;
- keeps `client`, `lease_id`, and lease `spec.context` keys inside the JSON body
  so they can be queried with `| json | lease_id="..."`;
- forwards the original JSON line, so multi-line stack traces remain one record;
- contains no credentials.

If `spec.telemetry.loki.url` is unset, the snippet uses the `{{LOKI_URL}}`
placeholder. Set a real URL before running the shipper validator or deploying
the snippet. Credentials always come from the shipper's own Secret.

`LogShipperReady=True` means the operator generated the ConfigMap. It does not
mean a shipper is running or that Loki is reachable; those remain cluster
administrator responsibilities.

Changing the type replaces the ConfigMap data in one reconciliation. Setting
`type: none` removes the ConfigMap; the pod labels remain, so no pod restart is
caused by disabling log shipping.

### Grafana Alloy

The generated `alloy.alloy` rendering is validated against Grafana Alloy
release `v1.21.3` (revision `f2108cd`, 2026-10-28).

The rendering:

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
Secret RBAC. Credentials never belong in the generated ConfigMap.

### Vector

The generated `vector.yaml` rendering is validated against Vector release
`v0.61.0` with `vector validate`.

The rendering:

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

The operator splits `spec.telemetry.loki.url` into the Vector sink `endpoint`
and `/loki/api/v1/push` path. The rendering uses `compression: none` and the
`text` codec, which produces an uncompressed JSON push containing the original
JSON line. If your Loki ingress requires snappy/protobuf, change the compression
and validate the resulting sink configuration with `vector validate`.

Vector's `kubernetes_logs` source requires the Helm chart's hostPath mount for
`/var/log/pods` and read access to pods and namespaces. The Vector Helm chart
provides the expected RBAC. Configure Loki authentication in the sink's `auth`
block using values from the shipper's own Secret; never put credentials in the
generated ConfigMap:

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

The generated `promtail.yaml` rendering is validated with
`promtail -check-syntax` against Promtail release `v3.6.0`.

The rendering:

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
`-config.expand-env`; the generated relabel rule uses `${HOSTNAME}` as a second
safety check. The Helm chart DaemonSet must mount `/var/log/pods` read-only and
`/run/promtail` for positions, and grant `get`, `list`, and `watch` on pods,
namespaces, and nodes.

Configure Loki authentication in the Promtail `clients` entry from the
shipper's own Secret; never put credentials in the generated ConfigMap.

## Validate

```sh
kubectl -n jumpstarter-lab get svc -l jumpstarter.dev/metrics=true
kubectl -n jumpstarter-lab get servicemonitors.monitoring.coreos.com
kubectl -n jumpstarter-lab get configmap jumpstarter-log-shipper -o yaml
```

Validate the generated shipper configuration with the shipper's own tool:

```sh
kubectl -n jumpstarter-lab get configmap jumpstarter-log-shipper \
  -o jsonpath='{.data.promtail\.yaml}' > /tmp/promtail.yaml
HOSTNAME=test-node promtail \
  -config.file=/tmp/promtail.yaml \
  -config.expand-env \
  -check-syntax
```

For Alloy use both `alloy validate` and `alloy fmt --test`; for Vector use
`vector validate`. `alloy validate` checks components, required properties, and
unknown properties; `alloy fmt --test` checks formatting only.

```sh
kubectl -n jumpstarter-lab get configmap jumpstarter-log-shipper \
  -o jsonpath='{.data.alloy\.alloy}' > /tmp/alloy.alloy
alloy validate /tmp/alloy.alloy
alloy fmt --test /tmp/alloy.alloy
```

For Vector:

```sh
kubectl -n jumpstarter-lab get configmap jumpstarter-log-shipper \
  -o jsonpath='{.data.vector\.yaml}' > /tmp/vector.yaml
vector validate --no-environment /tmp/vector.yaml
```

The operator's live-delivery tests run the generated Alloy, Vector, and
Promtail configurations against a real single-binary Loki `v3.7.9`. The tests
push known JSON lines through the production shipper blocks and query Loki's
HTTP API for the exact stream labels and body fields. CI installs the pinned
Loki binary and sets `LOKI_BIN`; local runs skip the live-Loki tests when it is
absent.

The gated Kubernetes deployment test, enabled with `SHIPPER_K8S_E2E=1`, deploys
real Alloy, Vector, and Promtail DaemonSets with the generated ConfigMaps, a
labeled log-source Pod, and an in-cluster Loki. It queries Loki through the
Kubernetes service proxy and is intended for Kind or a disposable cluster; the
`controller-kind` CI workflow runs it after the operator e2e suite. It is not
part of the default unit-test suite.

## Conditions

| Condition | True reason | False reasons |
| --- | --- | --- |
| `ServiceMonitorReady` | `Created` | `Disabled`, `CRDNotFound`, `Failed` |
| `LogShipperReady` | `Created` | `Disabled`, `Failed` |

Both are informational and do not affect the aggregate `Ready` condition: a
cluster without the Prometheus Operator or a log shipper is a supported
configuration.

## Notes

- Only Controller and Router pods carry the discovery labels. Telemetry logs are
  pushed by the Telemetry service and are not scraped from stdout.
- Vector uses two Loki sinks so lines without an `exporter` field are sent
  without that label; Alloy and Promtail omit the label through their native
  pipelines. Vector also falls back to the pod's `component` label, then
  `"unknown"`, when a line has no usable JSON `component`.
- The feature does not deploy, operate, or manage Prometheus, Loki, or any
  shipper; it only labels workloads and publishes integration configuration,
  and does not manage shipper credentials.
