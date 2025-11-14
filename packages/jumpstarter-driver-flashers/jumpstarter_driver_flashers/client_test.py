import click
import pytest

from .client import BaseFlasherClient, FlashNonRetryableError, FlashRetryableError
from jumpstarter.common.exceptions import ArgumentError


class MockFlasherClient(BaseFlasherClient):
    """Mock client for testing without full initialization"""

    def __init__(self):
        self._manifest = None
        self._console_debug = False
        self.logger = type(
            "MockLogger",
            (),
            {
                "warning": lambda *args, **kwargs: None,
                "info": lambda *args, **kwargs: None,
                "error": lambda *args, **kwargs: None,
                "exception": lambda *args, **kwargs: None,
            },
        )()

    def close(self):
        pass


def test_validate_bearer_token_fails_invalid():
    """Test bearer token validation fails with invalid tokens"""
    client = MockFlasherClient()

    with pytest.raises(click.ClickException, match="Bearer token cannot be empty"):
        client._validate_bearer_token("")

    with pytest.raises(click.ClickException, match="Bearer token contains invalid characters"):
        client._validate_bearer_token("token with spaces")

    with pytest.raises(click.ClickException, match="Bearer token contains invalid characters"):
        client._validate_bearer_token('token"with"quotes')


def test_curl_header_args_handles_quotes():
    """Test curl header formatting safely handles quotes"""
    client = MockFlasherClient()

    result = client._curl_header_args({"Authorization": "Bearer abc'def"})
    assert "'\"'\"'" in result
    assert result.startswith("-H '")
    assert result.endswith("'")


def test_flash_fails_with_invalid_headers():
    """Test flash method fails early with invalid headers"""
    client = MockFlasherClient()

    with pytest.raises(ArgumentError, match="Invalid header name 'Invalid Header': must be an HTTP token"):
        client.flash("test.raw", headers={"Invalid Header": "value"})


def test_categorize_exception_returns_non_retryable_when_present():
    """Test that non-retryable errors take priority"""
    client = MockFlasherClient()

    # Direct non-retryable error
    error = FlashNonRetryableError("Config error")
    result = client._categorize_exception(error)
    assert isinstance(result, FlashNonRetryableError)
    assert str(result) == "Config error"


def test_categorize_exception_returns_retryable_when_present():
    """Test that retryable errors are returned"""
    client = MockFlasherClient()

    # Direct retryable error
    error = FlashRetryableError("Network timeout")
    result = client._categorize_exception(error)
    assert isinstance(result, FlashRetryableError)
    assert str(result) == "Network timeout"


def test_categorize_exception_wraps_unknown_exceptions():
    """Test that unknown exceptions are wrapped as retryable"""
    client = MockFlasherClient()

    # Unknown exception type
    error = ValueError("Something went wrong")
    result = client._categorize_exception(error)
    assert isinstance(result, FlashRetryableError)
    assert "ValueError" in str(result)
    assert "Something went wrong" in str(result)
    # Verify the cause chain is preserved
    assert result.__cause__ is error


def test_categorize_exception_non_retryable_takes_priority_over_retryable():
    """Test that non-retryable errors take priority in cause chain"""
    client = MockFlasherClient()

    # Create a chain: retryable caused by non-retryable
    non_retryable = FlashNonRetryableError("Config issue")
    retryable = FlashRetryableError("Network error")
    retryable.__cause__ = non_retryable

    result = client._categorize_exception(retryable)
    assert isinstance(result, FlashNonRetryableError)
    assert str(result) == "Config issue"


def test_categorize_exception_searches_cause_chain():
    """Test that categorization searches through the cause chain"""
    client = MockFlasherClient()

    # Create a chain: generic -> generic -> retryable
    root = FlashRetryableError("Root cause")
    middle = ValueError("Middle error")
    middle.__cause__ = root
    top = RuntimeError("Top error")
    top.__cause__ = middle

    result = client._categorize_exception(top)
    assert isinstance(result, FlashRetryableError)
    assert str(result) == "Root cause"


def test_find_exception_in_chain_finds_target_type():
    """Test that _find_exception_in_chain correctly finds the target type"""
    client = MockFlasherClient()

    # Create a chain with retryable error
    retryable = FlashRetryableError("Network error")
    generic = RuntimeError("Generic error")
    generic.__cause__ = retryable

    result = client._find_exception_in_chain(generic, FlashRetryableError)
    assert result is retryable
    assert str(result) == "Network error"


def test_find_exception_in_chain_returns_none_when_not_found():
    """Test that _find_exception_in_chain returns None when target not found"""
    client = MockFlasherClient()

    error = ValueError("Some error")
    result = client._find_exception_in_chain(error, FlashRetryableError)
    assert result is None


def test_find_exception_in_chain_handles_exception_groups():
    """Test that _find_exception_in_chain searches through ExceptionGroups"""
    client = MockFlasherClient()

    # Create an ExceptionGroup with a retryable error
    retryable = FlashRetryableError("Network timeout")
    generic = ValueError("Generic error")

    # Mock an ExceptionGroup (Python 3.11+)
    class MockExceptionGroup(Exception):
        def __init__(self, message, exceptions):
            super().__init__(message)
            self.exceptions = exceptions

    group = MockExceptionGroup("Multiple errors", [generic, retryable])

    result = client._find_exception_in_chain(group, FlashRetryableError)
    assert result is retryable


def test_categorize_exception_with_nested_exception_groups():
    """Test categorization with nested ExceptionGroups"""
    client = MockFlasherClient()

    # Create nested ExceptionGroups
    non_retryable = FlashNonRetryableError("Config error")

    class MockExceptionGroup(Exception):
        def __init__(self, message, exceptions):
            super().__init__(message)
            self.exceptions = exceptions

    inner_group = MockExceptionGroup("Inner errors", [non_retryable])
    outer_group = MockExceptionGroup("Outer errors", [ValueError("Other"), inner_group])

    result = client._categorize_exception(outer_group)
    assert isinstance(result, FlashNonRetryableError)
    assert str(result) == "Config error"


def test_categorize_exception_preserves_cause_for_wrapped_exceptions():
    """Test that wrapped unknown exceptions preserve the cause chain"""
    client = MockFlasherClient()

    original = IOError("File not found")
    result = client._categorize_exception(original)

    assert isinstance(result, FlashRetryableError)
    assert result.__cause__ is original
    # IOError is an alias for OSError in Python 3
    assert "OSError" in str(result) or "IOError" in str(result)
    assert "File not found" in str(result)
