"""Auto-generated pytest base class for ExporterClass example-board.

Do not edit — regenerate with `jmp codegen --test-fixtures`.
"""

from __future__ import annotations

import pytest

from jumpstarter_testing.pytest import JumpstarterTest

from jumpstarter_gen.devices.example_board import ExampleBoardDevice


class ExampleBoardTest(JumpstarterTest):
    """Base class for tests targeting ExporterClass example-board.

    Inherit from this class and use the `device` fixture for typed access.
    Supports both `jmp shell` (via JUMPSTARTER_HOST) and lease acquisition
    (via `selector` class variable).
    """

    selector = "jumpstarter.dev/exporter-class=example-board"

    @pytest.fixture(scope="class")
    def device(self, client) -> ExampleBoardDevice:
        """Create a typed ExampleBoardDevice from the connected client."""
        return ExampleBoardDevice(client)
