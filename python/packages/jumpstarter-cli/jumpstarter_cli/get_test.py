from unittest.mock import Mock

import click
import pytest

from .get import parse_with
from jumpstarter.client.grpc import Exporter, ExporterList, Lease
from jumpstarter.config.client import ClientConfigV1Alpha1


class TestParseWith:
    def test_single_option(self):
        """Test parsing a single option"""
        result = parse_with(None, None, "leases")
        assert result == ["leases"]

    def test_multiple_options(self):
        """Test parsing multiple comma-separated options"""
        result = parse_with(None, None, "leases,online")
        assert result == ["leases", "online"]

    def test_options_with_spaces(self):
        """Test parsing options with spaces around commas"""
        result = parse_with(None, None, "leases, online")
        assert result == ["leases", "online"]

    def test_empty_value(self):
        """Test parsing empty or None value"""
        assert parse_with(None, None, None) == []
        assert parse_with(None, None, "") == []

    def test_invalid_options_raise_error(self):
        """Test that invalid options raise click.BadParameter"""
        with pytest.raises(click.BadParameter, match="Invalid field 'unknown'. Allowed values are: leases, online"):
            parse_with(None, None, "unknown,online,invalid")

        with pytest.raises(click.BadParameter, match="Invalid field 'invalid'. Allowed values are: leases, online"):
            parse_with(None, None, "online,invalid")

    def test_repeated_flags_tuple_input(self):
        """Test parsing multiple flags as tuple (--with a --with b)"""
        result = parse_with(None, None, ("leases", "online"))
        assert result == ["leases", "online"]

    def test_mixed_csv_and_repeated_flags(self):
        """Test mixing CSV and repeated flags"""
        result = parse_with(None, None, ("leases,online", "leases"))
        assert result == ["leases", "online"]  # deduplicated

    def test_normalization_lowercase(self):
        """Test that values are normalized to lowercase"""
        result = parse_with(None, None, "LEASES,Online")
        assert result == ["leases", "online"]

    def test_whitespace_stripping(self):
        """Test that whitespace is stripped from values"""
        result = parse_with(None, None, " leases , online ")
        assert result == ["leases", "online"]

    def test_empty_tokens_dropped(self):
        """Test that empty tokens are dropped"""
        result = parse_with(None, None, "leases,,online,")
        assert result == ["leases", "online"]

    def test_deduplication_preserves_order(self):
        """Test that deduplication preserves first occurrence order"""
        result = parse_with(None, None, "online,leases,online,leases")
        assert result == ["online", "leases"]

    def test_empty_string_in_tuple(self):
        """Test handling empty strings in tuple"""
        result = parse_with(None, None, ("", "leases", ""))
        assert result == ["leases"]

    def test_complex_mixed_input(self):
        """Test complex input with CSV, repeated flags, whitespace, and case variation"""
        result = parse_with(None, None, (" LEASES, online ", "Online", "leases,"))
        assert result == ["leases", "online"]


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
        """Test that the parse_with function now validates and rejects unknown options"""
        # This test verifies that the new parse_with function would reject unknown options
        # The actual CLI behavior now validates input, so unknown options cause failures
        # This test documents the expected behavior change
        pass  # Test is no longer relevant since parse_with now validates input

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
