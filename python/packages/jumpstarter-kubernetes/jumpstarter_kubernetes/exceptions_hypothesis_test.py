from hypothesis import given
from hypothesis import strategies as st

from .exceptions import (
    CertificateError,
    ClusterAlreadyExistsError,
    ClusterNameValidationError,
    ClusterNotFoundError,
    ClusterOperationError,
    ClusterTypeValidationError,
    EndpointConfigurationError,
    JumpstarterKubernetesError,
    KubeconfigError,
    ToolNotInstalledError,
)

safe_text = st.text(
    alphabet=st.characters(categories=("L", "N"), max_codepoint=0x7E),
    min_size=1,
    max_size=50,
)


class TestJumpstarterKubernetesError:
    @given(message=safe_text)
    def test_message_preserved(self, message: str) -> None:
        err = JumpstarterKubernetesError(message)
        assert str(err) == message

    @given(message=safe_text)
    def test_is_exception(self, message: str) -> None:
        err = JumpstarterKubernetesError(message)
        assert isinstance(err, Exception)


class TestToolNotInstalledError:
    @given(tool_name=safe_text)
    def test_without_additional_info(self, tool_name: str) -> None:
        err = ToolNotInstalledError(tool_name)
        assert err.tool_name == tool_name
        assert tool_name in str(err)
        assert "not installed" in str(err)

    @given(tool_name=safe_text, additional_info=safe_text)
    def test_with_additional_info(self, tool_name: str, additional_info: str) -> None:
        err = ToolNotInstalledError(tool_name, additional_info)
        assert err.tool_name == tool_name
        assert tool_name in str(err)
        assert additional_info in str(err)

    @given(tool_name=safe_text)
    def test_is_kubernetes_error(self, tool_name: str) -> None:
        err = ToolNotInstalledError(tool_name)
        assert isinstance(err, JumpstarterKubernetesError)


class TestClusterNotFoundError:
    @given(cluster_name=safe_text)
    def test_without_cluster_type(self, cluster_name: str) -> None:
        err = ClusterNotFoundError(cluster_name)
        assert err.cluster_name == cluster_name
        assert cluster_name in str(err)

    @given(cluster_name=safe_text, cluster_type=st.sampled_from(["kind", "minikube"]))
    def test_with_cluster_type(self, cluster_name: str, cluster_type: str) -> None:
        err = ClusterNotFoundError(cluster_name, cluster_type)
        assert err.cluster_name == cluster_name
        assert err.cluster_type == cluster_type
        assert cluster_name in str(err)


class TestClusterAlreadyExistsError:
    @given(
        cluster_name=safe_text,
        cluster_type=st.sampled_from(["kind", "minikube"]),
    )
    def test_construction(self, cluster_name: str, cluster_type: str) -> None:
        err = ClusterAlreadyExistsError(cluster_name, cluster_type)
        assert err.cluster_name == cluster_name
        assert err.cluster_type == cluster_type
        assert cluster_name in str(err)
        assert "already exists" in str(err)


class TestClusterOperationError:
    @given(
        operation=st.sampled_from(["create", "delete", "start", "stop"]),
        cluster_name=safe_text,
        cluster_type=st.sampled_from(["kind", "minikube"]),
    )
    def test_without_cause(self, operation: str, cluster_name: str, cluster_type: str) -> None:
        err = ClusterOperationError(operation, cluster_name, cluster_type)
        assert err.operation == operation
        assert err.cluster_name == cluster_name
        assert err.cluster_type == cluster_type
        assert err.cause is None
        assert operation in str(err)

    @given(
        operation=st.sampled_from(["create", "delete"]),
        cluster_name=safe_text,
        cluster_type=st.sampled_from(["kind", "minikube"]),
    )
    def test_with_cause(self, operation: str, cluster_name: str, cluster_type: str) -> None:
        cause = ValueError("boom")
        err = ClusterOperationError(operation, cluster_name, cluster_type, cause=cause)
        assert err.cause is cause
        assert "boom" in str(err)


class TestCertificateError:
    @given(message=safe_text)
    def test_without_path(self, message: str) -> None:
        err = CertificateError(message)
        assert str(err) == message
        assert err.certificate_path is None

    @given(message=safe_text, path=safe_text)
    def test_with_path(self, message: str, path: str) -> None:
        err = CertificateError(message, certificate_path=path)
        assert str(err) == message
        assert err.certificate_path == path


class TestKubeconfigError:
    @given(message=safe_text)
    def test_without_path(self, message: str) -> None:
        err = KubeconfigError(message)
        assert str(err) == message
        assert err.config_path is None

    @given(message=safe_text, path=safe_text)
    def test_with_path(self, message: str, path: str) -> None:
        err = KubeconfigError(message, config_path=path)
        assert err.config_path == path


class TestClusterTypeValidationError:
    @given(cluster_type=safe_text)
    def test_without_supported_types(self, cluster_type: str) -> None:
        err = ClusterTypeValidationError(cluster_type)
        assert err.cluster_type == cluster_type
        assert err.supported_types == ["kind", "minikube"]
        assert cluster_type in str(err)

    @given(
        cluster_type=safe_text,
        supported=st.lists(safe_text, min_size=1, max_size=5),
    )
    def test_with_supported_types(self, cluster_type: str, supported: list[str]) -> None:
        err = ClusterTypeValidationError(cluster_type, supported_types=supported)
        assert err.supported_types == supported


class TestClusterNameValidationError:
    @given(cluster_name=safe_text)
    def test_default_reason(self, cluster_name: str) -> None:
        err = ClusterNameValidationError(cluster_name)
        assert err.cluster_name == cluster_name
        assert "cannot be empty" in str(err)

    @given(cluster_name=safe_text, reason=safe_text)
    def test_custom_reason(self, cluster_name: str, reason: str) -> None:
        err = ClusterNameValidationError(cluster_name, reason=reason)
        assert str(err) == reason


class TestEndpointConfigurationError:
    @given(message=safe_text)
    def test_without_cluster_type(self, message: str) -> None:
        err = EndpointConfigurationError(message)
        assert str(err) == message
        assert err.cluster_type is None

    @given(message=safe_text, cluster_type=safe_text)
    def test_with_cluster_type(self, message: str, cluster_type: str) -> None:
        err = EndpointConfigurationError(message, cluster_type=cluster_type)
        assert err.cluster_type == cluster_type
