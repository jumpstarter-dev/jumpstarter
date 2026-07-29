# VirtualTargetClass

`virtualtarget.jumpstarter.dev/v1alpha1`

VirtualTargetClass is the Schema for the virtualtargetclasses API.

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.bindingMode` | `Immediate` \| `WaitForFirstConsumer` | BindingMode controls when instances are provisioned. Immediate: maintain a warm pool (default). WaitForFirstConsumer: provision on lease request. (default: `Immediate`) |
| `spec.caBundleConfigMapRef` | object | CABundleConfigMapRef references a ConfigMap containing CA certificates to inject into rendered Pods for corporate/private TLS verification. |
| `spec.caBundleConfigMapRef.key` | string | Key within the ConfigMap. Defaults to "ca-bundle.crt". (default: `ca-bundle.crt`) |
| `spec.caBundleConfigMapRef.name` | string | Name of the ConfigMap. |
| `spec.credentialsSecretRef` | object | CredentialsSecretRef is a reference to a Secret in the same namespace containing credentials for API-backed provisioners. |
| `spec.credentialsSecretRef.name` | string | Name of the referent. This field is effectively required, but due to backwards compatibility is allowed to be empty. Instances of this type with an empty value here are almost certainly wrong. More info: https://kubernetes.io/docs/concepts/overview/working-with-objects/names/#names (default: ``) |
| `spec.images` | object | Images overrides the default container images used by the provisioner. ExporterSet-level images take precedence over these class-level defaults. |
| `spec.images.exporter` | object | Exporter overrides the exporter sidecar container image. |
| `spec.images.exporter.image` | string | Image is the container image reference (e.g. "quay.io/org/repo:tag"). |
| `spec.images.exporter.imagePullPolicy` | `Always` \| `Never` \| `IfNotPresent` | ImagePullPolicy defines the pull policy for the container image. |
| `spec.images.runtime` | object | Runtime overrides the provisioner-specific runtime container image (e.g. QEMU runtime for the qemu.jumpstarter.dev provisioner). |
| `spec.images.runtime.image` | string | Image is the container image reference (e.g. "quay.io/org/repo:tag"). |
| `spec.images.runtime.imagePullPolicy` | `Always` \| `Never` \| `IfNotPresent` | ImagePullPolicy defines the pull policy for the container image. |
| `spec.parameters` | object | Parameters holds provisioner-specific configuration as a nested object. The active provisioner validates merged parameters during reconcile. |
| `spec.provisioner` | string | Provisioner identifies which exporter-set controller handles this class. Example: "qemu.jumpstarter.dev", "corellium.jumpstarter.dev" |
| `spec.reclaimPolicy` | `Delete` \| `Retain` | ReclaimPolicy controls what happens to the virtual target after lease release. Delete: target is destroyed (default). Retain: target is preserved for debugging. (default: `Delete`) |
| `spec.scheduling` | object | Scheduling defines node placement constraints inherited by rendered Pods. |
| `spec.scheduling.nodeSelector` | object | NodeSelector is a map of key-value pairs for node selection. |
| `spec.scheduling.resources` | object | Resources defines resource requirements for the rendered Pods. |
| `spec.scheduling.tolerations` | array | Tolerations are tolerations for the rendered Pods. |
| `spec.scheduling.tolerations[].effect` | string | Effect indicates the taint effect to match. Empty means match all taint effects. When specified, allowed values are NoSchedule, PreferNoSchedule and NoExecute. |
| `spec.scheduling.tolerations[].key` | string | Key is the taint key that the toleration applies to. Empty means match all taint keys. If the key is empty, operator must be Exists; this combination means to match all values and all keys. |
| `spec.scheduling.tolerations[].operator` | string | Operator represents a key's relationship to the value. Valid operators are Exists and Equal. Defaults to Equal. Exists is equivalent to wildcard for value, so that a pod can tolerate all taints of a particular category. |
| `spec.scheduling.tolerations[].tolerationSeconds` | integer | TolerationSeconds represents the period of time the toleration (which must be of effect NoExecute, otherwise this field is ignored) tolerates the taint. By default, it is not set, which means tolerate the taint forever (do not evict). Zero and negative values will be treated as 0 (evict immediately) by the system. |
| `spec.scheduling.tolerations[].value` | string | Value is the taint value the toleration matches to. If the operator is Exists, the value should be empty, otherwise just a regular string. |
