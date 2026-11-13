"""Tests for the video snapshot keywords."""

from __future__ import annotations

import io
from base64 import b64decode, b64encode
from pathlib import Path

import pytest
import robot
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from PIL import Image
from robot.api import ExecutionResult, Failure

from .. import context
from ..driver_library import Driver as DriverLibrary
from ..extensions import extensions_for
from ..testing import use_client
from .media import SNAPSHOT_CLIENTS, MediaKeywords, image_suffix
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, export


def png_bytes(color: str = "red", size: tuple[int, int] = (4, 3)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeVideo(Driver):
    """Serves fixed frames, base64 encoded like VideoInterface.snapshot."""

    frame: bytes = png_bytes()

    @classmethod
    def client(cls) -> str:
        return f"{__name__}.VideoClient"

    @export
    def snapshot(self) -> str:
        return b64encode(type(self).frame).decode("ascii")


class VideoClient(DriverClient):
    """Stands in for jumpstarter_driver_video.client.VideoClient (matched by class name)."""

    def snapshot_bytes(self) -> bytes:
        return b64decode(self.call("snapshot"))


class ImageOnly(Driver):
    @classmethod
    def client(cls) -> str:
        return f"{__name__}.ImageOnlyClient"

    @export
    def frame(self) -> str:
        return b64encode(png_bytes("green", (5, 5))).decode("ascii")


class ImageOnlyClient(DriverClient):
    """Like NanoKVMUSBVideoClient: only ``snapshot()``, returning a PIL image."""

    def snapshot(self) -> Image.Image:
        return Image.open(io.BytesIO(b64decode(self.call("frame"))))


class Odd(Driver):
    @classmethod
    def client(cls) -> str:
        return f"{__name__}.OddClient"

    @export
    def nothing(self) -> None:
        return None


class OddClient(DriverClient):
    def snapshot(self) -> int:
        return 42


@pytest.fixture(autouse=True)
def reset_frame():
    FakeVideo.frame = png_bytes()
    yield
    FakeVideo.frame = png_bytes()


@pytest.fixture
def output(tmp_path, monkeypatch):
    """Point the output directory at tmp_path, as ${OUTPUT DIR} would in a run."""
    monkeypatch.chdir(tmp_path)
    return tmp_path / "jumpstarter"


@pytest.fixture
def client():
    children: dict[str, Driver] = {"cam": FakeVideo(), "image": ImageOnly(), "odd": Odd(), "power": MockPower()}
    with serve(Composite(children=children)) as root, use_client(root):
        yield root


@pytest.fixture
def keywords():
    return MediaKeywords()


def test_image_suffix():
    assert image_suffix(png_bytes()) == ".png"
    assert image_suffix(jpeg_bytes()) == ".jpg"
    assert image_suffix(b"GIF89a....") == ".gif"
    assert image_suffix(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    assert image_suffix(b"BM\x00\x00") == ".bmp"
    assert image_suffix(b"unknown") == ".jpg"


def test_capture_writes_numbered_file_in_output_dir(client, keywords, output):
    first = Path(keywords.capture_video_snapshot("cam", embed=False))
    second = Path(keywords.capture_video_snapshot("cam"))
    assert first.parent.resolve() == output.resolve()
    assert first.name.startswith("cam-snapshot-") and first.suffix == ".png"
    assert first != second
    assert first.read_bytes() == png_bytes()
    with Image.open(second) as image:
        assert image.size == (4, 3)


def test_capture_keeps_jpeg_as_sent(client, keywords, output):
    FakeVideo.frame = jpeg_bytes()
    path = Path(keywords.capture_video_snapshot("cam"))
    assert path.suffix == ".jpg"
    assert path.read_bytes() == jpeg_bytes()


def test_capture_to_relative_and_absolute_filename(client, keywords, output, tmp_path):
    relative = Path(keywords.capture_video_snapshot("cam", filename="screens/boot.png"))
    assert relative.resolve() == (output / "screens" / "boot.png").resolve()
    assert relative.read_bytes() == png_bytes()
    absolute = tmp_path / "elsewhere" / "frame.png"
    assert Path(keywords.capture_video_snapshot("cam", filename=str(absolute))) == absolute
    assert absolute.exists()


def test_capture_from_pil_snapshot(client, keywords, output):
    path = Path(keywords.capture_video_snapshot("image"))
    assert path.suffix == ".png"
    with Image.open(path) as image:
        assert image.size == (5, 5)


def test_capture_errors(client, keywords, output):
    with pytest.raises(Failure, match="has no snapshot_bytes"):
        keywords.capture_video_snapshot("power")
    with pytest.raises(Failure, match="returned int, not an image"):
        keywords.capture_video_snapshot("odd")
    with pytest.raises(Failure, match="not present on this exporter"):
        keywords.capture_video_snapshot("missing")
    FakeVideo.frame = b""
    with pytest.raises(Failure, match="empty snapshot"):
        keywords.capture_video_snapshot("cam")


def test_node_extension_registered_for_video_clients():
    assert "VideoClient" in SNAPSHOT_CLIENTS
    words = [e.method_words for e in extensions_for(VideoClient)]
    assert "Capture Snapshot" in words
    assert "Capture Snapshot" not in [e.method_words for e in extensions_for(OddClient)]


def test_driver_library_offers_prefixed_keyword():
    library = DriverLibrary("cam", client=f"{__name__}.VideoClient", prefix="Camera")
    assert "Camera Capture Snapshot" in library.get_keyword_names()
    assert library.get_keyword_arguments("Camera Capture Snapshot") == [("filename", None), ("embed", True)]


def test_real_video_client_if_installed(keywords, output):
    video = pytest.importorskip("jumpstarter_driver_video.driver")

    class StillVideo(video.VideoInterface, Driver):
        @export
        async def snapshot(self) -> str:
            return b64encode(jpeg_bytes()).decode("ascii")

        @export
        def stream_path(self) -> str:
            return "/stream"

        @export
        async def state(self):
            return {"online": True}

    # VideoInterface (imported above, so unknown to the type checker) provides client()
    with serve(Composite(children={"hdmi": StillVideo()})) as root, use_client(root):  # ty: ignore[call-non-callable]
        assert type(root.children["hdmi"]).__name__ == "VideoClient"
        path = Path(keywords.capture_video_snapshot("hdmi"))
    assert path.suffix == ".jpg" and path.read_bytes() == jpeg_bytes()


SUITE = f"""
*** Settings ***
Library    OperatingSystem
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Driver    cam    client={__name__}.VideoClient    prefix=Camera    AS    cam

*** Test Cases ***
Generic Keyword
    ${{path}}=    Capture Video Snapshot    cam
    File Should Exist    ${{path}}
    Should Start With    ${{path}}    ${{OUTPUT DIR}}

Named File Without Embedding
    ${{path}}=    Capture Video Snapshot    cam    filename=boot.png    embed=False
    Should Be Equal    ${{path}}    ${{OUTPUT DIR}}${{/}}jumpstarter${{/}}boot.png
    File Should Exist    ${{path}}

Node Shortcut
    ${{path}}=    Camera Capture Snapshot    shortcut.png    embed=False
    File Should Exist    ${{path}}
    Camera Capture Snapshot
"""


def test_robot_suite(tmp_path):
    suite = tmp_path / "media.robot"
    suite.write_text(SUITE)
    with serve(Composite(children={"cam": FakeVideo()})) as root, use_client(root):
        rc = robot.run(
            str(suite),
            outputdir=str(tmp_path),
            output=str(tmp_path / "output.xml"),
            report="NONE",
            log="NONE",
            console="none",
        )
    result = ExecutionResult(str(tmp_path / "output.xml"))
    statuses = {test.name: (test.status, test.message) for test in result.suite.tests}
    assert all(status == "PASS" for status, _ in statuses.values()), statuses
    assert len(statuses) == 3 and rc == 0
    folder = tmp_path / "jumpstarter"
    assert (folder / "boot.png").read_bytes() == png_bytes()
    assert (folder / "shortcut.png").exists()
    assert len(list(folder.glob("cam-snapshot-*.png"))) == 2
    # the embedded image links relative to the log
    xml = (tmp_path / "output.xml").read_text()
    assert 'img src="jumpstarter/cam-snapshot-' in xml


def test_output_dir_outside_run(output):
    assert context.output_dir().resolve() == output.resolve()
