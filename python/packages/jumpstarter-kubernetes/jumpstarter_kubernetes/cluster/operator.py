"""Operator-based Jumpstarter installation."""

from subprocess import PIPE
from typing import Literal, Optional

import anyio

from ..callbacks import OutputCallback, SilentCallback
from ..exceptions import ClusterOperationError
from .common import GRPC_NODEPORT, LOGIN_NODEPORT, ROUTER_NODEPORT, run_command, run_command_with_output

CERTMANAGER_VERSION = "v1.19.2"
OPERATOR_INSTALLER_URL_TEMPLATE = (
    "https://github.com/jumpstarter-dev/jumpstarter/releases/download/{version}/operator-installer.yaml"
)
OPERATOR_NAMESPACE = "jumpstarter-operator-system"
OPERATOR_DEPLOYMENT = "jumpstarter-operator-controller-manager"


def _kubectl_base(kubeconfig: Optional[str] = None, context: Optional[str] = None) -> list[str]:
    """Build base kubectl command with optional kubeconfig and context."""
    cmd = ["kubectl"]
    if kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig])
    if context:
        cmd.extend(["--context", context])
    return cmd


async def install_cert_manager(
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    callback: OutputCallback = None,
) -> None:
    """Install cert-manager if not already present."""
    if callback is None:
        callback = SilentCallback()

    # Check if cert-manager is already installed
    cmd = _kubectl_base(kubeconfig, context) + ["get", "crd", "certificates.cert-manager.io"]
    returncode, _, _ = await run_command(cmd)
    if returncode == 0:
        callback.progress("cert-manager already installed, skipping")
        return

    callback.progress(f"Installing cert-manager {CERTMANAGER_VERSION}...")
    url = f"https://github.com/cert-manager/cert-manager/releases/download/{CERTMANAGER_VERSION}/cert-manager.yaml"
    returncode = await run_command_with_output(
        _kubectl_base(kubeconfig, context) + ["apply", "-f", url]
    )
    if returncode != 0:
        raise ClusterOperationError("install", "cert-manager", "operator", Exception("Failed to install cert-manager"))

    # Wait for cert-manager to be ready
    callback.progress("Waiting for cert-manager to be ready...")
    returncode = await run_command_with_output(
        _kubectl_base(kubeconfig, context)
        + [
            "wait",
            "--namespace", "cert-manager",
            "--for=condition=available",
            "deployment/cert-manager-webhook",
            "--timeout=120s",
        ]
    )
    if returncode != 0:
        raise ClusterOperationError(
            "install", "cert-manager", "operator", Exception("cert-manager did not become ready")
        )

    callback.success("cert-manager installed")


async def install_operator(
    version: str,
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    operator_installer: Optional[str] = None,
    callback: OutputCallback = None,
) -> None:
    """Apply the operator installer YAML from a GitHub release or local path."""
    if callback is None:
        callback = SilentCallback()

    # Use provided installer path/URL, or construct from version
    installer = operator_installer or OPERATOR_INSTALLER_URL_TEMPLATE.format(version=version)
    callback.progress(f"Installing Jumpstarter operator {version}...")
    callback.progress(f"Installer: {installer}")

    returncode = await run_command_with_output(
        _kubectl_base(kubeconfig, context) + ["apply", "-f", installer]
    )
    if returncode != 0:
        raise ClusterOperationError(
            "install", "operator", "operator",
            Exception(f"Failed to apply operator installer from {installer}"),
        )

    # If operator deployment already exists, restart it to pick up the new image
    cmd = _kubectl_base(kubeconfig, context) + [
        "get", "deployment", OPERATOR_DEPLOYMENT, "-n", OPERATOR_NAMESPACE,
    ]
    returncode, _, _ = await run_command(cmd)
    if returncode == 0:
        callback.progress("Restarting operator to pick up new image...")
        await run_command_with_output(
            _kubectl_base(kubeconfig, context)
            + ["rollout", "restart", f"deployment/{OPERATOR_DEPLOYMENT}", "-n", OPERATOR_NAMESPACE]
        )

    # Wait for operator to be ready
    callback.progress("Waiting for operator to be ready...")
    returncode = await run_command_with_output(
        _kubectl_base(kubeconfig, context)
        + [
            "wait",
            "--namespace", OPERATOR_NAMESPACE,
            "--for=condition=available",
            f"deployment/{OPERATOR_DEPLOYMENT}",
            "--timeout=120s",
        ]
    )
    if returncode != 0:
        raise ClusterOperationError(
            "install", "operator", "operator", Exception("Operator did not become ready")
        )

    callback.success("Operator is ready")


def _build_jumpstarter_cr(
    namespace: str,
    basedomain: str,
    grpc_endpoint: str,
    router_endpoint: str,
    mode: Literal["nodeport", "ingress"],
    image: Optional[str] = None,
) -> str:
    """Build the Jumpstarter CR YAML."""
    if mode == "nodeport":
        controller_endpoint = f"""        - address: "{grpc_endpoint}"
          nodeport:
            enabled: true
            port: {GRPC_NODEPORT}"""
        router_endpoint_config = f"""        - address: "{router_endpoint}"
          nodeport:
            enabled: true
            port: {ROUTER_NODEPORT}"""
        login_endpoint = f"""    login:
      endpoints:
        - address: "login.{basedomain}:{LOGIN_NODEPORT}"
          nodeport:
            enabled: true
            port: {LOGIN_NODEPORT}"""
    else:
        controller_endpoint = f"""        - address: "{grpc_endpoint}"
          ingress:
            enabled: true
            class: "nginx" """
        router_endpoint_config = f"""        - address: "{router_endpoint}"
          ingress:
            enabled: true
            class: "nginx" """
        login_endpoint = f"""    login:
      endpoints:
        - address: "login.{basedomain}:443"
          ingress:
            enabled: true
            class: "nginx" """

    image_config = ""
    if image:
        image_config = f"""
    image: {image}
    imagePullPolicy: IfNotPresent"""

    cr = f"""apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: {namespace}
spec:
  baseDomain: {basedomain}
  certManager:
    enabled: true
    server:
      selfSigned:
        enabled: true
  authentication:
    internal:
      prefix: "internal:"
      enabled: true
    autoProvisioning:
      enabled: true
  controller:{image_config}
    replicas: 1
    grpc:
      endpoints:
{controller_endpoint}
{login_endpoint}
  routers:{image_config}
    replicas: 1
    resources:
      requests:
        cpu: 100m
        memory: 100Mi
    grpc:
      endpoints:
{router_endpoint_config}
"""
    return cr


async def apply_jumpstarter_cr(
    namespace: str,
    basedomain: str,
    grpc_endpoint: str,
    router_endpoint: str,
    mode: Literal["nodeport", "ingress"] = "nodeport",
    image: Optional[str] = None,
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    callback: OutputCallback = None,
) -> None:
    """Create and apply the Jumpstarter custom resource."""
    if callback is None:
        callback = SilentCallback()

    # Create namespace
    cmd = _kubectl_base(kubeconfig, context) + [
        "create", "namespace", namespace, "--dry-run=client", "-o", "yaml",
    ]
    returncode, ns_yaml, _ = await run_command(cmd)
    if returncode == 0:
        apply_cmd = _kubectl_base(kubeconfig, context) + ["apply", "-f", "-"]
        result = await anyio.run_process(apply_cmd, input=ns_yaml.encode(), stdout=PIPE, stderr=PIPE)
        if result.returncode != 0:
            raise ClusterOperationError(
                "install", "jumpstarter", "operator",
                Exception(f"Failed to create namespace {namespace}: {result.stderr.decode(errors='replace')}"),
            )

    # Build and apply the CR
    cr_yaml = _build_jumpstarter_cr(namespace, basedomain, grpc_endpoint, router_endpoint, mode, image)
    callback.progress("Applying Jumpstarter CR...")

    apply_cmd = _kubectl_base(kubeconfig, context) + ["apply", "-f", "-"]
    result = await anyio.run_process(apply_cmd, input=cr_yaml.encode(), stdout=PIPE, stderr=PIPE, check=False)

    if result.returncode != 0:
        raise ClusterOperationError(
            "install", "jumpstarter", "operator",
            Exception(f"Failed to apply Jumpstarter CR: {result.stderr.decode(errors='replace')}"),
        )

    callback.success("Jumpstarter CR applied")


async def wait_for_jumpstarter_ready(
    namespace: str = "jumpstarter-lab",
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    callback: OutputCallback = None,
    timeout: int = 300,
) -> None:
    """Wait for Jumpstarter controller and router deployments to become ready."""
    if callback is None:
        callback = SilentCallback()

    callback.progress("Waiting for Jumpstarter deployments to be ready...")

    poll_interval = 5
    max_polls = timeout // poll_interval

    # The operator creates deployments named jumpstarter-controller and jumpstarter-router-0
    for deployment in ["jumpstarter-controller", "jumpstarter-router-0"]:
        # First wait for the deployment to exist (operator needs time to create it)
        for _ in range(max_polls):
            cmd = _kubectl_base(kubeconfig, context) + [
                "get", "deployment", deployment, "-n", namespace,
            ]
            returncode, _, _ = await run_command(cmd)
            if returncode == 0:
                break
            await anyio.sleep(poll_interval)
        else:
            raise ClusterOperationError(
                "install", "jumpstarter", "operator",
                Exception(f"Timeout waiting for deployment/{deployment} to be created"),
            )

        # Then wait for it to be available
        returncode = await run_command_with_output(
            _kubectl_base(kubeconfig, context)
            + [
                "wait",
                "--namespace", namespace,
                "--for=condition=available",
                f"deployment/{deployment}",
                f"--timeout={timeout}s",
            ]
        )
        if returncode != 0:
            raise ClusterOperationError(
                "install", "jumpstarter", "operator",
                Exception(f"deployment/{deployment} did not become ready"),
            )

    callback.success("Jumpstarter is ready")


async def install_jumpstarter_operator(
    version: str,
    namespace: str,
    basedomain: str,
    grpc_endpoint: str,
    router_endpoint: str,
    mode: Literal["nodeport", "ingress"] = "nodeport",
    image: Optional[str] = None,
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    operator_installer: Optional[str] = None,
    callback: OutputCallback = None,
) -> None:
    """Install Jumpstarter using the operator method.

    This is the high-level orchestrator that:
    1. Installs cert-manager (if not present)
    2. Applies the operator installer
    3. Creates the Jumpstarter CR
    4. Waits for everything to be ready
    """
    if callback is None:
        callback = SilentCallback()

    # Step 1: cert-manager
    await install_cert_manager(kubeconfig, context, callback)

    # Step 2: operator
    await install_operator(version, kubeconfig, context, operator_installer, callback)

    # Step 3: Jumpstarter CR
    await apply_jumpstarter_cr(
        namespace, basedomain, grpc_endpoint, router_endpoint,
        mode, image, kubeconfig, context, callback,
    )

    # Step 4: Wait for readiness
    await wait_for_jumpstarter_ready(namespace, kubeconfig, context, callback)
