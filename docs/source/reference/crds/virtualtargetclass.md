# VirtualTargetClass

`virtualtarget.jumpstarter.dev/v1alpha1`

VirtualTargetClass is the Schema for the virtualtargetclasses API.

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.bindingMode` | `Immediate` | `WaitForFirstConsumer` | BindingMode controls when instances are provisioned. (default: `Immediate`) |
| `spec.caBundleConfigMapRef` | object | CABundleConfigMapRef references a ConfigMap containing CA certificates |
| `spec.caBundleConfigMapRef.key` | string | Key within the ConfigMap. Defaults to "ca-bundle.crt". (default: `ca-bundle.crt`) |
| `spec.caBundleConfigMapRef.name` | string | Name of the ConfigMap. |
| `spec.credentialsSecretRef` | object | CredentialsSecretRef is a reference to a Secret in the same namespace |
| `spec.credentialsSecretRef.name` | string | Name of the referent. (default: ``) |
| `spec.images` | object | Images overrides the default container images used by the provisioner. |
| `spec.images.exporter` | object | Exporter overrides the exporter sidecar container image. |
| `spec.images.exporter.image` | string | Image is the container image reference (e.g. "quay.io/org/repo:tag"). |
| `spec.images.exporter.imagePullPolicy` | `Always` | `Never` | `IfNotPresent` | ImagePullPolicy defines the pull policy for the container image. |
| `spec.images.runtime` | object | Runtime overrides the provisioner-specific runtime container image |
| `spec.images.runtime.image` | string | Image is the container image reference (e.g. "quay.io/org/repo:tag"). |
| `spec.images.runtime.imagePullPolicy` | `Always` | `Never` | `IfNotPresent` | ImagePullPolicy defines the pull policy for the container image. |
| `spec.parameters` | object | Parameters holds provisioner-specific configuration as a nested object. |
| `spec.provisioner` | string | Provisioner identifies which exporter-set controller handles this class. |
| `spec.reclaimPolicy` | `Delete` | `Retain` | ReclaimPolicy controls what happens to the virtual target after lease release. (default: `Delete`) |
| `spec.scheduling` | object | Scheduling defines node placement constraints inherited by rendered Pods. |
| `spec.scheduling.nodeSelector` | object | NodeSelector is a map of key-value pairs for node selection. |
| `spec.scheduling.resources` | object | Resources defines resource requirements for the rendered Pods. |
| `spec.scheduling.tolerations` | array | Tolerations are tolerations for the rendered Pods. |
| `spec.scheduling.tolerations[].effect` | string | Effect indicates the taint effect to match. Empty means match all taint effects. |
| `spec.scheduling.tolerations[].key` | string | Key is the taint key that the toleration applies to. Empty means match all taint keys. |
| `spec.scheduling.tolerations[].operator` | string | Operator represents a key's relationship to the value. |
| `spec.scheduling.tolerations[].tolerationSeconds` | integer | TolerationSeconds represents the period of time the toleration (which must be |
| `spec.scheduling.tolerations[].value` | string | Value is the taint value the toleration matches to. |
