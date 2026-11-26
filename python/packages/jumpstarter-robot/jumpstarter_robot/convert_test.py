import dataclasses
from pathlib import Path

from pydantic import BaseModel
from robot.utils import DotDict

from . import context
from .convert import to_robot


class Inner(BaseModel):
    voltage: float


class Outer(BaseModel):
    name: str
    readings: list[Inner] = []


@dataclasses.dataclass
class Result:
    rc: int
    extra: dict


def test_pydantic_model_becomes_nested_dotdict():
    value = to_robot(Outer(name="x", readings=[Inner(voltage=1.0)]))
    assert isinstance(value, DotDict)
    assert value.name == "x"
    assert value.readings[0].voltage == 1.0


def test_dataclass_and_mapping_become_dotdict():
    value = to_robot(Result(rc=0, extra={"nested": {"deep": 1}}))
    assert isinstance(value, DotDict)
    assert value.rc == 0
    assert value.extra.nested.deep == 1


def test_scalars_and_bytes_unchanged():
    for value in (1, "text", None, b"\x00\x01", 1.5):
        assert to_robot(value) is value


def test_generator_cut_at_max_items():
    def stream():
        n = 0
        while True:
            n += 1
            yield Inner(voltage=n)

    assert [v.voltage for v in to_robot(stream(), max_items=3)] == [1.0, 2.0, 3.0]


class FakeImage:
    """Looks like a PIL image to to_robot: module under PIL. and a save method."""

    __module__ = "PIL.Image"

    def save(self, path):
        Path(path).write_bytes(b"\x89PNG")


def test_images_are_saved_under_the_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # outside a run, the output directory is ./jumpstarter
    path = Path(context.to_robot(FakeImage(), stem="snap shot"))
    assert path.resolve().parent == (tmp_path / "jumpstarter").resolve()
    assert path.suffix == ".png"
    assert path.read_bytes() == b"\x89PNG"
    image = FakeImage()
    assert to_robot(image) is image  # without somewhere to save it, unchanged


def test_live_dataclasses_survive_inside_containers():
    @dataclasses.dataclass
    class Connection:
        socket: object

    @dataclasses.dataclass
    class Plain:
        level: int

    connection = Connection(object())
    result = to_robot({"connection": connection, "plain": Plain(1), "list": [connection]})
    assert result.connection is connection
    assert result.plain.level == 1
    assert result.list[0] is connection
