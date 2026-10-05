from pathlib import Path
from types import SimpleNamespace

from hatch_pin_jumpstarter import PinJumpstarter


class _Hook(PinJumpstarter):
    def __init__(self, root):
        self._root = root

    root = property(lambda self: self._root)
    target_name = "sdist"
    metadata = SimpleNamespace(version="1.2.3")


def test_pinned_pyproject_replaces_original(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["jumpstarter", "requests>=2"]\n')
    build_data = {"force_include": {"/checkout/pyproject.toml": "pyproject.toml"}}
    hook = _Hook(str(tmp_path))

    hook.initialize("standard", build_data)
    try:
        [source] = [s for s, t in build_data["force_include"].items() if t == "pyproject.toml"]
        content = Path(source).read_text()
        assert "jumpstarter==1.2.3" in content
        assert "requests>=2" in content
    finally:
        hook.finalize("standard", build_data, "")
