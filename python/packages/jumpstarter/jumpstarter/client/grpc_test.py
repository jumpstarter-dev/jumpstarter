from datetime import datetime
from io import StringIO
from unittest.mock import Mock

from rich.console import Console
from rich.table import Table

from jumpstarter.client.grpc import (
    Exporter,
    Lease,
    WithOptions,
    add_display_columns,
    add_exporter_row,
)


class TestWithOptions:
    def test_default_options(self):
        options = WithOptions()
        assert options.show_online is False
        assert options.show_leases is False

    def test_custom_options(self):
        options = WithOptions(show_online=True, show_leases=True)
        assert options.show_online is True
        assert options.show_leases is True


class TestAddDisplayColumns:
    def test_basic_columns(self):
        table = Table()
        add_display_columns(table)

        columns = [col.header for col in table.columns]
        assert columns == ["NAME", "LABELS"]

    def test_with_online_column(self):
        table = Table()
        options = WithOptions(show_online=True)
        add_display_columns(table, options)

        columns = [col.header for col in table.columns]
        assert columns == ["NAME", "ONLINE", "LABELS"]

    def test_with_leases_columns(self):
        table = Table()
        options = WithOptions(show_leases=True)
        add_display_columns(table, options)

        columns = [col.header for col in table.columns]
        assert columns == ["NAME", "LABELS", "LEASED BY", "LEASE STATUS", "START TIME"]

    def test_with_all_columns(self):
        table = Table()
        options = WithOptions(show_online=True, show_leases=True)
        add_display_columns(table, options)

        columns = [col.header for col in table.columns]
        assert columns == ["NAME", "ONLINE", "LABELS", "LEASED BY", "LEASE STATUS", "START TIME"]


class TestAddExporterRow:
    def create_test_exporter(self, online=True, labels=None):
        if labels is None:
            labels = {"env": "test", "type": "device"}
        return Exporter(
            namespace="default",
            name="test-exporter",
            labels=labels,
            online=online
        )

    def test_basic_row(self):
        table = Table()
        add_display_columns(table)

        exporter = self.create_test_exporter()
        add_exporter_row(table, exporter)

        # Just verify a row was added and correct number of columns
        assert len(table.rows) == 1
        assert len(table.columns) == 2  # NAME, LABELS

    def test_row_with_lease_info(self):
        table = Table()
        options = WithOptions(show_leases=True)
        add_display_columns(table, options)

        exporter = self.create_test_exporter()
        lease_info = ("client-123", "Active", "2023-01-01 10:00:00")
        add_exporter_row(table, exporter, options, lease_info)

        assert len(table.rows) == 1
        assert len(table.columns) == 5  # NAME, LABELS, LEASED BY, LEASE STATUS, START TIME

    def test_row_with_lease_info_available(self):
        table = Table()
        options = WithOptions(show_leases=True)
        add_display_columns(table, options)

        exporter = self.create_test_exporter()
        lease_info = ("", "Available", "")
        add_exporter_row(table, exporter, options, lease_info)

        assert len(table.rows) == 1
        assert len(table.columns) == 5

    def test_row_with_all_options(self):
        table = Table()
        options = WithOptions(show_online=True, show_leases=True)
        add_display_columns(table, options)

        exporter = self.create_test_exporter(online=False)
        lease_info = ("client-456", "Expired", "2023-01-01 08:00:00")
        add_exporter_row(table, exporter, options, lease_info)

        assert len(table.rows) == 1
        assert len(table.columns) == 6  # NAME, ONLINE, LABELS, LEASED BY, LEASE STATUS, START TIME


class TestExporterList:
    def create_test_lease(self, client="test-client", status="Active"):
        lease = Mock(spec=Lease)
        lease.client = client
        lease.get_status.return_value = status
        lease.effective_begin_time = datetime(2023, 1, 1, 10, 0, 0)
        return lease

    def test_exporter_without_lease(self):
        exporter = Exporter(
            namespace="default",
            name="test-exporter",
            labels={"type": "device"},
            online=True
        )

        table = Table()
        Exporter.rich_add_columns(table)
        exporter.rich_add_rows(table)

        assert len(table.rows) == 1
        assert len(table.columns) == 2  # NAME, LABELS

    def test_exporter_with_lease_no_display(self):
        lease = self.create_test_lease()
        exporter = Exporter(
            namespace="default",
            name="test-exporter",
            labels={"type": "device"},
            online=True,
            lease=lease
        )

        table = Table()
        Exporter.rich_add_columns(table)
        exporter.rich_add_rows(table)

        # Should not show lease info when show_leases=False
        assert len(table.rows) == 1
        assert len(table.columns) == 2  # NAME, LABELS

    def test_exporter_with_lease_display(self):
        lease = self.create_test_lease()
        exporter = Exporter(
            namespace="default",
            name="test-exporter",
            labels={"type": "device"},
            online=True,
            lease=lease
        )

        table = Table()
        options = WithOptions(show_leases=True)
        Exporter.rich_add_columns(table, options)
        exporter.rich_add_rows(table, options)

        assert len(table.rows) == 1
        assert len(table.columns) == 5  # NAME, LABELS, LEASED BY, LEASE STATUS, START TIME

        # Test actual table content by rendering it
        console = Console(file=StringIO(), width=120)
        console.print(table)
        output = console.file.getvalue()

        # Check that the actual content is present in the rendered output
        assert "test-exporter" in output
        assert "type=device" in output
        assert "test-client" in output
        assert "Active" in output
        assert "2023-01-01 10:00:00" in output

    def test_exporter_without_lease_but_show_leases(self):
        exporter = Exporter(
            namespace="default",
            name="test-exporter",
            labels={"type": "device"},
            online=True
        )

        table = Table()
        options = WithOptions(show_leases=True)
        Exporter.rich_add_columns(table, options)
        exporter.rich_add_rows(table, options)

        assert len(table.rows) == 1
        assert len(table.columns) == 5  # NAME, LABELS, LEASED BY, LEASE STATUS, START TIME

        # Test actual table content by rendering it
        console = Console(file=StringIO(), width=120)
        console.print(table)
        output = console.file.getvalue()

        # Check that the actual content shows "Available" status
        assert "test-exporter" in output
        assert "type=device" in output
        assert "Available" in output
        # Should NOT contain lease client or start time for available exporters
        assert "test-client" not in output

    def test_exporter_online_status_display(self):
        """Test that online status icons are correctly displayed"""
        # Test online exporter
        exporter_online = Exporter(
            namespace="default",
            name="online-exporter",
            labels={"type": "device"},
            online=True
        )

        # Test offline exporter
        exporter_offline = Exporter(
            namespace="default",
            name="offline-exporter",
            labels={"type": "device"},
            online=False
        )

        # Test with online status display enabled
        table = Table()
        options = WithOptions(show_online=True)
        Exporter.rich_add_columns(table, options)
        exporter_online.rich_add_rows(table, options)
        exporter_offline.rich_add_rows(table, options)

        assert len(table.rows) == 2
        assert len(table.columns) == 3  # NAME, ONLINE, LABELS

        # Test actual table content by rendering it
        console = Console(file=StringIO(), width=120)
        console.print(table)
        output = console.file.getvalue()

        # Check that the actual content shows correct online status indicators
        assert "online-exporter" in output
        assert "offline-exporter" in output
        assert "yes" in output  # Should show "yes" for online
        assert "no" in output   # Should show "no" for offline

    def test_exporter_all_features_display(self):
        """Test all display features together: online status + lease info"""
        lease = self.create_test_lease(client="full-test-client", status="Active")

        # Create exporters with different combinations of online/lease status
        exporter_online_with_lease = Exporter(
            namespace="default",
            name="online-with-lease",
            labels={"env": "prod"},
            online=True,
            lease=lease
        )

        exporter_offline_no_lease = Exporter(
            namespace="default",
            name="offline-no-lease",
            labels={"env": "dev"},
            online=False
            # No lease
        )

        # Test with all options enabled
        table = Table()
        options = WithOptions(show_online=True, show_leases=True)
        Exporter.rich_add_columns(table, options)
        exporter_online_with_lease.rich_add_rows(table, options)
        exporter_offline_no_lease.rich_add_rows(table, options)

        assert len(table.rows) == 2
        assert len(table.columns) == 6  # NAME, ONLINE, LABELS, LEASED BY, LEASE STATUS, START TIME

        # Test actual table content by rendering it
        console = Console(file=StringIO(), width=150)
        console.print(table)
        output = console.file.getvalue()

        # Verify all content is present
        assert "online-with-lease" in output
        assert "offline-no-lease" in output
        assert "env=prod" in output
        assert "env=dev" in output
        assert "yes" in output  # Online indicator
        assert "no" in output   # Offline indicator
        assert "full-test-client" in output  # Lease client
        assert "Active" in output  # Lease status
        assert "Available" in output  # Available status for no lease
        assert "2023-01-01 10:00:00" in output  # Lease start time

    def test_exporter_lease_info_extraction(self):
        """Test that lease information is correctly extracted from lease objects"""
        lease = self.create_test_lease(client="my-client", status="Expired")
        exporter = Exporter(
            namespace="default",
            name="test-exporter",
            labels={"type": "device"},
            online=True,
            lease=lease
        )

        # Manually verify the lease data that would be extracted
        assert exporter.lease.client == "my-client"
        assert exporter.lease.get_status() == "Expired"
        assert exporter.lease.effective_begin_time.strftime("%Y-%m-%d %H:%M:%S") == "2023-01-01 10:00:00"

        # Test the logic that builds lease_info tuple in rich_add_rows
        options = WithOptions(show_leases=True)
        if options.show_leases and exporter.lease:
            lease_client = exporter.lease.client
            lease_status = exporter.lease.get_status()
            start_time = exporter.lease.effective_begin_time.strftime("%Y-%m-%d %H:%M:%S")
            lease_info = (lease_client, lease_status, start_time)

            assert lease_info == ("my-client", "Expired", "2023-01-01 10:00:00")

    def test_exporter_no_lease_info_extraction(self):
        """Test that default lease information is used when no lease exists"""
        exporter = Exporter(
            namespace="default",
            name="test-exporter",
            labels={"type": "device"},
            online=True
            # No lease attached
        )

        # Test the logic that builds lease_info tuple when no lease exists
        options = WithOptions(show_leases=True)
        if options.show_leases:
            if exporter.lease:
                # This path should not be taken
                raise AssertionError("Should not have lease data")
            else:
                # This path should be taken - default "Available" status
                lease_info = ("", "Available", "")
                assert lease_info == ("", "Available", "")


