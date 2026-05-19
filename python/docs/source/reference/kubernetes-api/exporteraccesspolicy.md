# ExporterAccessPolicy

`jumpstarter.dev/v1alpha1`

ExporterAccessPolicy is the Schema for the exporteraccesspolicies API.

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.exporterSelector` | object | A label selector is a label query over a set of resources. The result of matchLabels and |
| `spec.exporterSelector.matchExpressions` | array | matchExpressions is a list of label selector requirements. The requirements are ANDed. |
| `spec.exporterSelector.matchExpressions[].key` | string | key is the label key that the selector applies to. |
| `spec.exporterSelector.matchExpressions[].operator` | string | operator represents a key's relationship to a set of values. |
| `spec.exporterSelector.matchExpressions[].values` | array | values is an array of string values. If the operator is In or NotIn, |
| `spec.exporterSelector.matchLabels` | object | matchLabels is a map of {key,value} pairs. A single {key,value} in the matchLabels |
| `spec.policies` | array |  |
| `spec.policies[].from` | array |  |
| `spec.policies[].from[].clientSelector` | object | A label selector is a label query over a set of resources. The result of matchLabels and |
| `spec.policies[].maximumDuration` | string |  |
| `spec.policies[].priority` | integer |  |
| `spec.policies[].spotAccess` | boolean |  |
