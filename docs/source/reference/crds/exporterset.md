# ExporterSet

`virtualtarget.jumpstarter.dev/v1alpha1`

ExporterSet is the Schema for the exportersets API.

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.images` | object | Images overrides the default container images used by the provisioner. |
| `spec.images.exporter` | object | Exporter overrides the exporter sidecar container image. |
| `spec.images.exporter.image` | string | Image is the container image reference (e.g. "quay.io/org/repo:tag"). |
| `spec.images.exporter.imagePullPolicy` | `Always` | `Never` | `IfNotPresent` | ImagePullPolicy defines the pull policy for the container image. |
| `spec.images.runtime` | object | Runtime overrides the provisioner-specific runtime container image |
| `spec.images.runtime.image` | string | Image is the container image reference (e.g. "quay.io/org/repo:tag"). |
| `spec.images.runtime.imagePullPolicy` | `Always` | `Never` | `IfNotPresent` | ImagePullPolicy defines the pull policy for the container image. |
| `spec.maxReplicas` | integer | MaxReplicas is the maximum number of instances (ceiling). (default: `4`) |
| `spec.minAvailableReplicas` | integer | MinAvailableReplicas is the warm buffer: ready and unleased instances. (default: `0`) |
| `spec.minReplicas` | integer | MinReplicas is the minimum number of instances (floor). (default: `0`) |
| `spec.parameters` | object | Parameters are optional overrides deep-merged over the class parameters. |
| `spec.recycleStrategy` | `ExitAndReplace` | `InPlaceReuse` | RecycleStrategy defines what happens when a lease is released. (default: `ExitAndReplace`) |
| `spec.scaleDownCooldown` | string | ScaleDownCooldown is how long to wait before scaling down excess replicas. (default: `5m`) |
| `spec.selector` | object | Selector defines the label selector for matching exporters owned by this set. |
| `spec.selector.matchExpressions` | array | matchExpressions is a list of label selector requirements. The requirements are ANDed. |
| `spec.selector.matchLabels` | object | matchLabels is a map of {key,value} pairs. A single {key,value} in the matchLabels |
| `spec.template` | object | Template defines the exporter template for instances created by this set. |
| `spec.template.metadata` | object | Metadata for created Exporter resources. |
| `spec.template.metadata.annotations` | object | Annotations to apply to created Exporter resources. |
| `spec.template.metadata.labels` | object | Labels to apply to created Exporter resources. |
| `spec.template.spec` | object | Spec defines the exporter configuration. |
| `spec.template.spec.drivers` | array | Drivers is the list of drivers to configure on each exporter instance. |
| `spec.virtualTargetClassName` | string | VirtualTargetClassName references a VirtualTargetClass in the same namespace. |

## Status

| Field | Type | Description |
| --- | --- | --- |
| `status.availableReplicas` | integer | AvailableReplicas is the number of ready, unleased, and enabled instances (warm pool). |
| `status.conditions` | array | Conditions represent the latest available observations of the ExporterSet state. |
| `status.conditions[].lastTransitionTime` | string | lastTransitionTime is the last time the condition transitioned from one status to another. |
| `status.conditions[].message` | string | message is a human readable message indicating details about the transition. |
| `status.conditions[].observedGeneration` | integer | observedGeneration represents the .metadata.generation that the condition was set based upon. |
| `status.conditions[].reason` | string | reason contains a programmatic identifier indicating the reason for the condition's last transition. |
| `status.conditions[].status` | `True` | `False` | `Unknown` | status of the condition, one of True, False, Unknown. |
| `status.conditions[].type` | string | type of condition in CamelCase or in foo.example.com/CamelCase. |
| `status.exportersActive` | integer | ExportersActive is the number of exporters currently serving a lease. |
| `status.exportersDisabled` | integer | ExportersDisabled is the number of exporters with spec.enabled=false. |
| `status.exportersIdle` | integer | ExportersIdle is the number of exporters that are online, enabled, and not leased. |
| `status.exportersOffline` | integer | ExportersOffline is the number of enabled exporters that are not online. |
| `status.leasedReplicas` | integer | LeasedReplicas is the number of instances currently leased. |
| `status.podsFailed` | integer | PodsFailed is the number of owned pods in Failed phase. |
| `status.podsPending` | integer | PodsPending is the number of owned pods in Pending phase. |
| `status.podsRunning` | integer | PodsRunning is the number of owned pods in Running phase. |
| `status.podsUnknown` | integer | PodsUnknown is the number of owned pods in Unknown or Succeeded phase. |
| `status.readyReplicas` | integer | ReadyReplicas is the number of instances that are online and registered. |
| `status.replicas` | integer | Replicas is the total number of exporter instances owned by this set. |
| `status.selector` | string | Selector is the serialized label selector for HPA compatibility. |
| `status.unavailableReplicas` | integer | UnavailableReplicas is the number of enabled instances that are not yet ready. |
