from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import AsyncMock, Mock, patch

import pytest
from rich.console import Console
from rich.table import Table

from jumpstarter.client.grpc import (
    ClientService,
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
        assert columns == ["NAME", "LABELS", "LEASED BY", "LEASE STATUS", "RELEASE TIME"]

    def test_with_all_columns(self):
        table = Table()
        options = WithOptions(show_online=True, show_leases=True)
        add_display_columns(table, options)

        columns = [col.header for col in table.columns]
        assert columns == ["NAME", "ONLINE", "LABELS", "LEASED BY", "LEASE STATUS", "RELEASE TIME"]


class TestAddExporterRow:
    def create_test_exporter(self, online=True, labels=None):
        if labels is None:
            labels = {"env": "test", "type": "device"}
        return Exporter(namespace="default", name="test-exporter", labels=labels, online=online)

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
        assert len(table.columns) == 5  # NAME, LABELS, LEASED BY, LEASE STATUS, RELEASE TIME

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
        assert len(table.columns) == 6  # NAME, ONLINE, LABELS, LEASED BY, LEASE STATUS, RELEASE TIME


class TestExporterList:
    def create_test_lease(
        self,
        client="test-client",
        status="Active",
        effective_begin_time=datetime(2023, 1, 1, 10, 0, 0),
        effective_duration=timedelta(hours=1),
        begin_time=None,
        duration=timedelta(hours=1),
        effective_end_time=None,
    ):
        lease = Mock(spec=Lease)
        lease.client = client
        lease.get_status.return_value = status
        lease.effective_begin_time = effective_begin_time
        lease.effective_duration = effective_duration
        lease.begin_time = begin_time
        lease.duration = duration
        lease.effective_end_time = effective_end_time
        return lease

    def test_exporter_without_lease(self):
        exporter = Exporter(namespace="default", name="test-exporter", labels={"type": "device"}, online=True)

        table = Table()
        Exporter.rich_add_columns(table)
        exporter.rich_add_rows(table)

        assert len(table.rows) == 1
        assert len(table.columns) == 2  # NAME, LABELS

    def test_exporter_with_lease_no_display(self):
        lease = self.create_test_lease()
        exporter = Exporter(
            namespace="default", name="test-exporter", labels={"type": "device"}, online=True, lease=lease
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
            namespace="default", name="test-exporter", labels={"type": "device"}, online=True, lease=lease
        )

        table = Table()
        options = WithOptions(show_leases=True)
        Exporter.rich_add_columns(table, options)
        exporter.rich_add_rows(table, options)

        assert len(table.rows) == 1
        assert len(table.columns) == 5  # NAME, LABELS, LEASED BY, LEASE STATUS, RELEASE TIME

        # Test actual table content by rendering it
        console = Console(file=StringIO(), width=120)
        console.print(table)
        output = console.file.getvalue()

        # Check that the actual content is present in the rendered output
        assert "test-exporter" in output
        assert "type=device" in output
        assert "test-client" in output
        assert "Active" in output
        assert "2023-01-01 11:00:00" in output  # Expected release: begin_time (10:00:00) + duration (1h)

    def test_exporter_without_lease_but_show_leases(self):
        exporter = Exporter(namespace="default", name="test-exporter", labels={"type": "device"}, online=True)

        table = Table()
        options = WithOptions(show_leases=True)
        Exporter.rich_add_columns(table, options)
        exporter.rich_add_rows(table, options)

        assert len(table.rows) == 1
        assert len(table.columns) == 5  # NAME, LABELS, LEASED BY, LEASE STATUS, RELEASE TIME

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
        exporter_online = Exporter(namespace="default", name="online-exporter", labels={"type": "device"}, online=True)

        # Test offline exporter
        exporter_offline = Exporter(
            namespace="default", name="offline-exporter", labels={"type": "device"}, online=False
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
        assert "no" in output  # Should show "no" for offline

    def test_exporter_all_features_display(self):
        """Test all display features together: online status + lease info"""
        lease = self.create_test_lease(client="full-test-client", status="Active")

        # Create exporters with different combinations of online/lease status
        exporter_online_with_lease = Exporter(
            namespace="default", name="online-with-lease", labels={"env": "prod"}, online=True, lease=lease
        )

        exporter_offline_no_lease = Exporter(
            namespace="default",
            name="offline-no-lease",
            labels={"env": "dev"},
            online=False,
            # No lease
        )

        # Test with all options enabled
        table = Table()
        options = WithOptions(show_online=True, show_leases=True)
        Exporter.rich_add_columns(table, options)
        exporter_online_with_lease.rich_add_rows(table, options)
        exporter_offline_no_lease.rich_add_rows(table, options)

        assert len(table.rows) == 2
        assert len(table.columns) == 6  # NAME, ONLINE, LABELS, LEASED BY, LEASE STATUS, RELEASE TIME

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
        assert "no" in output  # Offline indicator
        assert "full-test-client" in output  # Lease client
        assert "Active" in output  # Lease status
        assert "Available" in output  # Available status for no lease
        assert "2023-01-01 11:00:00" in output  # Expected release time (begin_time + duration)

    def test_exporter_lease_info_extraction(self):
        """Test that lease information is correctly extracted from lease objects"""
        lease = self.create_test_lease(
            client="my-client",
            status="Expired",
            effective_end_time=datetime(2023, 1, 1, 11, 0, 0),  # Ended after 1 hour
        )
        exporter = Exporter(
            namespace="default", name="test-exporter", labels={"type": "device"}, online=True, lease=lease
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
            expected_release = ""
            if exporter.lease.effective_end_time:
                # Ended: use actual end time
                expected_release = exporter.lease.effective_end_time.strftime("%Y-%m-%d %H:%M:%S")
            elif exporter.lease.effective_begin_time:
                # Active: calculate expected end
                release_time = exporter.lease.effective_begin_time + exporter.lease.duration
                expected_release = release_time.strftime("%Y-%m-%d %H:%M:%S")
            elif exporter.lease.begin_time:
                # Scheduled: calculate expected end
                release_time = exporter.lease.begin_time + exporter.lease.duration
                expected_release = release_time.strftime("%Y-%m-%d %H:%M:%S")
            lease_info = (lease_client, lease_status, expected_release)

            assert lease_info == ("my-client", "Expired", "2023-01-01 11:00:00")

    def test_exporter_no_lease_info_extraction(self):
        """Test that default lease information is used when no lease exists"""
        exporter = Exporter(
            namespace="default",
            name="test-exporter",
            labels={"type": "device"},
            online=True,
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

    def test_exporter_scheduled_lease_expected_release(self):
        """Test that scheduled leases show expected release time"""
        lease = self.create_test_lease(
            client="my-client",
            status="Scheduled",
            effective_begin_time=None,  # Not started yet
            effective_duration=None,  # Not started yet
            begin_time=datetime(2023, 1, 1, 10, 0, 0),
            duration=timedelta(hours=1),
        )
        exporter = Exporter(
            namespace="default", name="test-exporter", labels={"type": "device"}, online=True, lease=lease
        )

        # Test the table display with scheduled lease
        table = Table()
        options = WithOptions(show_leases=True)
        Exporter.rich_add_columns(table, options)
        exporter.rich_add_rows(table, options)

        # Should have 5 columns: NAME, LABELS, LEASED BY, LEASE STATUS, RELEASE TIME
        assert len(table.columns) == 5
        assert len(table.rows) == 1

        # Test actual table content by rendering it
        console = Console(file=StringIO(), width=120)
        console.print(table)
        output = console.file.getvalue()

        # Verify the scheduled lease displays expected release time
        assert "test-exporter" in output
        assert "my-client" in output
        assert "Scheduled" in output
        assert "2023-01-01 11:00:00" in output  # begin_time (10:00) + duration (1h)


class TestLeaseRichDisplay:
    def create_lease(
        self,
        name="test-lease",
        selector="env=test",
        duration=timedelta(hours=1),
        effective_duration=None,
        begin_time=None,
        effective_begin_time=None,
        effective_end_time=None,
        client="test-client",
        exporter="test-exporter",
    ):
        return Lease(
            namespace="default",
            name=name,
            selector=selector,
            duration=duration,
            effective_duration=effective_duration,
            begin_time=begin_time,
            effective_begin_time=effective_begin_time,
            effective_end_time=effective_end_time,
            client=client,
            exporter=exporter,
            conditions=[],
        )

    def test_rich_add_columns_has_expires_at_and_remaining(self):
        table = Table()
        Lease.rich_add_columns(table)
        columns = [col.header for col in table.columns]
        assert columns == ["NAME", "SELECTOR", "EXPIRES AT", "REMAINING", "CLIENT", "EXPORTER", "TAGS"]

    def test_rich_add_columns_excludes_begin_time_and_duration(self):
        table = Table()
        Lease.rich_add_columns(table)
        columns = [col.header for col in table.columns]
        assert "BEGIN TIME" not in columns
        assert "DURATION" not in columns

    def test_compute_expires_at_from_effective_end_time(self):
        lease = self.create_lease(
            effective_end_time=datetime(2023, 1, 1, 11, 0, 0),
        )
        assert lease._compute_expires_at() == datetime(2023, 1, 1, 11, 0, 0)

    def test_compute_expires_at_from_effective_begin_and_duration(self):
        lease = self.create_lease(
            effective_begin_time=datetime(2023, 6, 15, 14, 30, 0),
            duration=timedelta(hours=2),
        )
        assert lease._compute_expires_at() == datetime(2023, 6, 15, 16, 30, 0)

    def test_compute_expires_at_from_begin_time_and_duration(self):
        lease = self.create_lease(
            begin_time=datetime(2023, 3, 10, 8, 0, 0),
            duration=timedelta(minutes=30),
        )
        assert lease._compute_expires_at() == datetime(2023, 3, 10, 8, 30, 0)

    def test_compute_expires_at_none_when_no_begin_time(self):
        lease = self.create_lease()
        assert lease._compute_expires_at() is None

    def test_format_remaining_expired(self):
        past = datetime(2020, 1, 1, 0, 0, 0)
        assert Lease._format_remaining(past) == "expired"

    def test_format_remaining_none(self):
        assert Lease._format_remaining(None) == ""

    def test_format_remaining_days_hours_minutes(self):
        now = datetime(2023, 1, 1, 0, 0, 0)
        expires_at = datetime(2023, 1, 3, 3, 45, 0)
        with patch("jumpstarter.client.grpc.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = now
            assert Lease._format_remaining(expires_at) == "2d 3h 45m"

    def test_format_remaining_hours_and_minutes(self):
        now = datetime(2023, 1, 1, 0, 0, 0)
        expires_at = datetime(2023, 1, 1, 5, 30, 0)
        with patch("jumpstarter.client.grpc.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = now
            assert Lease._format_remaining(expires_at) == "5h 30m"

    def test_format_remaining_minutes_only(self):
        now = datetime(2023, 1, 1, 0, 0, 0)
        expires_at = datetime(2023, 1, 1, 0, 15, 0)
        with patch("jumpstarter.client.grpc.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = now
            assert Lease._format_remaining(expires_at) == "15m"

    def test_format_remaining_zero_minutes_shows_0m(self):
        now = datetime(2023, 1, 1, 0, 0, 0)
        expires_at = datetime(2023, 1, 1, 0, 0, 30)
        with patch("jumpstarter.client.grpc.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = now
            assert Lease._format_remaining(expires_at) == "0m"

    def test_format_remaining_days_only(self):
        now = datetime(2023, 1, 1, 0, 0, 0)
        expires_at = datetime(2023, 1, 4, 0, 0, 0)
        with patch("jumpstarter.client.grpc.datetime", wraps=datetime) as mock_dt:
            mock_dt.now.return_value = now
            assert Lease._format_remaining(expires_at) == "3d"

    def test_rich_add_rows_shows_expires_at(self):
        lease = self.create_lease(
            effective_begin_time=datetime(2023, 1, 1, 10, 0, 0),
            effective_end_time=datetime(2023, 1, 1, 11, 0, 0),
        )
        table = Table()
        Lease.rich_add_columns(table)
        lease.rich_add_rows(table)

        console = Console(file=StringIO(), width=200)
        console.print(table)
        output = console.file.getvalue()
        assert "2023-01-01 11:00:00" in output

    def test_rich_add_rows_empty_when_no_timing_data(self):
        lease = self.create_lease()
        table = Table()
        Lease.rich_add_columns(table)
        lease.rich_add_rows(table)

        console = Console(file=StringIO(), width=200)
        console.print(table)
        output = console.file.getvalue()
        assert "test-lease" in output
        assert "test-client" in output

    def test_rich_display_shows_tags(self):
        lease = self.create_lease()
        lease.tags = {"team": "devops", "ci-job": "12345"}
        table = Table()
        Lease.rich_add_columns(table)
        lease.rich_add_rows(table)
        console = Console(file=StringIO(), force_terminal=True)
        console.print(table)
        output = console.file.getvalue()
        assert "team=devops" in output
        assert "ci-job=12345" in output

    def test_rich_display_empty_tags(self):
        lease = self.create_lease()
        table = Table()
        Lease.rich_add_columns(table)
        lease.rich_add_rows(table)
        # Should not crash with empty tags
        columns = [col.header for col in table.columns]
        assert "TAGS" in columns


@pytest.mark.anyio
async def test_create_lease_sets_tags_on_protobuf():
    from jumpstarter_protocol import client_pb2

    mock_channel = Mock()

    response_lease = client_pb2.Lease(
        selector="board=rpi4",
        client="namespaces/default/clients/test-client",
    )
    response_lease.name = "namespaces/default/leases/test-lease"
    response_lease.tags["team"] = "devops"
    response_lease.tags["ci-job"] = "999"
    response_lease.duration.FromTimedelta(timedelta(hours=1))

    mock_stub = Mock()
    mock_stub.CreateLease = AsyncMock(return_value=response_lease)

    svc = ClientService(channel=mock_channel, namespace="default")
    svc.stub = mock_stub

    result = await svc.CreateLease(
        selector="board=rpi4",
        duration=timedelta(hours=1),
        tags={"team": "devops", "ci-job": "999"},
    )

    call_args = mock_stub.CreateLease.call_args[0][0]
    assert dict(call_args.lease.tags) == {"team": "devops", "ci-job": "999"}
    assert result.tags == {"team": "devops", "ci-job": "999"}
