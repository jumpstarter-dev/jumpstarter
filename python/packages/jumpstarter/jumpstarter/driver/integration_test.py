"""Integration tests for JEP-0001: Protobuf Introspection (Phases 1a, 2).

These tests verify that DriverInterface + @export work together in
realistic driver/client scenarios using the serve() test harness.
"""

import asyncio
from abc import abstractmethod
from collections.abc import AsyncGenerator

import pytest
from google.protobuf import empty_pb2

from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, DriverInterface, export, exportstream
from jumpstarter.driver.decorators import MARKER_TYPE_INFO, CallType
from jumpstarter.exporter.session import Session


# ---------------------------------------------------------------------------
# Test interfaces and drivers
# ---------------------------------------------------------------------------

class PowerInterface(DriverInterface):
    """A power control interface for testing."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @abstractmethod
    async def on(self) -> None: ...

    @abstractmethod
    async def off(self) -> None: ...

    @abstractmethod
    async def status(self) -> str: ...


class MockPower(PowerInterface, Driver):
    """Concrete power driver for testing."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._state = "off"

    @export
    async def on(self) -> None:
        self._state = "on"

    @export
    async def off(self) -> None:
        self._state = "off"

    @export
    async def status(self) -> str:
        return self._state


class SensorInterface(DriverInterface):
    """A sensor interface with streaming for testing."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @abstractmethod
    async def read_temperature(self) -> float: ...


class MockSensor(SensorInterface, Driver):
    """Concrete sensor driver for testing."""

    @export
    async def read_temperature(self) -> float:
        return 25.5


class CompositeDriver(Driver):
    """Composite driver combining power + sensor for testing."""

    @classmethod
    def client(cls):
        return "jumpstarter.client.DriverClient"


# ---------------------------------------------------------------------------
# Integration: DriverInterface + @export metadata
# ---------------------------------------------------------------------------

class TestInterfaceExportIntegration:
    """Verify DriverInterface and @export work together properly."""

    def test_interface_methods_have_type_info(self):
        """All @export methods on an interface-based driver have ExportedMethodInfo."""
        driver = MockPower()
        for method_name in ("on", "off", "status"):
            method = getattr(driver, method_name)
            assert hasattr(method, MARKER_TYPE_INFO), (
                f"{method_name} should have ExportedMethodInfo"
            )

    def test_interface_method_call_types(self):
        """Verify correct call types on interface driver methods."""
        info_on = getattr(MockPower.on, MARKER_TYPE_INFO)
        info_off = getattr(MockPower.off, MARKER_TYPE_INFO)
        info_status = getattr(MockPower.status, MARKER_TYPE_INFO)

        assert info_on.call_type == CallType.UNARY
        assert info_off.call_type == CallType.UNARY
        assert info_status.call_type == CallType.UNARY
        assert info_status.return_type is str

    def test_interface_registered_in_metaclass(self):
        """PowerInterface and SensorInterface should be in the registry."""
        from jumpstarter.driver.interface import DriverInterfaceMeta

        registry = DriverInterfaceMeta._registry
        power_key = f"{PowerInterface.__module__}.{PowerInterface.__qualname__}"
        sensor_key = f"{SensorInterface.__module__}.{SensorInterface.__qualname__}"
        assert power_key in registry
        assert sensor_key in registry


# ---------------------------------------------------------------------------
# Integration: serve() with interface-based drivers
# ---------------------------------------------------------------------------

class TestServeInterfaceDrivers:
    """Verify interface-based drivers work through the serve() harness."""

    def test_single_interface_driver(self):
        """A DriverInterface-based driver can be served and called."""
        driver = MockPower()
        with serve(driver) as client:
            client.call("on")
            result = client.call("status")
            assert result == "on"

            client.call("off")
            result = client.call("status")
            assert result == "off"

    def test_composite_with_interface_drivers(self):
        """Composite driver with interface-based children works through serve()."""
        power = MockPower()
        sensor = MockSensor()
        root = CompositeDriver(
            children={
                "power": power,
                "sensor": sensor,
            }
        )

        with serve(root) as client:
            # Call power methods
            client.children["power"].call("on")
            status = client.children["power"].call("status")
            assert status == "on"

            # Call sensor methods
            temp = client.children["sensor"].call("read_temperature")
            assert temp == 25.5


# ---------------------------------------------------------------------------
# Integration: GetReport with interface-based drivers
# ---------------------------------------------------------------------------

class TestGetReportWithInterfaces:
    """Verify GetReport works with DriverInterface-based drivers."""

    def test_report_includes_interface_driver(self):
        """GetReport should list interface-based drivers in reports."""
        driver = MockPower(description="Test power driver")

        session = Session(
            uuid=driver.uuid,
            labels=driver.labels,
            root_device=driver,
        )

        response = asyncio.run(session.GetReport(empty_pb2.Empty(), None))
        reports_by_uuid = {r.uuid: r for r in response.reports}
        assert str(driver.uuid) in reports_by_uuid
        report = reports_by_uuid[str(driver.uuid)]
        assert report.description == "Test power driver"

    def test_report_composite_interface_drivers(self):
        """GetReport should enumerate all interface-based children."""
        power = MockPower(description="Power unit")
        sensor = MockSensor(description="Temp sensor")
        root = CompositeDriver(
            children={
                "power": power,
                "sensor": sensor,
            }
        )

        session = Session(
            uuid=root.uuid,
            labels=root.labels,
            root_device=root,
        )

        response = asyncio.run(session.GetReport(empty_pb2.Empty(), None))
        reports_by_uuid = {r.uuid: r for r in response.reports}

        # All drivers should be in the report
        assert str(power.uuid) in reports_by_uuid
        assert str(sensor.uuid) in reports_by_uuid
        assert reports_by_uuid[str(power.uuid)].description == "Power unit"
        assert reports_by_uuid[str(sensor.uuid)].description == "Temp sensor"


# ---------------------------------------------------------------------------
# Integration: file_descriptor_proto in reports (Phase 7)
# ---------------------------------------------------------------------------

class TestReportFileDescriptorProto:
    """Verify file_descriptor_proto field is populated in DriverInstanceReport."""

    def test_interface_driver_has_file_descriptor(self):
        """Interface-based driver should include file_descriptor_proto in report."""
        driver = MockPower()
        report = driver.report()
        assert report.file_descriptor_proto is not None
        assert len(report.file_descriptor_proto) > 0

    def test_file_descriptor_is_parseable(self):
        """file_descriptor_proto bytes should parse as FileDescriptorProto."""
        from google.protobuf.descriptor_pb2 import FileDescriptorProto

        driver = MockPower()
        report = driver.report()
        fd = FileDescriptorProto()
        fd.ParseFromString(report.file_descriptor_proto)

        assert fd.syntax == "proto3"
        assert len(fd.service) == 1
        # Service name comes from the interface class found in MRO
        assert len(fd.service[0].name) > 0

    def test_file_descriptor_methods_match_exports(self):
        """FileDescriptorProto service methods should match @export methods."""
        from google.protobuf.descriptor_pb2 import FileDescriptorProto

        driver = MockPower()
        report = driver.report()
        fd = FileDescriptorProto()
        fd.ParseFromString(report.file_descriptor_proto)

        method_names = sorted(m.name for m in fd.service[0].method)
        # Methods should include On, Off, Status (PascalCase)
        assert "On" in method_names
        assert "Off" in method_names
        assert "Status" in method_names

    def test_non_interface_driver_no_descriptor(self):
        """Drivers not extending DriverInterface should have no descriptor."""

        class PlainDriver(Driver):
            @classmethod
            def client(cls):
                return "jumpstarter.client.DriverClient"

        driver = PlainDriver()
        report = driver.report()
        # file_descriptor_proto should be None or empty bytes
        assert not report.file_descriptor_proto

    def test_composite_children_have_descriptors(self):
        """Each interface-based child should have its own file_descriptor_proto."""
        from google.protobuf.descriptor_pb2 import FileDescriptorProto

        power = MockPower()
        sensor = MockSensor()
        root = CompositeDriver(
            children={"power": power, "sensor": sensor}
        )

        session = Session(
            uuid=root.uuid,
            labels=root.labels,
            root_device=root,
        )

        response = asyncio.run(session.GetReport(empty_pb2.Empty(), None))
        reports_by_uuid = {r.uuid: r for r in response.reports}

        # Power driver report should have a parseable descriptor
        power_report = reports_by_uuid[str(power.uuid)]
        assert power_report.file_descriptor_proto
        power_fd = FileDescriptorProto()
        power_fd.ParseFromString(power_report.file_descriptor_proto)
        assert len(power_fd.service) == 1
        power_methods = sorted(m.name for m in power_fd.service[0].method)
        assert "On" in power_methods
        assert "Off" in power_methods

        # Sensor driver report should have a different descriptor
        sensor_report = reports_by_uuid[str(sensor.uuid)]
        assert sensor_report.file_descriptor_proto
        sensor_fd = FileDescriptorProto()
        sensor_fd.ParseFromString(sensor_report.file_descriptor_proto)
        assert len(sensor_fd.service) == 1
        sensor_methods = [m.name for m in sensor_fd.service[0].method]
        assert "ReadTemperature" in sensor_methods
