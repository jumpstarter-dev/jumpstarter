# Client

`jumpstarter.dev/v1alpha1`

Client is the Schema for the identities API

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.username` | string |  |

## Status

| Field | Type | Description |
| --- | --- | --- |
| `status.credential` | object | Status field for the clients |
| `status.credential.name` | string | Name of the referent. (default: ``) |
| `status.endpoint` | string |  |
