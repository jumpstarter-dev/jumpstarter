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
The labels do not change the metrics Service selector.

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
        type: promtail   # none | promtail | alloy | vector
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
promtail -check-config.file=/tmp/promtail.yaml
```

For Alloy use `alloy fmt --test`, for Vector use `vector validate`.

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
- Vector cannot conditionally omit a templated label; lines without an
  `exporter` field get `exporter=""` in the Vector rendering. Promtail and Alloy
  omit the label.
- The feature does not install, configure, or upgrade Prometheus, Loki, or any
  shipper, and does not manage their credentials.
