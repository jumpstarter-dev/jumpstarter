# Operator API

The Jumpstarter {term}`operator` is configured through the `Jumpstarter` custom
resource (`operator.jumpstarter.dev/v1alpha1`).

The schema below is auto-generated from the CRD definition.

## Spec

```{jsonschema} jumpstarter-crd-spec.json
```

## Status Conditions

| Condition | Meaning |
| --- | --- |
| `Ready` | Overall deployment readiness. |
| `ControllerDeploymentReady` | Controller deployment is available. |
| `RouterDeploymentsReady` | All {term}`router` deployments are available. |
| `CertManagerAvailable` | cert-manager CRDs are present (when enabled). |
| `IssuerReady` | Configured issuer is ready (when enabled). |
| `ControllerCertificateReady` | Controller TLS secret is ready (when enabled). |
| `RouterCertificatesReady` | Router TLS secrets are ready for all replicas (when enabled). |
