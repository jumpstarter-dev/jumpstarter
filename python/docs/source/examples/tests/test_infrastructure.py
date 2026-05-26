from pathlib import Path


def test_examples_directory_exists():
    examples_dir = Path(__file__).parent.parent
    assert examples_dir.is_dir()
    assert (examples_dir / "introduction").is_dir()


def test_conftest_provides_examples_root(examples_root):
    assert examples_root.is_dir()
    assert examples_root.name == "examples"


def test_conftest_provides_introduction_dir(examples_root):
    intro_dir = examples_root / "introduction"
    assert intro_dir.is_dir()
