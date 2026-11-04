import dataclasses
from pathlib import Path

from pydantic import BaseModel
from robot.utils import DotDict

from . import context
from .convert import to_robot


class Inner(BaseModel):
    voltage: float
    tags: list[str] = []


class Outer(BaseModel):
    name: str
    inner: Inner
    readings: list[Inner] = []


@dataclasses.dataclass
class Result:
    rc: int
    stdout: str
    extra: dict


def test_pydantic_model_becomes_nested_dotdict():
    value = to_robot(Outer(name="x", inner=Inner(voltage=5.0, tags=["a"]), readings=[Inner(voltage=1.0)]))
    assert isinstance(value, DotDict)
    assert value.name == "x"
    assert value.inner.voltage == 5.0
    assert value["inner"]["tags"] == ["a"]
    assert value.readings[0].voltage == 1.0


def test_dataclass_becomes_dotdict():
    value = to_robot(Result(rc=0, stdout="hi", extra={"nested": {"deep": 1}}))
    assert isinstance(value, DotDict)
    assert value.rc == 0
    assert value.stdout == "hi"
    assert value.extra.nested.deep == 1


def test_dataclass_class_itself_is_unchanged():
    assert to_robot(Result) is Result


def test_list_of_models():
    value = to_robot([Inner(voltage=1.0), Inner(voltage=2.0)])
    assert [v.voltage for v in value] == [1.0, 2.0]
    assert all(isinstance(v, DotDict) for v in value)


def test_plain_mapping_becomes_dotdict():
    value = to_robot({"a": {"b": 1}})
    assert isinstance(value, DotDict)
    assert value.a.b == 1


def test_dotdict_returned_as_is():
    value = DotDict(a=1)
    assert to_robot(value) is value


def test_scalars_and_bytes_unchanged():
    for value in (1, "text", None, b"\x00\x01", 1.5):
        assert to_robot(value) is value


def test_generator_cut_at_max_items_and_closed():
    state = {"produced": 0, "closed": False}

    def stream():
        try:
            while True:
                state["produced"] += 1
                yield Inner(voltage=state["produced"])
        finally:
            state["closed"] = True

    value = to_robot(stream(), max_items=3)
    assert [v.voltage for v in value] == [1.0, 2.0, 3.0]
    assert state["closed"] is True
    assert state["produced"] == 3


def test_short_generator():
    assert to_robot((i for i in range(2)), max_items=10) == [0, 1]


class FakeImage:
    """Looks like a PIL image to to_robot: module under PIL. and a save method."""

    __module__ = "PIL.Image"

    def __init__(self):
        self.saved_to = None

    def save(self, path):
        self.saved_to = Path(path)
        Path(path).write_bytes(b"\x89PNG")


def test_image_saved_through_callback(tmp_path):
    image = FakeImage()
    target = tmp_path / "shot.png"
    assert to_robot(image, save_image=lambda img: (img.save(target), target)[1]) == str(target)
    assert image.saved_to == target


def test_image_without_callback_is_unchanged():
    image = FakeImage()
    assert to_robot(image) is image


def test_context_to_robot_saves_images_under_output_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # outside a run, the output directory is ./jumpstarter
    path = Path(context.to_robot(FakeImage(), stem="snap shot"))
    assert path.resolve().parent == (tmp_path / "jumpstarter").resolve()
    assert path.name.startswith("snap-shot-")
    assert path.suffix == ".png"
    assert path.read_bytes() == b"\x89PNG"


def test_context_to_robot_limits_generators():
    assert context.to_robot(iter_forever(), max_items=2) == [0, 1]


def iter_forever():
    n = 0
    while True:
        yield n
        n += 1


def test_dataclass_holding_a_live_object_is_returned_as_is():

    import threading

    @dataclasses.dataclass
    class Stream:
        lock: threading.Lock

    stream = Stream(threading.Lock())
    assert to_robot(stream) is stream


def test_plain_nested_dataclass_still_converts():


    @dataclasses.dataclass
    class Inner:
        port: int

    @dataclasses.dataclass
    class Outer:
        host: str
        inner: Inner

    result = to_robot(Outer("h", Inner(5)))
    assert result.inner.port == 5
