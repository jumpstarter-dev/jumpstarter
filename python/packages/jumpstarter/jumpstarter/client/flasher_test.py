import warnings

import pytest

from jumpstarter.client.flasher import _parse_path


class TestParsePath:
    """Tests for _parse_path which routes local files vs HTTP URLs."""

    def test_http_url(self):
        local, url = _parse_path("http://example.com/image.qcow2")
        assert local is None
        assert url == "http://example.com/image.qcow2"

    def test_https_url(self):
        local, url = _parse_path("https://download.fedoraproject.org/pub/fedora/image.qcow2")
        assert local is None
        assert url == "https://download.fedoraproject.org/pub/fedora/image.qcow2"

    def test_local_path_string(self, tmp_path):
        test_file = tmp_path / "image.qcow2"
        test_file.touch()
        local, url = _parse_path(str(test_file))
        assert url is None
        assert local == test_file.resolve()

    def test_local_path_object(self, tmp_path):
        test_file = tmp_path / "image.qcow2"
        test_file.touch()
        local, url = _parse_path(test_file)
        assert url is None
        assert local == test_file.resolve()

    def test_relative_path(self):
        local, url = _parse_path("relative/path/image.qcow2")
        assert url is None
        assert local is not None
        assert local.is_absolute()

    def test_url_with_query_params(self):
        test_url = "https://example.com/image.qcow2?token=abc&expires=123"
        local, url = _parse_path(test_url)
        assert local is None
        assert url == test_url


class TestHttpUrlAdapter:
    """Tests for _http_url_adapter which creates PresignedRequestResource for HTTP URLs."""

    @pytest.mark.anyio
    async def test_read_mode_produces_get_request(self):
        from jumpstarter.client.flasher import _http_url_adapter

        # _http_url_adapter is decorated with @blocking, but the underlying
        # async generator can be tested directly via its __wrapped__ attribute
        gen = _http_url_adapter.__wrapped__(
            client=None,
            url="https://example.com/firmware.bin",
            mode="rb",
        )
        result = await gen.__aenter__()

        # Should produce a serialized PresignedRequestResource with GET method
        assert result["url"] == "https://example.com/firmware.bin"
        assert result["method"] == "GET"
        assert result["headers"] == {}

        await gen.__aexit__(None, None, None)

    @pytest.mark.anyio
    async def test_write_mode_produces_put_request(self):
        from jumpstarter.client.flasher import _http_url_adapter

        gen = _http_url_adapter.__wrapped__(
            client=None,
            url="https://example.com/dump.bin",
            mode="wb",
        )
        result = await gen.__aenter__()

        assert result["url"] == "https://example.com/dump.bin"
        assert result["method"] == "PUT"
        assert result["headers"] == {}

        await gen.__aexit__(None, None, None)


class TestAsyncIteratorStream:
    """Tests for _AsyncIteratorStream receive/send/aclose lifecycle."""

    @pytest.mark.anyio
    async def test_receive_yields_chunks(self):
        from anyio import EndOfStream

        from jumpstarter.client.flasher import _AsyncIteratorStream

        async def gen():
            yield b"chunk1"
            yield b"chunk2"

        stream = _AsyncIteratorStream(iterator=gen(), total=12)
        assert await stream.receive() == b"chunk1"
        assert await stream.receive() == b"chunk2"
        with pytest.raises(EndOfStream):
            await stream.receive()

    @pytest.mark.anyio
    async def test_send_raises_broken_resource(self):
        from anyio import BrokenResourceError

        from jumpstarter.client.flasher import _AsyncIteratorStream

        async def gen():
            yield b"data"

        stream = _AsyncIteratorStream(iterator=gen())
        with pytest.raises(BrokenResourceError):
            await stream.send(b"data")

    @pytest.mark.anyio
    async def test_aclose_propagates_to_generator(self):
        from jumpstarter.client.flasher import _AsyncIteratorStream

        closed = False

        async def gen():
            nonlocal closed
            try:
                yield b"data"
                yield b"more"
            finally:
                closed = True

        stream = _AsyncIteratorStream(iterator=gen())
        await stream.receive()
        await stream.aclose()
        assert closed

    @pytest.mark.anyio
    async def test_extra_attributes_with_total(self):
        from jumpstarter.client.flasher import _AsyncIteratorStream
        from jumpstarter.streams.progress import ProgressAttribute

        async def gen():
            yield b"data"

        stream = _AsyncIteratorStream(iterator=gen(), total=100)
        attrs = stream.extra_attributes
        assert ProgressAttribute.total in attrs
        assert attrs[ProgressAttribute.total]() == 100.0

    @pytest.mark.anyio
    async def test_extra_attributes_without_total(self):
        from jumpstarter.client.flasher import _AsyncIteratorStream

        async def gen():
            yield b"data"

        stream = _AsyncIteratorStream(iterator=gen(), total=None)
        assert stream.extra_attributes == {}

    @pytest.mark.anyio
    async def test_receive_on_empty_iterator(self):
        from anyio import EndOfStream

        from jumpstarter.client.flasher import _AsyncIteratorStream

        async def gen():
            return
            yield  # noqa: RET504

        stream = _AsyncIteratorStream(iterator=gen())
        with pytest.raises(EndOfStream):
            await stream.receive()


class TestFileWriteObjectStream:
    """Tests for _FileWriteObjectStream send/aclose lifecycle."""

    @pytest.mark.anyio
    async def test_write_and_read_back(self, tmp_path):
        from jumpstarter.client.flasher import _FileWriteObjectStream

        out = tmp_path / "output.bin"
        stream = _FileWriteObjectStream(path=out)
        await stream.send(b"hello ")
        await stream.send(b"world")
        await stream.send_eof()
        assert out.read_bytes() == b"hello world"

    @pytest.mark.anyio
    async def test_receive_raises_end_of_stream(self, tmp_path):
        from anyio import EndOfStream

        from jumpstarter.client.flasher import _FileWriteObjectStream

        stream = _FileWriteObjectStream(path=tmp_path / "out.bin")
        with pytest.raises(EndOfStream):
            await stream.receive()

    @pytest.mark.anyio
    async def test_aclose_without_open(self, tmp_path):
        from jumpstarter.client.flasher import _FileWriteObjectStream

        stream = _FileWriteObjectStream(path=tmp_path / "out.bin")
        await stream.aclose()

    @pytest.mark.anyio
    async def test_aclose_closes_file(self, tmp_path):
        from jumpstarter.client.flasher import _FileWriteObjectStream

        out = tmp_path / "output.bin"
        stream = _FileWriteObjectStream(path=out)
        await stream.send(b"data")
        await stream.aclose()
        assert out.read_bytes() == b"data"
        assert stream._file is None


class TestFlasherClientRouting:
    """Tests that FlasherClient routes HTTP URLs vs local paths correctly."""

    def test_flash_single_routes_http_url(self):
        """Verify that an HTTP URL goes through _http_url_adapter, not _local_file_adapter."""
        from unittest.mock import MagicMock, patch

        from jumpstarter.client.flasher import FlasherClient

        client = object.__new__(FlasherClient)

        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value="http_handle")
        mock_http.__exit__ = MagicMock(return_value=False)

        mock_local = MagicMock()

        with (
            patch("jumpstarter.client.flasher._http_url_adapter", return_value=mock_http) as http_patch,
            patch("jumpstarter.client.flasher._local_file_adapter", return_value=mock_local) as local_patch,
            patch.object(client, "call", return_value=None) as call_mock,
        ):
            client._flash_single("https://example.com/image.bin", target=None, compression=None)

            http_patch.assert_called_once_with(client=client, url="https://example.com/image.bin", mode="rb")
            local_patch.assert_not_called()
            call_mock.assert_called_once_with("flash", "http_handle", None)

    def test_flash_single_routes_local_path(self, tmp_path):
        """Verify that a local path goes through _local_file_adapter, not _http_url_adapter."""
        from unittest.mock import MagicMock, patch

        from jumpstarter.client.flasher import FlasherClient

        client = object.__new__(FlasherClient)
        test_file = tmp_path / "image.bin"
        test_file.touch()

        mock_local = MagicMock()
        mock_local.__enter__ = MagicMock(return_value="local_handle")
        mock_local.__exit__ = MagicMock(return_value=False)

        mock_http = MagicMock()

        with (
            patch("jumpstarter.client.flasher._http_url_adapter", return_value=mock_http) as http_patch,
            patch("jumpstarter.client.flasher._local_file_adapter", return_value=mock_local) as local_patch,
            patch.object(client, "call", return_value=None) as call_mock,
        ):
            client._flash_single(str(test_file), target=None, compression=None)

            local_patch.assert_called_once()
            http_patch.assert_not_called()
            call_mock.assert_called_once_with("flash", "local_handle", None)

    def test_dump_routes_http_url(self):
        """Verify that dump with an HTTP URL goes through _http_url_adapter."""
        from unittest.mock import MagicMock, patch

        from jumpstarter.client.flasher import FlasherClient

        client = object.__new__(FlasherClient)

        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value="http_handle")
        mock_http.__exit__ = MagicMock(return_value=False)

        with (
            patch("jumpstarter.client.flasher._http_url_adapter", return_value=mock_http) as http_patch,
            patch("jumpstarter.client.flasher._local_file_adapter") as local_patch,
            patch.object(client, "call", return_value=None) as call_mock,
        ):
            client.dump("https://example.com/dump.bin", target=None)

            http_patch.assert_called_once_with(client=client, url="https://example.com/dump.bin", mode="wb")
            local_patch.assert_not_called()
            call_mock.assert_called_once_with("dump", "http_handle", None)

    def test_dump_routes_local_path(self, tmp_path):
        """Verify that dump with a local path goes through _local_file_adapter."""
        from unittest.mock import MagicMock, patch

        from jumpstarter.client.flasher import FlasherClient

        client = object.__new__(FlasherClient)
        test_file = tmp_path / "dump.bin"

        mock_local = MagicMock()
        mock_local.__enter__ = MagicMock(return_value="local_handle")
        mock_local.__exit__ = MagicMock(return_value=False)

        with (
            patch("jumpstarter.client.flasher._http_url_adapter") as http_patch,
            patch("jumpstarter.client.flasher._local_file_adapter", return_value=mock_local) as local_patch,
            patch.object(client, "call", return_value=None) as call_mock,
        ):
            client.dump(str(test_file), target=None)

            local_patch.assert_called_once()
            http_patch.assert_not_called()
            call_mock.assert_called_once_with("dump", "local_handle", None)


class TestFlasherClientMultiTarget:
    """Tests for dict-based multi-target flash."""

    def test_flash_dict_calls_flash_single_per_entry(self):
        from unittest.mock import MagicMock, patch

        from jumpstarter.client.flasher import FlasherClient

        client = object.__new__(FlasherClient)

        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value="handle")
        mock_http.__exit__ = MagicMock(return_value=False)

        with (
            patch("jumpstarter.client.flasher._http_url_adapter", return_value=mock_http),
            patch.object(client, "call", return_value="ok") as call_mock,
        ):
            results = client.flash(
                {"boot": "https://example.com/boot.bin", "root": "https://example.com/root.bin"},
                compression=None,
            )
            assert results == {"boot": "ok", "root": "ok"}
            assert call_mock.call_count == 2

    def test_flash_dict_with_target_raises_argument_error(self):
        from jumpstarter.client.flasher import FlasherClient
        from jumpstarter.common.exceptions import ArgumentError

        client = object.__new__(FlasherClient)

        with pytest.raises(ArgumentError, match="'target' parameter is not valid"):
            client.flash({"boot": "/tmp/boot.bin"}, target="some_target")


class TestCompressionWarning:
    """Tests that compression parameter warns when used with HTTP URLs."""

    def test_flash_http_with_compression_warns(self):
        from unittest.mock import MagicMock, patch

        from jumpstarter.client.flasher import FlasherClient

        client = object.__new__(FlasherClient)

        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value="handle")
        mock_http.__exit__ = MagicMock(return_value=False)

        with (
            patch("jumpstarter.client.flasher._http_url_adapter", return_value=mock_http),
            patch.object(client, "call", return_value=None),
        ):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                client._flash_single("https://example.com/image.bin", target=None, compression="zstd")
                assert len(w) == 1
                assert "compression parameter is ignored" in str(w[0].message)

    def test_flash_local_with_compression_no_warning(self, tmp_path):
        from unittest.mock import MagicMock, patch

        from jumpstarter.client.flasher import FlasherClient

        client = object.__new__(FlasherClient)
        test_file = tmp_path / "image.bin"
        test_file.touch()

        mock_local = MagicMock()
        mock_local.__enter__ = MagicMock(return_value="handle")
        mock_local.__exit__ = MagicMock(return_value=False)

        with (
            patch("jumpstarter.client.flasher._local_file_adapter", return_value=mock_local),
            patch.object(client, "call", return_value=None),
        ):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                client._flash_single(str(test_file), target=None, compression="zstd")
                assert len(w) == 0

    def test_dump_http_with_compression_warns(self):
        from unittest.mock import MagicMock, patch

        from jumpstarter.client.flasher import FlasherClient

        client = object.__new__(FlasherClient)

        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value="handle")
        mock_http.__exit__ = MagicMock(return_value=False)

        with (
            patch("jumpstarter.client.flasher._http_url_adapter", return_value=mock_http),
            patch.object(client, "call", return_value=None),
        ):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                client.dump("https://example.com/dump.bin", target=None, compression="zstd")
                assert len(w) == 1
                assert "compression parameter is ignored" in str(w[0].message)
