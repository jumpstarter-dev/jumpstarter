from __future__ import annotations

from pathlib import Path

import pytest

from jumpstarter.testing.checks import (
    discover_example_files,
    find_inline_code_blocks,
    find_unused_examples,
    find_unused_examples_in_docs,
)
from jumpstarter.testing.examples import instantiate_yaml_example, validate_example

PACKAGES_DIR = Path(__file__).resolve().parent.parent / "packages"
DOCS_SOURCE_DIR = Path(__file__).resolve().parent.parent / "docs" / "source"


def _discover_driver_packages() -> list[Path]:
    return sorted(pkg for pkg in PACKAGES_DIR.iterdir() if pkg.is_dir() and pkg.name.startswith("jumpstarter-driver-"))


def _example_file_params() -> list[pytest.param]:
    params = []
    for pkg in _discover_driver_packages():
        examples_dir = pkg / "examples"
        for path, kind in discover_example_files(examples_dir):
            rel = path.relative_to(pkg)
            params.append(pytest.param(path, kind, id=f"{pkg.name}/{rel}"))
    docs_examples_root = DOCS_SOURCE_DIR / "examples"
    if docs_examples_root.is_dir():
        for subdir in sorted(docs_examples_root.iterdir()):
            if not subdir.is_dir() or subdir.name == "tests":
                continue
            for path, kind in discover_example_files(subdir):
                rel = path.relative_to(docs_examples_root)
                params.append(pytest.param(path, kind, id=f"docs-examples/{rel}"))
    return params


def _driver_params() -> list[pytest.param]:
    return [pytest.param(pkg, id=pkg.name) for pkg in _discover_driver_packages() if (pkg / "examples").is_dir()]


def _driver_inline_params() -> list[pytest.param]:
    params = []
    for pkg in _discover_driver_packages():
        if not (pkg / "examples").is_dir():
            continue
        marks = ()
        if pkg.name in DRIVER_INLINE_BASH_XFAIL:
            marks = (pytest.mark.xfail(reason="known inline bash/shell code blocks to be converted", strict=True),)
        params.append(pytest.param(pkg, id=pkg.name, marks=marks))
    return params


DRIVER_INLINE_BASH_XFAIL = frozenset(
    {
        "jumpstarter-driver-adb",
        "jumpstarter-driver-androidemulator",
        "jumpstarter-driver-composite",
        "jumpstarter-driver-doip",
        "jumpstarter-driver-dut-network",
        "jumpstarter-driver-energenie",
        "jumpstarter-driver-esp32",
        "jumpstarter-driver-flashers",
        "jumpstarter-driver-http-power",
        "jumpstarter-driver-mitmproxy",
        "jumpstarter-driver-noyito-relay",
        "jumpstarter-driver-pyserial",
        "jumpstarter-driver-snmp",
        "jumpstarter-driver-someip",
        "jumpstarter-driver-ssh",
        "jumpstarter-driver-ssh-mitm",
        "jumpstarter-driver-ssh-mount",
        "jumpstarter-driver-stlink-msd",
        "jumpstarter-driver-tmt",
        "jumpstarter-driver-uds-can",
        "jumpstarter-driver-uds-doip",
        "jumpstarter-driver-vnc",
        "jumpstarter-driver-xcp",
        "jumpstarter-driver-yepkit",
    }
)

DOCS_INLINE_CODE_XFAIL = frozenset(
    {
        "getting-started/configuration/authentication.md",
        "getting-started/configuration/files.md",
        "getting-started/configuration/loading-order.md",
        "getting-started/guides/examples/scripting.md",
        "getting-started/guides/examples/testing.md",
        "getting-started/guides/integration-patterns/cicd.md",
        "getting-started/guides/setup/direct-mode.md",
        "getting-started/guides/setup/distributed-mode.md",
        "getting-started/guides/setup/local-mode.md",
        "reference/package-apis/drivers/adb.md",
        "reference/package-apis/drivers/androidemulator.md",
        "reference/package-apis/drivers/doip.md",
        "reference/package-apis/drivers/dut-network.md",
        "reference/package-apis/drivers/energenie.md",
        "reference/package-apis/drivers/esp32.md",
        "reference/package-apis/drivers/flashers.md",
        "reference/package-apis/drivers/http-power.md",
        "reference/package-apis/drivers/mitmproxy.md",
        "reference/package-apis/drivers/noyito-relay.md",
        "reference/package-apis/drivers/pyserial.md",
        "reference/package-apis/drivers/snmp.md",
        "reference/package-apis/drivers/someip.md",
        "reference/package-apis/drivers/ssh-mitm.md",
        "reference/package-apis/drivers/ssh-mount.md",
        "reference/package-apis/drivers/ssh.md",
        "reference/package-apis/drivers/stlink-msd.md",
        "reference/package-apis/drivers/tmt.md",
        "reference/package-apis/drivers/uds-can.md",
        "reference/package-apis/drivers/uds-doip.md",
        "reference/package-apis/drivers/vnc.md",
        "reference/package-apis/drivers/xcp.md",
        "reference/package-apis/drivers/yepkit.md",
    }
)


def _docs_markdown_params() -> list[pytest.param]:
    if not DOCS_SOURCE_DIR.is_dir():
        return []
    params = []
    for md_file in sorted(DOCS_SOURCE_DIR.rglob("*.md")):
        rel = str(md_file.relative_to(DOCS_SOURCE_DIR))
        if rel.startswith("contributing/jeps/"):
            continue
        marks = ()
        if rel in DOCS_INLINE_CODE_XFAIL:
            marks = (pytest.mark.xfail(reason="known inline code blocks to be converted", strict=True),)
        params.append(pytest.param(md_file, id=rel, marks=marks))
    return params


def _docs_example_dirs() -> list[pytest.param]:
    examples_root = DOCS_SOURCE_DIR / "examples"
    if not examples_root.is_dir():
        return []
    dirs = sorted(d for d in examples_root.iterdir() if d.is_dir() and d.name != "tests")
    return [pytest.param(d, id=d.name) for d in dirs if any(discover_example_files(d))]


@pytest.mark.parametrize("path,kind", _example_file_params())
def test_example_validates(path, kind):
    validate_example(path, kind)


@pytest.mark.parametrize("path,kind", _example_file_params())
def test_example_instantiates(path, kind):
    if kind != "yaml":
        pytest.skip("not a YAML example")
    instantiate_yaml_example(path)


@pytest.mark.parametrize("pkg", _driver_params())
def test_no_unused_examples(pkg):
    examples_dir = pkg / "examples"
    readme_path = pkg / "README.md"
    unused = find_unused_examples(examples_dir, readme_path)
    assert not unused, f"{pkg.name}: example files not referenced in README.md: {[p.name for p in unused]}"


@pytest.mark.parametrize("pkg", _driver_inline_params())
def test_no_inline_code_blocks(pkg):
    readme_path = pkg / "README.md"
    violations = find_inline_code_blocks(readme_path)
    assert not violations, (
        f"{pkg.name}: README.md has inline code blocks that should use literalinclude: "
        f"{[f'line {line}: {desc}' for line, desc in violations]}"
    )


@pytest.mark.parametrize("md_file", _docs_markdown_params())
def test_docs_no_inline_code_blocks(md_file):
    violations = find_inline_code_blocks(md_file)
    rel = md_file.relative_to(DOCS_SOURCE_DIR)
    assert not violations, (
        f"docs/{rel} has inline code blocks that should use literalinclude: "
        f"{[f'line {line}: {desc}' for line, desc in violations]}"
    )


@pytest.mark.parametrize("examples_dir", _docs_example_dirs())
def test_docs_no_unused_examples(examples_dir):
    docs_markdown_files = sorted(DOCS_SOURCE_DIR.rglob("*.md"))
    unused = find_unused_examples_in_docs(examples_dir, docs_markdown_files)
    assert not unused, (
        f"docs/source/examples/{examples_dir.name}: example files not referenced in any doc: {[p.name for p in unused]}"
    )
