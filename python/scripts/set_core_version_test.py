import pytest
import set_core_version


@pytest.fixture
def pyproject(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "jumpstarter-core"\nversion = "0.0.0"\n\n[tool.maturin]\nfeatures = []\n')
    return path


@pytest.mark.parametrize(
    ("version", "tag", "expected"),
    [
        ("0.10.0", "v0.10.0", "0.10.0"),
        ("0.10.0rc1", "v0.10.0-rc.1", "0.10.0rc1"),
        ("0.10.0.dev113+ga56b4d2ab", None, "0.10.0.dev113+ga56b4d2ab"),
    ],
)
def test_stamps_the_release_version(pyproject, version, tag, expected):
    assert set_core_version.set_version(pyproject, version, tag) == expected
    assert f'version = "{expected}"' in pyproject.read_text()
    assert "features = []" in pyproject.read_text()


def test_rejects_a_version_that_does_not_match_the_tag(pyproject):
    with pytest.raises(SystemExit, match="does not match release tag v0.10.1"):
        set_core_version.set_version(pyproject, "0.10.0", "v0.10.1")
    assert 'version = "0.0.0"' in pyproject.read_text()


def test_requires_a_version_field(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "jumpstarter-core"\n')
    with pytest.raises(SystemExit, match="No version field"):
        set_core_version.set_version(path, "0.10.0")


def test_checked_in_version_is_a_placeholder():
    text = set_core_version.PYPROJECT.read_text(encoding="utf-8")
    assert 'version = "0.0.0"' in text
