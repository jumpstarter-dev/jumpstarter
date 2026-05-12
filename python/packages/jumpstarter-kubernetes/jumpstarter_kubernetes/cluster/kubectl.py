"""Kubectl operations for cluster management."""

import json
from typing import List, Literal, Optional, TypedDict, Union

from ..clusters import V1Alpha1ClusterInfo, V1Alpha1ClusterList, V1Alpha1JumpstarterInstance
from ..exceptions import JumpstarterKubernetesError
from .common import run_command


class KubectlContext(TypedDict):
    name: str
    cluster: str
    server: str
    user: str
    namespace: str
    current: bool


async def check_kubernetes_access(context: Optional[str] = None, kubectl: str = "kubectl") -> bool:
    """Check if Kubernetes cluster is accessible."""
    try:
        cmd = [kubectl]
        if context:
            cmd.extend(["--context", context])
        cmd.extend(["cluster-info", "--request-timeout=5s"])

        returncode, _, _ = await run_command(cmd)
        return returncode == 0
    except RuntimeError:
        return False


async def get_kubectl_contexts(kubectl: str = "kubectl") -> List[KubectlContext]:
    """Get all kubectl contexts."""
    contexts = []

    try:
        cmd = [kubectl, "config", "view", "-o", "json"]
        returncode, stdout, stderr = await run_command(cmd)

        if returncode != 0:
            from ..exceptions import KubeconfigError
            raise KubeconfigError(f"Failed to get kubectl config: {stderr}")

        config = json.loads(stdout)

        current_context = config.get("current-context", "")
        context_list = config.get("contexts", [])

        for ctx in context_list:
            context_name = ctx.get("name", "")
            cluster_name = ctx.get("context", {}).get("cluster", "")
            user_name = ctx.get("context", {}).get("user", "")
            namespace = ctx.get("context", {}).get("namespace") or "default"

            # Get cluster server URL
            server_url = ""
            for cluster in config.get("clusters", []):
                if cluster.get("name") == cluster_name:
                    server_url = cluster.get("cluster", {}).get("server", "")
                    break

            contexts.append(
                {
                    "name": context_name,
                    "cluster": cluster_name,
                    "server": server_url,
                    "user": user_name,
                    "namespace": namespace,
                    "current": context_name == current_context,
                }
            )

        return contexts

    except json.JSONDecodeError as e:
        from ..exceptions import KubeconfigError
        raise KubeconfigError(f"Failed to parse kubectl config: {e}") from e
    except (RuntimeError, JumpstarterKubernetesError) as e:
        from ..exceptions import KubeconfigError
        raise KubeconfigError(f"Error listing kubectl contexts: {e}") from e


class CrInstanceSuccess(TypedDict):
    installed: Literal[True]
    namespace: str
    status: str


class CrInstanceError(TypedDict):
    installed: Literal[False]
    error: str


class CrInstanceNotFound(TypedDict):
    installed: Literal[False]


CrInstanceResult = Union[CrInstanceSuccess, CrInstanceError, CrInstanceNotFound]


async def _check_cr_instances(
    kubectl: str, context: str, namespace: Optional[str]
) -> CrInstanceResult:
    """Query for Jumpstarter CR instances to confirm full installation."""
    cr_resource = "jumpstarters.operator.jumpstarter.dev"
    try:
        cr_cmd = [kubectl, "--context", context, "get", cr_resource, "-A", "-o", "json"]
        cr_returncode, cr_stdout, cr_stderr = await run_command(cr_cmd)
        if cr_returncode == 0:
            cr_data = json.loads(cr_stdout)
            if cr_data.get("items"):
                cr_namespace = cr_data["items"][0].get("metadata", {}).get("namespace")
                return CrInstanceSuccess(
                    installed=True,
                    namespace=cr_namespace or namespace or "unknown",
                    status="installed",
                )
            else:
                return CrInstanceNotFound(installed=False)
        else:
            return CrInstanceError(
                installed=False,
                error=f"CR instance check failed (exit {cr_returncode}): {cr_stderr or cr_stdout}",
            )
    except (json.JSONDecodeError, RuntimeError) as e:
        return CrInstanceError(installed=False, error=f"CR instance check failed: {e}")


def _parse_json_with_prefix(stdout: str) -> dict:
    json_start = stdout.find("{")
    if json_start >= 0:
        return json.loads(stdout[json_start:])
    return json.loads(stdout)


def _apply_cr_result(result_data: dict, cr_result: CrInstanceResult) -> None:
    if cr_result["installed"] is True:
        result_data["installed"] = True
        result_data["namespace"] = cr_result["namespace"]
        result_data["status"] = cr_result["status"]
    elif "error" in cr_result:
        result_data["error"] = cr_result["error"]


async def check_jumpstarter_installation(
    context: str, namespace: Optional[str] = None, kubectl: str = "kubectl"
) -> V1Alpha1JumpstarterInstance:
    """Check if Jumpstarter is installed in the cluster using CRD detection."""
    result_data = {
        "installed": False,
        "version": None,
        "namespace": None,
        "status": None,
        "has_crds": False,
        "error": None,
        "basedomain": None,
        "controller_endpoint": None,
        "router_endpoint": None,
    }

    try:
        crd_cmd = [kubectl, "--context", context, "get", "crd", "-o", "json"]
        returncode, stdout, stderr = await run_command(crd_cmd)

        if returncode != 0:
            result_data["error"] = f"Command failed: {stderr or stdout}"
            return V1Alpha1JumpstarterInstance(**result_data)

        crds = _parse_json_with_prefix(stdout)
        jumpstarter_crds = [
            item.get("metadata", {}).get("name", "")
            for item in crds.get("items", [])
            if "jumpstarter.dev" in item.get("metadata", {}).get("name", "")
        ]

        if jumpstarter_crds:
            result_data["has_crds"] = True

            if "jumpstarters.operator.jumpstarter.dev" in jumpstarter_crds:
                cr_result = await _check_cr_instances(kubectl, context, namespace)
                _apply_cr_result(result_data, cr_result)

    except json.JSONDecodeError as e:
        result_data["error"] = f"Failed to parse output: {e}"
    except RuntimeError as e:
        result_data["error"] = f"Command failed: {e}"

    return V1Alpha1JumpstarterInstance(**result_data)


async def get_cluster_info(
    context: str,
    kubectl: str = "kubectl",
    minikube: str = "minikube",
) -> V1Alpha1ClusterInfo:
    """Get comprehensive cluster information."""
    try:
        contexts = await get_kubectl_contexts(kubectl)
        context_info = None

        for ctx in contexts:
            if ctx["name"] == context:
                context_info = ctx
                break

        if not context_info:
            return V1Alpha1ClusterInfo(
                name=context,
                cluster="unknown",
                server="unknown",
                user="unknown",
                namespace="unknown",
                is_current=False,
                type="remote",
                accessible=False,
                jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
                error=f"Context '{context}' not found",
            )

        # Detect cluster type
        from .detection import detect_cluster_type

        cluster_type = await detect_cluster_type(context_info["name"], context_info["server"], minikube)

        # Check if cluster is accessible
        try:
            version_cmd = [kubectl, "--context", context, "version", "-o", "json"]
            returncode, stdout, _ = await run_command(version_cmd)
            cluster_accessible = returncode == 0
            cluster_version = None

            if cluster_accessible:
                try:
                    version_info = json.loads(stdout)
                    cluster_version = version_info.get("serverVersion", {}).get("gitVersion", "unknown")
                except (json.JSONDecodeError, KeyError):
                    cluster_version = "unknown"
        except RuntimeError:
            cluster_accessible = False
            cluster_version = None

        # Check Jumpstarter installation
        if cluster_accessible:
            jumpstarter_info = await check_jumpstarter_installation(context, None, kubectl)
        else:
            jumpstarter_info = V1Alpha1JumpstarterInstance(installed=False, error="Cluster not accessible")

        return V1Alpha1ClusterInfo(
            name=context_info["name"],
            cluster=context_info["cluster"],
            server=context_info["server"],
            user=context_info["user"],
            namespace=context_info["namespace"],
            is_current=context_info["current"],
            type=cluster_type,
            accessible=cluster_accessible,
            version=cluster_version,
            jumpstarter=jumpstarter_info,
        )

    except (RuntimeError, JumpstarterKubernetesError) as e:
        return V1Alpha1ClusterInfo(
            name=context,
            cluster="unknown",
            server="unknown",
            user="unknown",
            namespace="unknown",
            is_current=False,
            type="remote",
            accessible=False,
            jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            error=f"Failed to get cluster info: {e}",
        )


async def list_clusters(
    cluster_type_filter: str = "all",
    kubectl: str = "kubectl",
    minikube: str = "minikube",
) -> V1Alpha1ClusterList:
    """List all Kubernetes clusters with Jumpstarter status."""
    try:
        contexts = await get_kubectl_contexts(kubectl)
        cluster_infos = []

        for context in contexts:
            cluster_info = await get_cluster_info(context["name"], kubectl, minikube)

            # Filter by type if specified
            if cluster_type_filter != "all" and cluster_info.type != cluster_type_filter:
                continue

            cluster_infos.append(cluster_info)

        return V1Alpha1ClusterList(items=cluster_infos)

    except (RuntimeError, JumpstarterKubernetesError) as e:
        error_cluster = V1Alpha1ClusterInfo(
            name="error",
            cluster="error",
            server="error",
            user="error",
            namespace="error",
            is_current=False,
            type="remote",
            accessible=False,
            jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            error=f"Failed to list clusters: {e}",
        )
        return V1Alpha1ClusterList(items=[error_cluster])
