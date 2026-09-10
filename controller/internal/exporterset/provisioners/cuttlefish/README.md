# Cuttlefish ExporterSets

Each exporter owns one CVD. The managed backend uses Host Orchestrator over
HTTP inside the Pod, crosvm with private userspace VSOCK, and netsim Bluetooth.
The standalone Python driver continues to support externally managed HTTP hosts.
An exec backend is a separate follow-up.

## Workload admission and networking

Create a dedicated service account in the ExporterSet namespace and set
`parameters.service_account_name` to its name. `default` is rejected. The Pod
does not mount a Kubernetes API token. `runtime_privileged: true` is required;
`false` is rejected until device permissions and capabilities are supported.

On Kubernetes, create the namespace and workload account. If Pod Security
Admission is enabled, the namespace must permit privileged workloads; the
`baseline` and `restricted` profiles reject this Pod. For a dedicated test
namespace:

```sh
kubectl create namespace cuttlefish-lab
kubectl label namespace cuttlefish-lab pod-security.kubernetes.io/enforce=privileged
kubectl -n cuttlefish-lab create serviceaccount cuttlefish-runtime
```

This namespace setting permits privileged workloads for every account in that
namespace, so restrict who can create workloads there. Other admission policies
must also allow the Pod's privileged containers, hostPath devices and container
UIDs. The workload account needs no Kubernetes API permissions. Nodes must expose
the required KVM and networking devices; VM-based nodes need nested virtualization.
The cluster must support native sidecar containers: the runtime init containers
use `restartPolicy: Always`.
See [Kubernetes Pod Security Admission](https://kubernetes.io/docs/concepts/security/pod-security-admission/).

On OpenShift, a cluster administrator must grant that workload account access
to an SCC permitting privileged containers, hostPath devices, and the UIDs used
by **all** containers, including image copy/fetch and permission init containers.
For initial testing, a scoped grant to the built-in privileged SCC is:

```sh
oc -n cuttlefish-lab create serviceaccount cuttlefish-runtime
oc adm policy add-scc-to-user privileged -z cuttlefish-runtime -n cuttlefish-lab
```

This grant is for the workload account, not the ExporterSet controller. Merely
setting `runtime_privileged` cannot grant SCC admission. Check the admitted Pod's
`openshift.io/scc` annotation, completion of init containers, and an actual guest
boot. A server-side dry run only establishes admission, not device access.
See [OpenShift SCC documentation](https://docs.redhat.com/en/documentation/openshift_container_platform/4.19/html/authentication_and_authorization/managing-pod-security-policies).

The controller creates and reconciles an ExporterSet-owned NetworkPolicy before
creating workloads. It denies Pod ingress, including Host Orchestrator, nginx,
ADB and simulator listeners, while leaving outbound exporter connections and
same-Pod loopback traffic available. Deploy with a CNI that enforces NetworkPolicy.
Other policies must not grant ingress to these Pods: Kubernetes allow rules are
additive. Verify denial from a second Pod, using the actual runtime image;
loopback addresses in driver configuration do not change the server's listeners.
NetworkPolicy does not isolate privileged containers from their node.

Controllers deployed outside the operator also need `get,list,watch,create,update,patch`
on `networking.k8s.io/networkpolicies`. The operator supplies these permissions.
Existing Pods must be drained and replaced to receive the isolation label,
service account, runtime marker and managed driver configuration.

## Configuration and resource allocation

The provisioner requires exactly one Cuttlefish driver and one
`env_config.instances` entry. It pins the managed endpoint to
`http://127.0.0.1:2081` and `instance_num` to 1; a template that sets different
values is rejected. The upstream image fixes Host Orchestrator to 2081 behind
nginx on 2080, and all containers share the Pod network namespace, so the netsim
and bt_peer drivers are pointed directly at the simulators on loopback ports
7681 and 7300.

Guest defaults are 4 CPUs and 8192 MiB. Runtime memory requests default to the
**effective** guest memory plus `runtime_memory_overhead_mb` (2048 MiB by default).
Explicit requests and limits must cover that budget. Increase the overhead for
larger simulator workloads. Guest values in driver `env_config` take precedence
over `vm_cpus` and `vm_memory_mb` when calculating the budget. CPU requests default
to guest CPUs, or to an explicit CPU limit; CPU overcommit remains configurable.

`create_cvd()` in managed mode accepts only the exact configured `env_config`,
checks the entire Host Orchestrator inventory, and serializes creation with other
lifecycle operations. Destroy the existing CVD before creating another. This
prevents alternate API payloads or concurrent calls from exceeding the configured
instance count and memory budget. Arbitrary template code and driver imports are
administrator-controlled; they are not a security boundary against a malicious
cluster administrator.

## VSOCK and Bluetooth compatibility

Managed configuration sets:

```yaml
env_config:
  netsim_bt: true
  instances:
    - vm:
        crosvm:
          vhost_user_vsock: "true"
```

The string value is required by the upstream configuration schema. The runtime
and guest must support that backend. The provisioner does not mount
`/dev/vhost-vsock`; each Pod has private runtime files and Unix sockets. A
privileged runtime still has broad node access, so this is not containment of a
compromised runtime. QEMU, gem5, disabled userspace VSOCK, and standalone RootCanal
(`netsim_bt: false`) are rejected for this managed backend. The referenced
upstream standalone RootCanal proxy does not propagate the userspace VSOCK flag.

Before approving a runtime/build pair, boot two Pods on the same node using the
default identical guest CID, verify their generated configuration and private
`vhost.socket`/`vm.vsock` paths, and exercise netsim plus Bluetooth peer traffic
in both leases. Stop one guest and verify the other remains usable. Source/unit
tests cannot establish compatibility of a mutable image tag or guest build.

## Image PVCs and reproducibility

A prewarmed image PVC is mounted read-only, then copied into a private writable
`emptyDir`. It remains a Pod volume after the init container exits. Read-only
mounting does **not** change the PVC's access mode or remove attachment constraints.

For a pool spanning nodes, use storage supporting ReadOnlyMany or ReadWriteMany
with the required topology. For ReadWriteOnce, keep readers on one compatible
node; ReadWriteOncePod permits only one Pod. Alternatively fetch images per Pod.
See [Kubernetes access modes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes).

Use an immutable runtime manifest digest and Android build ID for
repeatable replacements. This example is a template: substitute verified values
from a tested pair; the placeholders are not a published compatibility claim.
Pin the exporter image built with this provisioner/driver change too.

```yaml
apiVersion: virtualtarget.jumpstarter.dev/v1alpha1
kind: VirtualTargetClass
metadata:
  name: cuttlefish
  namespace: cuttlefish-lab
spec:
  provisioner: cuttlefish.jumpstarter.dev
  parameters:
    service_account_name: cuttlefish-runtime
    runtime_privileged: true
    fetch_images: true
    default_build: "<android-build-id>/aosp_cf_x86_64_auto-userdebug"
    vm_cpus: 4
    vm_memory_mb: 8192
    runtime_memory_overhead_mb: 2048
  images:
    runtime:
      image: "us-docker.pkg.dev/android-cuttlefish-artifacts/cuttlefish-orchestration/cuttlefish-orchestration@sha256:<runtime-manifest-digest>"
    exporter:
      image: "quay.io/jumpstarter-dev/jumpstarter@sha256:<exporter-manifest-digest>"
  scheduling:
    resources:
      requests:
        cpu: "4"
        memory: 10Gi
      limits:
        memory: 10Gi
```

Record both the resolved runtime image digest and the guest `fetcher_config.json`
with validation results. A prewarmed PVC needs the same build provenance.

## WebRTC display over the lease

Set `parameters.webrtc_turn: true` to make the CVD's screen viewable by whoever
holds the lease. It is off by default: it adds a container, and exporters driven
over adb alone do not need it.

The isolation policy denies all ingress to the runtime Pod, and WebRTC media is
UDP addressed to the Pod's own interfaces - the streamer offers candidates like
`192.168.190.172:15550`. Opening ingress for those ports would defeat the policy
and still only serve viewers that can route to Pod IPs. A relay the Pod can reach
locally solves the addressing instead, so the provisioner adds:

- **`cuttlefish-turn`**, a coturn sidecar on `127.0.0.1:3478/tcp` whose relay
  range is loopback as well. The browser allocates a relay through it over the
  forwarded TCP connection, the streamer sends media to that relay inside its own
  network namespace, and coturn passes it back.
- **a second nginx vhost**, written to `sites-enabled` before the image's services
  start. It proxies Host Orchestrator like the stock 2080 vhost, but overrides
  `/infra_config` - whose ICE server list is otherwise a public STUN server
  compiled into the operator binary - and upgrades the signalling WebSocket that
  the stock vhost answers with 400.

Both listeners are loopback-only, so the lease remains the only way in, and both
join `health_ports` so a dead relay or vhost fails the probe. On the client side
this is `j cuttlefish webrtc --forward`.

| Parameter | Description | Default |
| --- | --- | --- |
| `webrtc_turn` | Enable the in-Pod TURN relay and display vhost | `false` |
| `turn_image` | coturn image | `docker.io/coturn/coturn:4.7.0` |
| `turn_port` | coturn TCP listener, and the local port the client binds | `3478` |
| `webui_port` | nginx vhost serving the TURN-aware client page | `2090` |
| `turn_secret` | Long-term credential for the relay | `cuttlefish` |

`turn_secret` is not an access boundary: the relay is unreachable except through
the lease, like Host Orchestrator and netsim, which have no authentication at
all. Media crosses the lease over TCP (the relay-to-streamer leg is UDP on the
Pod's loopback), so a lossy link degrades into stutter rather than WebRTC's
usual frame dropping.

## Failure and recovery behavior

The exporter liveness probe reads state written atomically by the managed driver.
It always checks Host Orchestrator availability and a per-start runtime ID.
When the guest is expected to run, it also checks the CVD inventory/status and
simulator TCP listeners in the shared network namespace. It inspects
listeners instead of opening HCI connections that could disturb Bluetooth peers.
A listening socket alone does not prove simulator protocol correctness.

Intentional stop, destroy and reset leave the Pod healthy without a guest.
Create/start/restart/powerwash receive bounded transition time; failed or expired
operations fail health checks. A runtime sidecar restart invalidates the exporter
even if its HTTP API comes back: it must not silently resume a lease after losing
runtime state. Six failed checks, ten seconds apart, terminate the exporter.

With `ExitAndReplace`, the failed exporter remains associated with an active
lease. Release that lease to let the controller delete and replace the Pod;
reacquire a lease for a fresh device. Automatic replacement during an active
lease is not attempted. Use `ExitAndReplace` for managed Cuttlefish pools.

Validate recovery on disposable leased Pods by terminating the VMM, netsim, and
then the runtime sidecar in separate trials. A component may recover
within the probe failure window; verify guest and simulator functionality after
recovery. For unrecovered failures and runtime sidecar restarts, confirm liveness
failure, client disconnect, retention while leased, and replacement after release. Also
exercise intentional power off/on and destroy/create, which must remain healthy.
Actual CNI enforcement and colocated VSOCK/BT trials are required integration
checks beyond the local regression tests. OpenShift deployments additionally
require SCC admission and guest boot validation.
