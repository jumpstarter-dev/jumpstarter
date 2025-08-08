from unittest.mock import Mock

from jumpstarter.client.grpc import Exporter, ExporterList, Lease
from jumpstarter.config.client import ClientConfigV1Alpha1


class TestGetExportersLogic:
    def create_test_config(self):
        """Create a mock config for testing"""
        config = Mock(spec=ClientConfigV1Alpha1)
        return config

    def create_test_exporters(self, include_leases=False, include_online_status=False):
        """Create test exporters with optional lease data"""
        exporters = [
            Exporter(
                namespace="default",
                name="exporter-1",
                labels={"type": "device", "env": "test"},
                online=True
            ),
            Exporter(
                namespace="default",
                name="exporter-2",
                labels={"type": "server", "env": "prod"},
                online=False
            )
        ]

        if include_leases:
            # Add lease to first exporter
            lease = Mock(spec=Lease)
            lease.client = "test-client"
            lease.get_status.return_value = "Active"
            lease.effective_begin_time = Mock()
            lease.effective_begin_time.strftime.return_value = "2023-01-01 10:00:00"
            exporters[0].lease = lease

        return ExporterList(
            exporters=exporters,
            next_page_token=None,
            include_online=include_online_status,
            include_leases=include_leases
        )

    def test_with_options_parsing_leases(self):
        """Test that 'leases' in with_options is parsed correctly"""
        with_options = ("leases",)

        include_leases = "leases" in with_options
        include_online = "online" in with_options

        assert include_leases is True
        assert include_online is False

    def test_with_options_parsing_online(self):
        """Test that 'online' in with_options is parsed correctly"""
        with_options = ("online",)

        include_leases = "leases" in with_options
        include_online = "online" in with_options

        assert include_leases is False
        assert include_online is True

    def test_with_options_parsing_both(self):
        """Test that both 'leases' and 'online' in with_options are parsed correctly"""
        with_options = ("leases", "online")

        include_leases = "leases" in with_options
        include_online = "online" in with_options

        assert include_leases is True
        assert include_online is True

    def test_with_options_parsing_empty(self):
        """Test that empty with_options are parsed correctly"""
        with_options = ()

        include_leases = "leases" in with_options
        include_online = "online" in with_options

        assert include_leases is False
        assert include_online is False

    def test_with_options_parsing_unknown(self):
        """Test that unknown options in with_options are ignored"""
        with_options = ("unknown", "online", "invalid")

        include_leases = "leases" in with_options
        include_online = "online" in with_options

        assert include_leases is False  # unknown/invalid options ignored
        assert include_online is True   # valid option processed

    def test_exporter_list_creation_basic(self):
        """Test creating ExporterList with basic exporters"""
        exporters = self.create_test_exporters()

        assert isinstance(exporters, ExporterList)
        assert len(exporters.exporters) == 2
        assert exporters.include_online is False
        assert exporters.include_leases is False

    def test_exporter_list_creation_with_options(self):
        """Test creating ExporterList with various options"""
        exporters = self.create_test_exporters(include_leases=True, include_online_status=True)

        assert isinstance(exporters, ExporterList)
        assert len(exporters.exporters) == 2
        assert exporters.include_online is True
        assert exporters.include_leases is True


class TestGetExportersIntegration:
    """Integration tests for data flow"""

    def test_exporter_to_exporter_list_flow(self):
        """Test the data flow from individual Exporter objects to ExporterList"""
        # Create individual exporters
        exporter1 = Exporter(
            namespace="lab-1",
            name="rpi-device-001",
            labels={"device": "raspberry-pi", "location": "rack-1"},
            online=True
        )
        exporter2 = Exporter(
            namespace="lab-1",
            name="server-001",
            labels={"device": "server", "location": "rack-2"},
            online=False
        )

        # Create ExporterList
        exporter_list = ExporterList(
            exporters=[exporter1, exporter2],
            next_page_token=None,
            include_online=True,
            include_leases=False
        )

        # Verify the list contains the exporters and has correct options
        assert len(exporter_list.exporters) == 2
        assert exporter_list.exporters[0].name == "rpi-device-001"
        assert exporter_list.exporters[1].name == "server-001"
        assert exporter_list.include_online is True
        assert exporter_list.include_leases is False
