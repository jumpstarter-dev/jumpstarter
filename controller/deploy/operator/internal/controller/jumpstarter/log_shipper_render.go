/*
Copyright 2025.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package jumpstarter

import (
	"fmt"
	"net/url"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

// alloyValidatedVersion is the Grafana Alloy release used to validate the
// generated Alloy rendering with `alloy validate` and `alloy fmt --test`.
const alloyValidatedVersion = "v1.21.3"

// vectorValidatedVersion is the Vector release used to validate the generated
// Vector rendering with `vector validate`.
const vectorValidatedVersion = "v0.61.0"

// promtailValidatedVersion is the last Promtail release with a published Linux
// binary. Promtail is deprecated upstream; this pin is the compatibility target
// for the Phase 5 all-three-shipper requirement.
const promtailValidatedVersion = "v3.6.0"

// lokiURL returns the configured Loki endpoint or the documented placeholder.
// Credentials are never embedded; the shipper's own Secret supplies them.
func lokiURL(js *operatorv1alpha1.Jumpstarter) string {
	if js.Spec.Telemetry != nil && js.Spec.Telemetry.Loki.URL != "" {
		return js.Spec.Telemetry.Loki.URL
	}
	return "{{LOKI_URL}}"
}

// vectorLokiEndpointAndPath splits a Loki push URL into the base endpoint and
// path expected by the Vector loki sink. Vector appends path to endpoint, so
// passing a full push URL as endpoint would duplicate /loki/api/v1/push.
func vectorLokiEndpointAndPath(raw string) (endpoint, path string) {
	if raw == "" || raw == "{{LOKI_URL}}" {
		return "http://loki.invalid:3100", "/loki/api/v1/push"
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return raw, "/loki/api/v1/push"
	}
	path = u.EscapedPath()
	if path == "" {
		path = "/loki/api/v1/push"
	}
	if u.RawQuery != "" {
		path += "?" + u.RawQuery
	}
	return u.Scheme + "://" + u.Host, path
}

// renderShipper returns the ConfigMap key and content for the selected shipper.
func renderShipper(js *operatorv1alpha1.Jumpstarter, shipper string) (key, content string) {
	switch shipper {
	case shipperTypePromtail:
		return "promtail.yaml", renderPromtail(js)
	case shipperTypeAlloy:
		return "alloy.alloy", renderAlloy(js)
	case shipperTypeVector:
		return "vector.yaml", renderVector(js)
	default:
		return "", ""
	}
}

const promtailPipelineStages = `      - cri: {}
      - json:
          expressions:
            component: component
            exporter: exporter
      - labels:
          component:
          exporter:
      - labeldrop:
          - filename
          - job
          - stream
`

const promtailTemplate = `# Jumpstarter log shipper configuration for Promtail.
# Merge into your Promtail scrape_configs. If the Loki URL below is a
# placeholder, replace it with your push endpoint (or set
# spec.telemetry.loki.url on the Jumpstarter CR).
#
# Run Promtail with -config.expand-env and set HOSTNAME from spec.nodeName.
# Promtail also uses HOSTNAME for its automatic node selector.
# The Promtail Helm chart mounts /var/log/pods and /run/promtail.
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /run/promtail/positions.yaml

clients:
  - url: "%s"

scrape_configs:
  - job_name: jumpstarter
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names:
            - %s
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_jumpstarter_dev_log_scrape]
        regex: "true"
        action: keep
      - source_labels: [__meta_kubernetes_pod_node_name]
        regex: "${HOSTNAME}"
        action: keep
      - source_labels: [__meta_kubernetes_namespace]
        target_label: namespace
      - source_labels:
          - __meta_kubernetes_pod_uid
          - __meta_kubernetes_pod_container_name
        separator: /
        target_label: __path__
        replacement: /var/log/pods/*$1/*.log
      - action: labeldrop
        regex: job|pod|container|stream|instance
    pipeline_stages:
` + promtailPipelineStages

const alloyScrapeTemplate = `// Jumpstarter log shipper configuration for Grafana Alloy.
// Merge into your Alloy configuration. If the Loki URL below is a
// placeholder, replace it with your push endpoint (or set
// spec.telemetry.loki.url on the Jumpstarter CR).
//
// This assumes the Alloy Helm chart DaemonSet, where HOSTNAME is the
// Kubernetes node name. For a single-replica Deployment, remove the
// selectors.field line or enable Alloy clustering.
discovery.kubernetes "jumpstarter_pods" {
	role = "pod"

	namespaces {
		names = ["%s"]
	}

	selectors {
		role  = "pod"
		label = "jumpstarter.dev/log-scrape=true"
		field = "spec.nodeName=" + coalesce(sys.env("HOSTNAME"), constants.hostname)
	}
}

discovery.relabel "jumpstarter_pods" {
	targets = discovery.kubernetes.jumpstarter_pods.targets

	rule {
		source_labels = ["__meta_kubernetes_pod_label_jumpstarter_dev_log_scrape"]
		regex         = "true"
		action        = "keep"
	}

	rule {
		source_labels = ["__meta_kubernetes_namespace"]
		target_label  = "namespace"
	}
}

loki.source.kubernetes "jumpstarter" {
	targets    = discovery.relabel.jumpstarter_pods.output
	forward_to = [loki.process.jumpstarter.receiver]
}

`

// alloyProcessWriteTemplate is shared by the generated shipper config and the
// live delivery test so both exercise the same stream-label and write path.
const alloyProcessWriteTemplate = `loki.process "jumpstarter" {
	stage.json {
		expressions = {
			component = "component",
			exporter  = "exporter",
		}
	}

	stage.labels {
		values = {
			component = "",
			exporter  = "",
		}
	}

	// Enforce the JEP-0013 stream-label set and remove the default "job"
	// label added by loki.source.kubernetes.
	stage.label_keep {
		values = ["component", "exporter", "namespace"]
	}

	forward_to = [loki.write.default.receiver]
}

loki.write "default" {
	endpoint {
		url = "%s"
	}
}
`

const vectorSourceTemplate = `# Jumpstarter log shipper configuration for Vector.
# Merge into your Vector configuration. If the Loki URL below is a
# placeholder, replace it with your push endpoint (or set
# spec.telemetry.loki.url on the Jumpstarter CR).
sources:
  jumpstarter_pods:
    type: kubernetes_logs
    extra_label_selector: "jumpstarter.dev/log-scrape=true"
    extra_field_selector: "metadata.namespace=%s"

`

const vectorProcessBlocks = `  jumpstarter_parse:
    type: remap
    inputs: [jumpstarter_pods]
    source: |
      parsed, err = parse_json(.message)
      if err == null {
        .component = parsed.component
        if exists(parsed.exporter) && !is_null(parsed.exporter) && parsed.exporter != "" {
          .exporter = parsed.exporter
        } else {
          del(.exporter)
        }
      } else {
        del(.exporter)
      }

  jumpstarter_with_exporter:
    type: filter
    inputs: [jumpstarter_parse]
    condition: 'exists(.exporter) && !is_null(.exporter) && .exporter != ""'

  jumpstarter_without_exporter:
    type: filter
    inputs: [jumpstarter_parse]
    condition: '!exists(.exporter) || is_null(.exporter) || .exporter == ""'

`

const vectorSinksTemplate = `sinks:
  jumpstarter_loki_with_exporter:
    type: loki
    inputs: [jumpstarter_with_exporter]
    endpoint: "%s"
    path: "%s"
    compression: none
    encoding:
      codec: text
    labels:
      component: "{{ component }}"
      exporter: "{{ exporter }}"
      namespace: "{{ kubernetes.pod_namespace }}"

  jumpstarter_loki_without_exporter:
    type: loki
    inputs: [jumpstarter_without_exporter]
    endpoint: "%s"
    path: "%s"
    compression: none
    encoding:
      codec: text
    labels:
      component: "{{ component }}"
      namespace: "{{ kubernetes.pod_namespace }}"
`

const vectorTemplate = vectorSourceTemplate + `transforms:
` + vectorProcessBlocks + vectorSinksTemplate

// renderPromtail renders the Promtail configuration.
func renderPromtail(js *operatorv1alpha1.Jumpstarter) string {
	return fmt.Sprintf(promtailTemplate, lokiURL(js), js.Namespace)
}

// renderAlloy renders the Grafana Alloy configuration.
func renderAlloy(js *operatorv1alpha1.Jumpstarter) string {
	return fmt.Sprintf(alloyScrapeTemplate, js.Namespace) + fmt.Sprintf(alloyProcessWriteTemplate, lokiURL(js))
}

// renderVector renders the Vector configuration.
func renderVector(js *operatorv1alpha1.Jumpstarter) string {
	endpoint, path := vectorLokiEndpointAndPath(lokiURL(js))
	return fmt.Sprintf(vectorTemplate, js.Namespace, endpoint, path, endpoint, path)
}

// renderShipperReadme documents how to use the generated configuration.
func renderShipperReadme(js *operatorv1alpha1.Jumpstarter, shipper, key string) string {
	readme := fmt.Sprintf(`# Jumpstarter log shipper configuration

This ConfigMap was generated by the Jumpstarter operator for the
Jumpstarter resource %q in namespace %q.

## What it selects

Pods labeled:

    jumpstarter.dev/log-scrape="true"

That is the Jumpstarter controller and router only. Telemetry logs are
pushed directly to Loki by the telemetry service and are not scraped here.

## How to use it

1. Copy the %q key into your %s configuration and merge it with the
   existing scrape/pipeline definitions.
2. Replace the {{LOKI_URL}} placeholder with your Loki push endpoint, or set
   spec.telemetry.loki.url on the Jumpstarter CR and let the operator render it.
3. Configure credentials in your shipper's own Secret. This ConfigMap never
   contains credentials, and the Jumpstarter controller and router never hold
   Loki credentials.

## Stream labels

Only "component", "exporter", and "namespace" are promoted to Loki stream
labels. Fields such as "client", "lease_id" and any lease spec.context keys stay
in the JSON body.
Query them with a JSON filter, for example:

    {component="controller"} | json | lease_id="<id>"

## Note

When a log line has no "exporter" field, the Vector rendering routes it through
a separate sink without the exporter label. All supported shippers omit the
label when it is absent.

A cluster log shipper is required to see controller and router logs in Loki.
`, js.Name, js.Namespace, key, shipper)

	if shipper == shipperTypeAlloy {
		readme += fmt.Sprintf(`
## Alloy validation

The Alloy rendering is validated with "alloy validate" and
"alloy fmt --test" against Grafana Alloy release %s.

The snippet assumes the Alloy Helm chart DaemonSet, where HOSTNAME is the
Kubernetes node name. For a single-replica Deployment, remove the
selectors.field line or enable Alloy clustering.
`, alloyValidatedVersion)
	}

	if shipper == shipperTypeVector {
		readme += fmt.Sprintf(`
## Vector validation

The Vector rendering is validated with "vector validate" against Vector release
%s.

The snippet assumes the Vector Helm chart DaemonSet, which sets
VECTOR_SELF_NODE_NAME from spec.nodeName and mounts /var/log/pods. The operator
splits the configured Loki URL into the sink endpoint and /loki/api/v1/push
path. Two Loki sinks keep the exporter label only when it is present.
`, vectorValidatedVersion)
	}

	if shipper == shipperTypePromtail {
		readme += fmt.Sprintf(`
## Promtail validation

The Promtail rendering is validated with "promtail -check-syntax" against
Promtail release %s.

Run Promtail with -config.expand-env and set HOSTNAME from spec.nodeName.
Promtail also uses HOSTNAME for its automatic node selector. The Helm chart
DaemonSet mounts /var/log/pods and /run/promtail. The config resolves the pod
log path with __path__, strips the Kubernetes CRI prefix, and drops the default
job/filename labels so Loki receives exactly component, exporter, and namespace.
`, promtailValidatedVersion)
	}

	return readme
}
