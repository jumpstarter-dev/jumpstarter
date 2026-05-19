# Lease

`jumpstarter.dev/v1alpha1`

Lease is the Schema for the exporters API

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.beginTime` | string | Requested start time. If omitted, lease starts when exporter is acquired. |
| `spec.clientRef` | object | The client that is requesting the lease |
| `spec.clientRef.name` | string (default: ``) | Name of the referent. |
| `spec.duration` | string | Duration of the lease. Must be positive when provided. |
| `spec.endTime` | string | Requested end time. If specified with BeginTime, Duration is calculated. |
| `spec.exporterRef` | object | Optionally pin this lease to a specific exporter name. |
| `spec.exporterRef.name` | string (default: ``) | Name of the referent. |
| `spec.release` | boolean | The release flag requests the controller to end the lease now |
| `spec.selector` | object (default: `{}`) | The selector for the exporter to be used |
| `spec.selector.matchExpressions` | array | matchExpressions is a list of label selector requirements. The requirements are ANDed. |
| `spec.selector.matchLabels` | object | matchLabels is a map of {key,value} pairs. A single {key,value} in the matchLabels |
| `spec.tags` | object | User-defined tags for the lease. Immutable after creation. |

## Status

| Field | Type | Description |
| --- | --- | --- |
| `status.beginTime` | string | If the lease has been acquired an exporter name is assigned |
| `status.conditions` | array |  |
| `status.conditions[].lastTransitionTime` | string | lastTransitionTime is the last time the condition transitioned from one status to another. |
| `status.conditions[].message` | string | message is a human readable message indicating details about the transition. |
| `status.conditions[].observedGeneration` | integer | observedGeneration represents the .metadata.generation that the condition was set based upon. |
| `status.conditions[].reason` | string | reason contains a programmatic identifier indicating the reason for the condition's last transition. |
| `status.conditions[].status` | `True` | `False` | `Unknown` | status of the condition, one of True, False, Unknown. |
| `status.conditions[].type` | string | type of condition in CamelCase or in foo.example.com/CamelCase. |
| `status.endTime` | string |  |
| `status.ended` | boolean |  |
| `status.exporterRef` | object | LocalObjectReference contains enough information to let you locate the |
| `status.exporterRef.name` | string (default: ``) | Name of the referent. |
| `status.priority` | integer |  |
| `status.spotAccess` | boolean |  |
