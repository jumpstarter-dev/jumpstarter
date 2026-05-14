import yarl

from .base import Driver


def test_make_url_preserves_percent_encoding():
    """_make_url must preserve %XX sequences that appear in presigned URLs.

    Without encoded=True, yarl.URL normalizes %40 to @, breaking S3/CloudFront
    signatures which are computed over the percent-encoded form.
    """
    url = "https://cdn.example.com/path%40encoded/file.bin?X-Amz-Signature=abc123"

    broken = yarl.URL(url)
    assert "%40" not in str(broken), "sanity: yarl.URL() should decode %40"

    preserved = Driver._make_url(url)
    assert str(preserved) == url


def test_make_url_preserves_multiple_encoded_sequences():
    url = "https://cdn.example.com/a%40b%2Fc%23d?token=xyz"

    preserved = Driver._make_url(url)
    assert str(preserved) == url
    assert "%40" in str(preserved)
    assert "%2F" in str(preserved)
    assert "%23" in str(preserved)


def test_strip_sensitive_headers_same_origin():
    headers = {
        "Authorization": "Bearer tok",
        "x-amz-security-token": "sess",
        "Content-Type": "application/octet-stream",
    }
    result = Driver._strip_sensitive_headers(
        headers, "https://s3.amazonaws.com/a", "https://s3.amazonaws.com/b"
    )
    assert result == headers


def test_strip_sensitive_headers_cross_origin():
    headers = {
        "Authorization": "Bearer tok",
        "x-amz-security-token": "sess",
        "x-amz-server-side-encryption-customer-key": "secret",
        "Content-Type": "application/octet-stream",
        "Host": "s3.amazonaws.com",
    }
    result = Driver._strip_sensitive_headers(headers, "https://s3.amazonaws.com/a", "https://cdn.example.com/b")
    assert "Authorization" not in result
    assert "x-amz-security-token" not in result
    assert "x-amz-server-side-encryption-customer-key" not in result
    assert result["Content-Type"] == "application/octet-stream"
    assert result["Host"] == "s3.amazonaws.com"


def test_strip_sensitive_headers_different_port():
    headers = {"Authorization": "Bearer tok", "Accept": "*/*"}
    result = Driver._strip_sensitive_headers(headers, "https://s3.amazonaws.com:443/a", "https://s3.amazonaws.com:8443/b")
    assert "Authorization" not in result
    assert result["Accept"] == "*/*"


def test_strip_sensitive_headers_scheme_change():
    headers = {"Authorization": "Bearer tok", "Accept": "*/*"}
    result = Driver._strip_sensitive_headers(
        headers, "https://s3.amazonaws.com/a", "http://s3.amazonaws.com/a"
    )
    assert "Authorization" not in result
    assert result["Accept"] == "*/*"


def test_strip_sensitive_headers_cloud_providers():
    headers = {"x-ms-blob-type": "BlockBlob", "x-goog-encryption-key": "key", "Accept": "*/*"}
    result = Driver._strip_sensitive_headers(headers, "https://a.blob.core.windows.net/c", "https://evil.com/c")
    assert "x-ms-blob-type" not in result
    assert "x-goog-encryption-key" not in result
    assert result["Accept"] == "*/*"


def test_strip_sensitive_headers_cookie_and_proxy_auth():
    headers = {"Cookie": "session=abc", "Proxy-Authorization": "Basic xyz", "Accept": "*/*"}
    result = Driver._strip_sensitive_headers(headers, "https://s3.amazonaws.com/a", "https://evil.com/b")
    assert "Cookie" not in result
    assert "Proxy-Authorization" not in result
    assert result["Accept"] == "*/*"


def test_redact_url_with_query_params():
    url = "https://s3.amazonaws.com/bucket/key?X-Amz-Credential=AKIA123&X-Amz-Signature=abc"
    assert Driver._redact_url(url) == "https://s3.amazonaws.com/bucket/key?[REDACTED]"


def test_redact_url_without_query_params():
    url = "https://s3.amazonaws.com/bucket/key"
    assert Driver._redact_url(url) == url
