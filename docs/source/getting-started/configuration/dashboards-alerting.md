# Dashboards and Alerting

Jumpstarter can publish a Perses overview dashboard for Controller, Router,
exporter, and Telemetry health, alongside the opt-in starter alerts described in
[Observability](observability.md). The operator creates declarative Perses
resources; it does not deploy or operate Prometheus, Loki, Alertmanager, or
Perses.

## Prerequisites

- The Prometheus Operator CRDs (`ServiceMonitor`, `PrometheusRule`) for metrics
  autodiscovery and starter alerts.
- The Perses Operator **v0.6** (or a compatible release) with the
  `perses.dev/v1alpha2` CRDs, and a `Perses` instance in the cluster.
- Prometheus and Loki datasources registered in Perses. The operator references
  them by name; it does not create them.
- Alertmanager for routing the starter alerts (optional for dashboards).

## Enable dashboards

```yaml
spec:
  telemetry:
    enabled: true
    metrics:
      serviceMonitor: true
      dashboards: true
      persesInstanceSelector:
        app.kubernetes.io/instance: perses-sample
      dashboardLabels:
        perses.dev/instance: perses-sample
      prometheusDatasource: prometheus
      lokiDatasource: loki
```

`dashboards` defaults to `false`. When enabled, the operator creates one
`PersesDashboard` named `<name>-overview` in the Jumpstarter namespace, labelled
`jumpstarter.dev/dashboards: "true"` plus `dashboardLabels`. The
`persesInstanceSelector` is required and is mapped to the dashboard's
`spec.instanceSelector.matchLabels`; an empty selector leaves the dashboard
uncreated and reports `DashboardsReady=False/NotConfigured`.

The generated dashboard contains:

- variables for `exporter` and `driver_type` (Prometheus) and a `lease_id` text
  variable;
- operation failure rate, p95 duration, errors by type, active sessions, stream
  throughput, lease acquisitions, top exporters, telemetry drops, and scrape
  timeout panels;
- a `LogsTable` panel using
  `{component="exporter"} | json | lease_id="$lease_id"`;
- a Grid layout referencing every panel.

No dashboard-level `datasources` map is emitted. Each query carries an explicit
selector such as:

```json
"datasource": { "kind": "PrometheusDatasource", "name": "prometheus" }
```

## Datasource prerequisites

Perses resolves datasource names across scopes: global, project, and dashboard,
with the lower scope taking precedence. The operator references the configured
names directly, so create matching datasources in one of these scopes.

### Global datasources

```yaml
apiVersion: perses.dev/v1alpha2
kind: PersesGlobalDatasource
metadata:
  name: prometheus
spec:
  instanceSelector:
    matchLabels:
      app.kubernetes.io/instance: perses-sample
  config:
    default: true
    plugin:
      kind: PrometheusDatasource
      spec:
        directUrl: http://prometheus.monitoring.svc:9090
---
apiVersion: perses.dev/v1alpha2
kind: PersesGlobalDatasource
metadata:
  name: loki
spec:
  instanceSelector:
    matchLabels:
      app.kubernetes.io/instance: perses-sample
  config:
    plugin:
      kind: LokiDatasource
      spec:
        directUrl: http://loki-gateway.monitoring.svc:3100
```

### Project datasources

A `PersesDatasource` is namespace-scoped and becomes a datasource in the Perses
project matching its namespace. Create it in the Jumpstarter CR namespace if you
prefer project isolation:

```yaml
apiVersion: perses.dev/v1alpha2
kind: PersesDatasource
metadata:
  name: prometheus
  namespace: jumpstarter-lab
spec:
  instanceSelector:
    matchLabels:
      app.kubernetes.io/instance: perses-sample
  config:
    plugin:
      kind: PrometheusDatasource
      spec:
        directUrl: http://prometheus.monitoring.svc:9090
```

The datasource name in the selector is the CR's `metadata.name`; the
`display.name` field is only the UI label. The datasource CR's
`instanceSelector` should match, or at least overlap, the dashboard's selector
so both land on the same Perses instance. The operator does not validate that
the named datasource exists: the dashboard syncs, and a missing datasource
surfaces as a panel query error at runtime.

## Starter alerts

The dashboard is independent of the starter alerts. To create the
`PrometheusRule` set as well, enable `spec.telemetry.metrics.prometheusRules`;
see [Observability](observability.md) for the alert table, severities, and
tuning guidance.

## Conditions

| Condition | True reason | False reasons |
| --- | --- | --- |
| `DashboardsReady` | `Created` | `Disabled`, `NotConfigured`, `CRDNotFound`, `Failed` |
| `PrometheusRulesReady` | `Created` | `Disabled`, `CRDNotFound`, `Failed` |

Both are informational and excluded from the aggregate `Ready` condition.

## Validate

The generated dashboard is validated offline with `percli`, matching the Perses
release used by the operator test suite:

```sh
make -C controller/deploy/operator test-phase6-dashboards
```

The target requires `PERCLI` and `PERCLI_PLUGINS`; CI installs a pinned
`percli` and extracts its plugin archives. `percli lint` accepts a Perses
`Dashboard`, not the Kubernetes `PersesDashboard` CR, so the validation test
extracts `spec.config` and wraps it with `kind: Dashboard` and
`metadata.project`. `percli lint --online` can add server-side checks but needs
a live Perses API and the datasources.

You can also inspect the generated objects directly:

```sh
kubectl -n jumpstarter-lab get persesdashboards.perses.dev \
  -l jumpstarter.dev/dashboards=true
```

## Versions and compatibility

- Perses Operator **v0.6** with `perses.dev/v1alpha2` is the tested API.
  `v1alpha2` is marked unstable upstream; the operator isolates the resource
  shape behind its builder and CRD probe.
- The operator test suite uses Perses **0.53.1** (the operator sample image);
  the Perses documentation currently tracks **v0.55**.
- Perses does not render Prometheus exemplars yet. Use the `lease_id` LogQL
  panel for correlation, or point Grafana at the same Prometheus and Loki
  datasources if exemplar overlays are required.
- The operator does not deploy Perses or its datasources, and does not manage
  their credentials.

## Related

- [Observability](observability.md): Prometheus autodiscovery, starter alert
  rules, and cluster log-shipper integration.
- [JEP-0013](../../contributing/jeps/JEP-0013-observability-telemetry-logs.md):
  the observability design and phase plan.
