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

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

// lokiURL returns the configured Loki endpoint or the documented placeholder.
// Credentials are never embedded; the shipper's own Secret supplies them.
func lokiURL(js *operatorv1alpha1.Jumpstarter) string {
	if js.Spec.Telemetry != nil && js.Spec.Telemetry.Loki.URL != "" {
		return js.Spec.Telemetry.Loki.URL
	}
	return "{{LOKI_URL}}"
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

const promtailTemplate = `# Jumpstarter log shipper configuration for Promtail.
# Merge into your Promtail scrape_configs. If the Loki URL below is a
# placeholder, replace it with your push endpoint (or set
# spec.telemetry.loki.url on the Jumpstarter CR).
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
      - source_labels: [__meta_kubernetes_namespace]
        target_label: namespace
    pipeline_stages:
      - json:
          expressions:
            component: component
            exporter: exporter
      - labels:
          component:
          exporter:
`

const alloyTemplate = `// Jumpstarter log shipper configuration for Grafana Alloy.
// Merge into your Alloy configuration. If the Loki URL below is a
// placeholder, replace it with your push endpoint (or set
// spec.telemetry.loki.url on the Jumpstarter CR).
discovery.kubernetes "jumpstarter_pods" {
  role = "pod"
  namespaces {
    names = ["%s"]
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

loki.process "jumpstarter" {
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

  forward_to = [loki.write.default.receiver]
}

loki.write "default" {
  endpoint {
    url = "%s"
  }
}
`

const vectorTemplate = `# Jumpstarter log shipper configuration for Vector.
# Merge into your Vector configuration. If the Loki URL below is a
# placeholder, replace it with your push endpoint (or set
# spec.telemetry.loki.url on the Jumpstarter CR).
sources:
  jumpstarter_pods:
    type: kubernetes_logs
    extra_label_selector: "jumpstarter.dev/log-scrape=true"
    extra_field_selector: "metadata.namespace=%s"

transforms:
  jumpstarter_parse:
    type: remap
    inputs: [jumpstarter_pods]
    source: |
      parsed, err = parse_json(.message)
      if err == null {
        .component = parsed.component
        .exporter = parsed.exporter
      }

sinks:
  jumpstarter_loki:
    type: loki
    inputs: [jumpstarter_parse]
    endpoint: "%s"
    encoding:
      codec: text
    labels:
      component: "{{ component }}"
      exporter: "{{ exporter }}"
      namespace: "{{ kubernetes.namespace_name }}"
`

// renderPromtail renders the Promtail configuration.
func renderPromtail(js *operatorv1alpha1.Jumpstarter) string {
	return fmt.Sprintf(promtailTemplate, lokiURL(js), js.Namespace)
}

// renderAlloy renders the Grafana Alloy configuration.
func renderAlloy(js *operatorv1alpha1.Jumpstarter) string {
	return fmt.Sprintf(alloyTemplate, js.Namespace, lokiURL(js))
}

// renderVector renders the Vector configuration.
func renderVector(js *operatorv1alpha1.Jumpstarter) string {
	return fmt.Sprintf(vectorTemplate, js.Namespace, lokiURL(js))
}

// renderShipperReadme documents how to use the generated configuration.
func renderShipperReadme(js *operatorv1alpha1.Jumpstarter, shipper, key string) string {
	return fmt.Sprintf(`# Jumpstarter log shipper configuration

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

Vector cannot conditionally omit a templated label; when a log line has no
"exporter" field the Vector rendering emits exporter="" rather than omitting it.
Promtail and Alloy omit the label.

A cluster log shipper is required to see controller and router logs in Loki.
`, js.Name, js.Namespace, key, shipper)
}
