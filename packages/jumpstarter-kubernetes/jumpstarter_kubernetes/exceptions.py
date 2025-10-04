"""Custom exceptions for jumpstarter-kubernetes package.

This module defines domain-specific exceptions to replace click.ClickException
and provide better error handling without CLI framework dependencies.
"""


class JumpstarterKubernetesError(Exception):
    """Base exception for all jumpstarter-kubernetes errors."""

    pass


class ToolNotInstalledError(JumpstarterKubernetesError):
    """Raised when a required tool (kind, minikube, helm, kubectl) is not installed."""

    def __init__(self, tool_name: str, additional_info: str = ""):
        self.tool_name = tool_name
        message = f"{tool_name} is not installed (or not in your PATH)"
        if additional_info:
            message += f": {additional_info}"
        super().__init__(message)


class ClusterNotFoundError(JumpstarterKubernetesError):
    """Raised when a cluster cannot be found."""

    def __init__(self, cluster_name: str, cluster_type: str = None):
        self.cluster_name = cluster_name
        self.cluster_type = cluster_type
        if cluster_type:
            message = f'{cluster_type.title()} cluster "{cluster_name}" does not exist'
        else:
            message = f'No cluster named "{cluster_name}" found'
        super().__init__(message)


class ClusterAlreadyExistsError(JumpstarterKubernetesError):
    """Raised when trying to create a cluster that already exists."""

    def __init__(self, cluster_name: str, cluster_type: str):
        self.cluster_name = cluster_name
        self.cluster_type = cluster_type
        message = f'{cluster_type.title()} cluster "{cluster_name}" already exists'
        super().__init__(message)


class ClusterOperationError(JumpstarterKubernetesError):
    """Raised when a cluster operation (create, delete, etc.) fails."""

    def __init__(self, operation: str, cluster_name: str, cluster_type: str, cause: Exception = None):
        self.operation = operation
        self.cluster_name = cluster_name
        self.cluster_type = cluster_type
        self.cause = cause
        if cause:
            message = f"Failed to {operation} {cluster_type} cluster: {cause}"
        else:
            message = f"Failed to {operation} {cluster_type} cluster"
        super().__init__(message)


class CertificateError(JumpstarterKubernetesError):
    """Raised when certificate operations fail."""

    def __init__(self, message: str, certificate_path: str = None):
        self.certificate_path = certificate_path
        super().__init__(message)


class KubeconfigError(JumpstarterKubernetesError):
    """Raised when kubectl configuration operations fail."""

    def __init__(self, message: str, config_path: str = None):
        self.config_path = config_path
        super().__init__(message)


class ClusterTypeValidationError(JumpstarterKubernetesError):
    """Raised when cluster type validation fails."""

    pass


class ClusterNameValidationError(JumpstarterKubernetesError):
    """Raised when cluster name validation fails."""

    def __init__(self, cluster_name: str, reason: str = "Cluster name cannot be empty"):
        self.cluster_name = cluster_name
        super().__init__(reason)


class EndpointConfigurationError(JumpstarterKubernetesError):
    """Raised when endpoint configuration fails."""

    def __init__(self, message: str, cluster_type: str = None):
        self.cluster_type = cluster_type
        super().__init__(message)
