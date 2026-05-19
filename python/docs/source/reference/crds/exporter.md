# Exporter

`jumpstarter.dev/v1alpha1`

Exporter is the Schema for the exporters API

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.username` | string |  |

## Status

| Field | Type | Description |
| --- | --- | --- |
| `status.conditions` | array | Exporter status fields |
| `status.conditions[].lastTransitionTime` | string | lastTransitionTime is the last time the condition transitioned from one status to another. |
| `status.conditions[].message` | string | message is a human readable message indicating details about the transition. |
| `status.conditions[].observedGeneration` | integer | observedGeneration represents the .metadata.generation that the condition was set based upon. |
| `status.conditions[].reason` | string | reason contains a programmatic identifier indicating the reason for the condition's last transition. |
| `status.conditions[].status` | `True` | `False` | `Unknown` | status of the condition, one of True, False, Unknown. |
| `status.conditions[].type` | string | type of condition in CamelCase or in foo.example.com/CamelCase. |
| `status.credential` | object | LocalObjectReference contains enough information to let you locate the |
| `status.credential.name` | string | Name of the referent. (default: ``) |
| `status.devices` | array |  |
| `status.devices[].labels` | object |  |
| `status.devices[].parent_uuid` | string |  |
| `status.devices[].uuid` | string |  |
| `status.endpoint` | string |  |
| `status.exporterStatus` | `Unspecified` | `Offline` | `Available` | `BeforeLeaseHook` | `LeaseReady` | `AfterLeaseHook` | `BeforeLeaseHookFailed` | `AfterLeaseHookFailed` | ExporterStatusValue is the current operational status reported by the exporter |
| `status.lastSeen` | string |  |
| `status.leaseRef` | object | LocalObjectReference contains enough information to let you locate the |
| `status.leaseRef.name` | string | Name of the referent. (default: ``) |
| `status.statusMessage` | string | StatusMessage is an optional human-readable message describing the current state |
