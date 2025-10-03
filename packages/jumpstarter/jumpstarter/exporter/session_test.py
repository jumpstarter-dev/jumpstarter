"""Tests for session GetReport with descriptions"""

from google.protobuf import empty_pb2

from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver


class SimpleDriver(Driver):
    """Simple test driver"""

    @classmethod
    def client(cls):
        return "jumpstarter.client.DriverClient"


class CompositeDriver_(Driver):
    """Simple composite driver for testing"""

    @classmethod
    def client(cls):
        return "jumpstarter.client.DriverClient"


def test_get_report_includes_descriptions():
    """Test that GetReport includes descriptions for drivers that have them"""
    # Create drivers with and without descriptions
    driver_with_desc = SimpleDriver(description="Custom test driver")
    driver_without_desc = SimpleDriver()

    root = CompositeDriver_(
        children={
            "with_desc": driver_with_desc,
            "without_desc": driver_without_desc,
        }
    )

    with serve(root) as _:
        # Get the raw report response

        from jumpstarter.exporter.session import Session

        # Create session manually to access GetReport
        session = Session(
            uuid=root.uuid,
            labels=root.labels,
            root_device=root,
        )

        # Call GetReport
        import asyncio
        response = asyncio.run(session.GetReport(empty_pb2.Empty(), None))

        # Build a map of uuid -> report for easy lookup
        reports_by_uuid = {r.uuid: r for r in response.reports}

        # Verify driver with description has it in its report
        assert str(driver_with_desc.uuid) in reports_by_uuid
        report_with_desc = reports_by_uuid[str(driver_with_desc.uuid)]
        assert hasattr(report_with_desc, 'description')
        assert report_with_desc.description == "Custom test driver"

        # Verify driver without description doesn't have the field set
        assert str(driver_without_desc.uuid) in reports_by_uuid
        report_without_desc = reports_by_uuid[str(driver_without_desc.uuid)]
        # Optional field - either not set or empty string
        assert not getattr(report_without_desc, 'description', None)


def test_client_receives_description():
    """Test that client receives description from GetReport"""
    driver = SimpleDriver(description="Test description")

    with serve(driver) as client:
        # Description is passed during init from GetReport
        assert client.description == "Test description"


def test_cli_uses_description_or_default():
    """Test that CLI uses description from GetReport or falls back to default"""
    # Test with description set
    driver_with_desc = SimpleDriver(description="Custom CLI description")
    with serve(driver_with_desc) as client:
        # Simulate what cli() method would do
        help_text = client.description or "Default help text"
        assert help_text == "Custom CLI description"

    # Test without description
    driver_without_desc = SimpleDriver()
    with serve(driver_without_desc) as client:
        help_text = client.description or "Default help text"
        assert help_text == "Default help text"


def test_multiple_drivers_with_descriptions():
    """Test that multiple drivers can have different descriptions"""
    power = SimpleDriver(description="Power control")
    serial = SimpleDriver(description="Serial communication")
    storage = SimpleDriver(description="Storage management")
    plain = SimpleDriver()  # No description

    root = CompositeDriver_(
        children={
            "power": power,
            "serial": serial,
            "storage": storage,
            "plain": plain,
        }
    )

    with serve(root) as client:
        # Each child should have its description from GetReport
        assert client.children['power'].description == "Power control"
        assert client.children['serial'].description == "Serial communication"
        assert client.children['storage'].description == "Storage management"
        assert client.children['plain'].description is None


def test_empty_description_not_included():
    """Test that empty strings are not included in descriptions map"""
    driver = SimpleDriver(description="")

    with serve(driver) as _:
        from jumpstarter.exporter.session import Session

        session = Session(
            uuid=driver.uuid,
            labels=driver.labels,
            root_device=driver,
        )

        import asyncio
        response = asyncio.run(session.GetReport(empty_pb2.Empty(), None))

        # Empty string should not be included in the report
        reports_by_uuid = {r.uuid: r for r in response.reports}
        assert str(driver.uuid) in reports_by_uuid
        report = reports_by_uuid[str(driver.uuid)]
        # Empty description should not be set
        assert not getattr(report, 'description', None)

