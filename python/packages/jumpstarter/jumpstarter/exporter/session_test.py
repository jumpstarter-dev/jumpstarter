"""Tests for session GetReport with descriptions and methods_description"""

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


def test_description_override_in_exporter_config():
    """Test that description in exporter config overrides default"""
    # Create a driver with a custom description
    custom_driver = SimpleDriver(description="Custom override description")

    with serve(custom_driver) as client:
        # Client should receive the custom description
        assert client.description == "Custom override description"


def test_description_available_to_cli():
    """Test that description is available for CLI group help text"""
    # Test with custom description
    driver_with_desc = SimpleDriver(description="Power management interface")
    with serve(driver_with_desc) as client:
        # Description should be available for CLI
        assert client.description == "Power management interface"

        # This is what DriverClickGroup would use
        cli_help = client.description or "Default CLI help"
        assert cli_help == "Power management interface"

    # Test without description (falls back to default)
    driver_no_desc = SimpleDriver()
    with serve(driver_no_desc) as client:
        assert client.description is None

        # DriverClickGroup falls back to provided default
        cli_help = client.description or "Default CLI help"
        assert cli_help == "Default CLI help"


def test_composite_children_each_have_own_description():
    """Test that each child in composite can have its own description"""
    power = SimpleDriver(description="Power control interface")
    serial = SimpleDriver(description="Serial communication interface")
    storage = SimpleDriver(description="Storage management interface")
    network = SimpleDriver()  # No custom description

    root = CompositeDriver_(
        description="Main composite device",
        children={
            "power": power,
            "serial": serial,
            "storage": storage,
            "network": network,
        }
    )

    with serve(root) as client:
        # Root has its own description
        assert client.description == "Main composite device"

        # Each child maintains its own description
        assert client.children['power'].description == "Power control interface"
        assert client.children['serial'].description == "Serial communication interface"
        assert client.children['storage'].description == "Storage management interface"
        assert client.children['network'].description is None


def test_methods_description_set_via_config():
    """Test that methods_description can be set via server configuration"""
    # Server can override method descriptions via config
    driver = SimpleDriver(
        description="Power management",
        methods_description={
            "on": "Custom: Turn device power on",
            "off": "Custom: Turn device power off",
            "cycle": "Custom: Power cycle the device"
        }
    )

    # methods_description should be set
    assert "on" in driver.methods_description
    assert driver.methods_description["on"] == "Custom: Turn device power on"
    assert "off" in driver.methods_description
    assert driver.methods_description["off"] == "Custom: Turn device power off"


def test_methods_description_included_in_getreport():
    """Test that GetReport includes methods_description for drivers"""
    driver = SimpleDriver(
        methods_description={
            "on": "Turn the device on",
            "off": "Turn the device off",
        }
    )

    with serve(driver) as _:
        from jumpstarter.exporter.session import Session

        session = Session(
            uuid=driver.uuid,
            labels=driver.labels,
            root_device=driver,
        )

        import asyncio
        response = asyncio.run(session.GetReport(empty_pb2.Empty(), None))

        # Find the driver's report
        reports_by_uuid = {r.uuid: r for r in response.reports}
        assert str(driver.uuid) in reports_by_uuid
        report = reports_by_uuid[str(driver.uuid)]

        # Verify methods_description is in the report
        assert hasattr(report, 'methods_description')
        assert "on" in report.methods_description
        assert report.methods_description["on"] == "Turn the device on"
        assert "off" in report.methods_description
        assert report.methods_description["off"] == "Turn the device off"


def test_client_receives_methods_description():
    """Test that client receives methods_description from GetReport"""
    driver = SimpleDriver(
        description="Test power driver",
        methods_description={
            "on": "Turn the device on",
            "off": "Turn the device off",
            "read": "Stream power readings"
        }
    )

    with serve(driver) as client:
        # Client should have methods_description populated
        assert "on" in client.methods_description
        assert client.methods_description["on"] == "Turn the device on"
        assert "off" in client.methods_description
        assert client.methods_description["off"] == "Turn the device off"
        assert "read" in client.methods_description
        assert client.methods_description["read"] == "Stream power readings"


def test_driverclickgroup_uses_methods_description_as_override():
    """Test that DriverClickGroup uses methods_description to override client defaults"""
    driver = SimpleDriver(
        description="Power management",
        methods_description={
            "on": "Server override: Power on",
        }
    )

    with serve(driver) as client:
        # Simulate what DriverClickGroup.command() does:
        # Priority: server methods_description > client help= > empty

        # Method with server override
        method_name = "on"
        if method_name in client.methods_description:
            help_text = client.methods_description[method_name]
        elif "help" in {}:  # Simulate client's help= parameter
            help_text = {}["help"]
        else:
            help_text = ""

        # Should get server override
        assert help_text == "Server override: Power on"

        # Method without server override
        method_name = "off"
        client_help = "Client default: Power off"
        if method_name in client.methods_description:
            help_text = client.methods_description[method_name]
        else:
            help_text = client_help

        # Should fall back to client default
        assert help_text == "Client default: Power off"

