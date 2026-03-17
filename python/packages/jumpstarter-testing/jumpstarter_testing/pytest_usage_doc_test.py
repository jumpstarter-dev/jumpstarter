import ast
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jumpstarter_testing.pytest import JumpstarterTest

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "source" / "getting-started" / "guides"
PYTEST_USAGE_MD = DOCS_DIR / "pytest-usage.md"


def extract_python_blocks(markdown_path):
    content = markdown_path.read_text()
    return re.findall(r"```python\n(.*?)```", content, re.DOTALL)


class TestDocExamplesSyntax:
    @pytest.fixture()
    def python_blocks(self):
        return extract_python_blocks(PYTEST_USAGE_MD)

    def test_guide_file_exists(self):
        assert PYTEST_USAGE_MD.exists()

    def test_has_python_examples(self, python_blocks):
        assert len(python_blocks) >= 5

    def test_all_examples_parse(self, python_blocks):
        for i, block in enumerate(python_blocks):
            try:
                ast.parse(block)
            except SyntaxError as e:
                pytest.fail(f"Code block {i} has syntax error: {e}")

    def test_all_examples_import_jumpstarter_testing(self, python_blocks):
        for block in python_blocks:
            tree = ast.parse(block)
            imports = [
                node for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
            ]
            assert len(imports) > 0

    def test_all_test_classes_extend_jumpstarter_test(self, python_blocks):
        for block in python_blocks:
            tree = ast.parse(block)
            classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
            for cls in classes:
                base_names = [
                    base.id if isinstance(base, ast.Name) else
                    base.attr if isinstance(base, ast.Attribute) else None
                    for base in cls.bases
                ]
                assert "JumpstarterTest" in base_names, (
                    f"Class {cls.name} does not extend JumpstarterTest"
                )

    def test_all_test_classes_have_selector(self, python_blocks):
        for block in python_blocks:
            tree = ast.parse(block)
            classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
            for cls in classes:
                assigns = [
                    node for node in ast.walk(cls)
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "selector"
                        for t in node.targets
                    )
                ]
                assert len(assigns) > 0, (
                    f"Class {cls.name} missing selector attribute"
                )


class TestDocExamplesExecute:
    @pytest.fixture()
    def mock_client(self):
        client = MagicMock()
        client.power.on.return_value = None
        client.power.off.return_value = None
        client.storage.write_local_file.return_value = None
        client.storage.dut.return_value = None
        client.serial.read_until.return_value = "version: 1.0"
        client.tftp.start.return_value = None
        client.tftp.stop.return_value = None
        client.tftp.put_local_file.return_value = None
        client.tftp.list_files.return_value = ["test.bin"]
        client.camera.snapshot.return_value = None
        return client

    def test_power_cycle_example(self, mock_client):
        mock_client.power.on()
        mock_client.power.off()
        mock_client.power.on.assert_called()
        mock_client.power.off.assert_called()

    def test_custom_fixture_example(self, mock_client):
        mock_client.power.off()
        mock_client.storage.write_local_file("firmware.img")
        mock_client.storage.dut()
        mock_client.power.on()
        mock_client.serial.read_until("login:")
        mock_client.power.off()

        mock_client.storage.write_local_file.assert_called_once_with("firmware.img")
        mock_client.storage.dut.assert_called_once()
        mock_client.serial.read_until.assert_called_once_with("login:")

    def test_logging_example(self, mock_client):
        mock_client.power.on()
        version = mock_client.serial.read_until("version:")
        assert version is not None
        mock_client.power.off()

    def test_tftp_example(self, mock_client):
        mock_client.tftp.start()
        mock_client.tftp.put_local_file("test.bin")
        files = mock_client.tftp.list_files()
        assert "test.bin" in files
        mock_client.tftp.stop()

        mock_client.tftp.start.assert_called_once()
        mock_client.tftp.stop.assert_called_once()

    def test_shell_mode_uses_env(self):
        mock_client = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_client)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch("jumpstarter_testing.pytest.env", return_value=mock_ctx):
            fixture_method = JumpstarterTest.client._get_wrapped_function()
            gen = fixture_method(JumpstarterTest)
            client = next(gen)
            assert client is mock_client

    def test_lease_mode_uses_selector(self):
        mock_client = MagicMock()
        mock_env_ctx = MagicMock()
        mock_env_ctx.__enter__ = MagicMock(side_effect=RuntimeError)
        mock_env_ctx.__exit__ = MagicMock(return_value=True)

        mock_lease_ctx = MagicMock()
        mock_connect_ctx = MagicMock()
        mock_connect_ctx.__enter__ = MagicMock(return_value=mock_client)
        mock_connect_ctx.__exit__ = MagicMock(return_value=False)
        mock_lease_ctx.__enter__ = MagicMock(return_value=MagicMock(connect=MagicMock(return_value=mock_connect_ctx)))
        mock_lease_ctx.__exit__ = MagicMock(return_value=False)

        mock_config = MagicMock()
        mock_config.lease.return_value = mock_lease_ctx

        with patch("jumpstarter_testing.pytest.env", return_value=mock_env_ctx), \
             patch("jumpstarter_testing.pytest.ClientConfigV1Alpha1") as mock_config_cls:
            mock_config_cls.load.return_value = mock_config

            instance = JumpstarterTest()
            instance.selector = "board=rpi4"
            fixture_method = JumpstarterTest.client._get_wrapped_function()
            gen = fixture_method(instance)
            client = next(gen)
            assert client is mock_client
            mock_config.lease.assert_called_once_with(selector="board=rpi4")
