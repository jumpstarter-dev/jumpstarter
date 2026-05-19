# ExporterAccessPolicy

`jumpstarter.dev/v1alpha1`

ExporterAccessPolicy is the Schema for the exporteraccesspolicies API.

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.exporterSelector` | object | A label selector is a label query over a set of resources. The result of matchLabels and |
| `spec.exporterSelector.matchExpressions` | array | matchExpressions is a list of label selector requirements. The requirements are ANDed. |
| `spec.exporterSelector.matchLabels` | object | matchLabels is a map of {key,value} pairs. A single {key,value} in the matchLabels |
| `spec.policies` | array |  |
| `spec.policies[].from` | array |  |
| `spec.policies[].from[].clientSelector` | object | A label selector is a label query over a set of resources. The result of matchLabels and |
| `spec.policies[].maximumDuration` | string |  |
| `spec.policies[].priority` | integer |  |
| `spec.policies[].spotAccess` | boolean |  |
