"""Kubectl operations for cluster management."""

import json
from typing import Dict, List, Optional

from ..clusters import V1Alpha1ClusterInfo, V1Alpha1ClusterList, V1Alpha1JumpstarterInstance
from .common import run_command


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


async def get_kubectl_contexts(kubectl: str = "kubectl") -> List[Dict[str, str]]:
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
    except Exception as e:
        from ..exceptions import KubeconfigError
        raise KubeconfigError(f"Error listing kubectl contexts: {e}") from e


async def check_jumpstarter_installation(  # noqa: C901
    context: str, namespace: Optional[str] = None, helm: str = "helm", kubectl: str = "kubectl"
) -> V1Alpha1JumpstarterInstance:
    """Check if Jumpstarter is installed in the cluster."""
    result_data = {
        "installed": False,
        "version": None,
        "namespace": None,
        "chart_name": None,
        "status": None,
        "has_crds": False,
        "error": None,
        "basedomain": None,
        "controller_endpoint": None,
        "router_endpoint": None,
    }

    try:
        # Check for Helm installation first
        helm_cmd = [helm, "list", "--all-namespaces", "-o", "json", "--kube-context", context]
        returncode, stdout, _ = await run_command(helm_cmd)

        if returncode == 0:
            # Extract JSON from output (handle case where warnings are printed before JSON)
            json_start = stdout.find("[")
            if json_start >= 0:
                json_output = stdout[json_start:]
                releases = json.loads(json_output)
            else:
                releases = json.loads(stdout)  # Fallback to original parsing
            for release in releases:
                # Look for Jumpstarter chart
                if "jumpstarter" in release.get("chart", "").lower():
                    result_data["installed"] = True
                    result_data["version"] = release.get("app_version") or release.get("chart", "").split("-")[-1]
                    result_data["namespace"] = release.get("namespace")
                    result_data["chart_name"] = release.get("name")
                    result_data["status"] = release.get("status")

                    # Try to get Helm values to extract basedomain and endpoints
                    try:
                        values_cmd = [
                            helm,
                            "get",
                            "values",
                            release.get("name"),
                            "-n",
                            release.get("namespace"),
                            "-o",
                            "json",
                            "--kube-context",
                            context,
                        ]
                        values_returncode, values_stdout, _ = await run_command(values_cmd)

                        if values_returncode == 0:
                            # Extract JSON from values output (handle warnings)
                            json_start = values_stdout.find("{")
                            if json_start >= 0:
                                json_output = values_stdout[json_start:]
                                values = json.loads(json_output)
                            else:
                                values = json.loads(values_stdout)  # Fallback

                            # Extract basedomain
                            basedomain = values.get("global", {}).get("baseDomain")
                            if basedomain:
                                result_data["basedomain"] = basedomain
                                # Construct default endpoints from basedomain
                                result_data["controller_endpoint"] = f"grpc.{basedomain}:8082"
                                result_data["router_endpoint"] = f"router.{basedomain}:8083"

                            # Check for explicit endpoints in values
                            controller_config = values.get("jumpstarter-controller", {}).get("grpc", {})
                            if controller_config.get("endpoint"):
                                result_data["controller_endpoint"] = controller_config["endpoint"]
                            if controller_config.get("routerEndpoint"):
                                result_data["router_endpoint"] = controller_config["routerEndpoint"]

                    except (json.JSONDecodeError, RuntimeError):
                        # Failed to get Helm values, but we still have basic info
                        pass

                    break

        # Check for Jumpstarter CRDs as secondary verification
        try:
            crd_cmd = [kubectl, "--context", context, "get", "crd", "-o", "json"]
            returncode, stdout, _ = await run_command(crd_cmd)

            if returncode == 0:
                # Extract JSON from CRD output (handle warnings)
                json_start = stdout.find("{")
                if json_start >= 0:
                    json_output = stdout[json_start:]
                    crds = json.loads(json_output)
                else:
                    crds = json.loads(stdout)  # Fallback
                jumpstarter_crds = []
                for item in crds.get("items", []):
                    name = item.get("metadata", {}).get("name", "")
                    if "jumpstarter.dev" in name:
                        jumpstarter_crds.append(name)

                if jumpstarter_crds:
                    result_data["has_crds"] = True
                    if not result_data["installed"]:
                        # CRDs exist but no Helm release found - manual installation?
                        result_data["installed"] = True
                        result_data["version"] = "unknown"
                        result_data["namespace"] = namespace or "unknown"
                        result_data["status"] = "manual-install"
        except RuntimeError:
            pass  # CRD check failed, continue with Helm results

    except json.JSONDecodeError as e:
        result_data["error"] = f"Failed to parse Helm output: {e}"
    except RuntimeError as e:
        result_data["error"] = f"Command failed: {e}"

    return V1Alpha1JumpstarterInstance(**result_data)


async def get_cluster_info(
    context: str,
    kubectl: str = "kubectl",
    helm: str = "helm",
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
            jumpstarter_info = await check_jumpstarter_installation(context, None, helm, kubectl)
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

    except Exception as e:
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
    helm: str = "helm",
    kind: str = "kind",
    minikube: str = "minikube",
) -> V1Alpha1ClusterList:
    """List all Kubernetes clusters with Jumpstarter status."""
    try:
        contexts = await get_kubectl_contexts(kubectl)
        cluster_infos = []

        for context in contexts:
            cluster_info = await get_cluster_info(context["name"], kubectl, helm, minikube)

            # Filter by type if specified
            if cluster_type_filter != "all" and cluster_info.type != cluster_type_filter:
                continue

            cluster_infos.append(cluster_info)

        return V1Alpha1ClusterList(items=cluster_infos)

    except Exception as e:
        # Return empty list with error in the first cluster
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
