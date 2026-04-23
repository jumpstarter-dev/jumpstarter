import os
from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_network.driver import TcpNetwork
from jumpstarter_driver_ssh.driver import SSHWrapper

from jumpstarter_driver_ssh_mount.client import MOUNT_POLL_INTERVAL
from jumpstarter_driver_ssh_mount.driver import SSHMount

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

TEST_SSH_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "test-key-content\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


def _make_ssh_child(default_username="testuser", ssh_identity=None, ssh_identity_file=None,
                    host="127.0.0.1", port=22):
    """Helper to create an SSHWrapper driver instance for use as a child of SSHMount."""
    kwargs = {
        "default_username": default_username,
        "children": {"tcp": TcpNetwork(host=host, port=port)},
    }
    if ssh_identity is not None:
        kwargs["ssh_identity"] = ssh_identity
    if ssh_identity_file is not None:
        kwargs["ssh_identity_file"] = ssh_identity_file
    return SSHWrapper(**kwargs)


def _fake_find_executable(name):
    """Return plausible paths per executable name."""
    paths = {
        "sshfs": "/usr/bin/sshfs",
        "fusermount3": "/usr/bin/fusermount3",
        "fusermount": "/usr/bin/fusermount",
    }
    return paths.get(name)


@pytest.fixture
def mount_instance():
    return SSHMount(children={"ssh": _make_ssh_child()})


@pytest.fixture
def mount_instance_with_identity():
    return SSHMount(children={"ssh": _make_ssh_child(ssh_identity=TEST_SSH_KEY)})


@pytest.fixture
def mock_portforward():
    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 2222))
        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)
        yield mock_adapter


@pytest.fixture
def mock_portforward_22():
    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)
        yield mock_adapter


# ---------------------------------------------------------------------------
# Driver configuration tests
# ---------------------------------------------------------------------------

def test_ssh_mount_requires_ssh_child():
    """Test that SSHMount driver requires an ssh child"""
    with pytest.raises(ConfigurationError, match="'ssh' child is required"):
        SSHMount()


# ---------------------------------------------------------------------------
# _build_sshfs_args unit tests (argument construction validated independently)
# ---------------------------------------------------------------------------

def test_build_sshfs_args_basic(mount_instance):
    """Test basic sshfs argument construction"""
    with serve(mount_instance) as client:
        args = client._build_sshfs_args("192.168.1.1", 22, "/mnt/remote", "/", None, None)
        assert args[0] == "sshfs"
        assert "testuser@192.168.1.1:/" in args
        assert "/mnt/remote" in args
        assert "-p" not in args


def test_build_sshfs_args_custom_port(mount_instance):
    """Test sshfs args include -p for non-default port"""
    with serve(mount_instance) as client:
        args = client._build_sshfs_args("192.168.1.1", 2222, "/mnt/remote", "/", None, None)
        assert "-p" in args
        assert "2222" in args


def test_build_sshfs_args_with_identity(mount_instance):
    """Test sshfs args include IdentityFile when identity file is provided"""
    with serve(mount_instance) as client:
        args = client._build_sshfs_args("192.168.1.1", 22, "/mnt/remote", "/",
                                        "/tmp/my_key", None)
        identity_opts = [args[i + 1] for i in range(len(args) - 1)
                         if args[i] == "-o" and args[i + 1].startswith("IdentityFile=")]
        assert len(identity_opts) == 1
        assert identity_opts[0] == "IdentityFile=/tmp/my_key"


def test_build_sshfs_args_allow_other_present(mount_instance):
    """Test sshfs args include allow_other by default"""
    with serve(mount_instance) as client:
        args = client._build_sshfs_args("192.168.1.1", 22, "/mnt/remote", "/", None, None)
        assert "allow_other" in args


def test_build_sshfs_args_with_extra_args(mount_instance):
    """Test extra args are prefixed with -o"""
    with serve(mount_instance) as client:
        args = client._build_sshfs_args("192.168.1.1", 22, "/mnt/remote", "/", None,
                                        ["reconnect", "cache=yes"])
        for extra in ["reconnect", "cache=yes"]:
            idx = args.index(extra)
            assert args[idx - 1] == "-o"


def test_build_sshfs_args_remote_path(mount_instance):
    """Test sshfs args use the correct remote path"""
    with serve(mount_instance) as client:
        args = client._build_sshfs_args("10.0.0.1", 22, "/mnt/remote", "/home/user", None, None)
        assert "testuser@10.0.0.1:/home/user" in args


def test_build_sshfs_args_no_username():
    """Test sshfs args without default username"""
    instance = SSHMount(children={"ssh": _make_ssh_child(default_username="")})
    with serve(instance) as client:
        args = client._build_sshfs_args("10.0.0.1", 22, "/mnt/remote", "/", None, None)
        assert "10.0.0.1:/" in args
        assert not any("@" in a for a in args if ":" in a)


# ---------------------------------------------------------------------------
# Mount workflow tests
# ---------------------------------------------------------------------------

def test_mount_sshfs_not_installed(mount_instance):
    """Test mount fails gracefully when sshfs is not installed"""
    with serve(mount_instance) as client:
        with patch.object(client, '_find_executable', return_value=None):
            with pytest.raises(Exception, match="sshfs is not installed"):
                client.mount("/tmp/test-mount")


def test_mount_sshfs_success(mount_instance, mock_portforward):
    """Test successful sshfs mount via port forwarding with subshell"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_proc.wait.side_effect = [None]

            with pytest.raises(Exception, match="sshfs mount failed"):
                client.mount("/tmp/test-mount", remote_path="/home/user")

            test_run_args = mock_run.call_args_list[0][0][0]
            assert test_run_args[0] == "sshfs"
            assert "testuser@127.0.0.1:/home/user" in test_run_args
            assert os.path.realpath("/tmp/test-mount") in test_run_args
            assert "-p" in test_run_args
            assert "2222" in test_run_args
            assert "-f" not in test_run_args


def test_mount_sshfs_with_identity(mount_instance_with_identity, mock_portforward_22):
    """Test sshfs mount with SSH identity"""
    with serve(mount_instance_with_identity) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_proc.wait.side_effect = [None]

            with pytest.raises(Exception, match="sshfs mount failed"):
                client.mount("/tmp/test-mount")

            test_run_args = mock_run.call_args_list[0][0][0]
            identity_opts = [
                test_run_args[i + 1] for i in range(len(test_run_args) - 1)
                if test_run_args[i] == "-o" and test_run_args[i + 1].startswith("IdentityFile=")
            ]
            assert len(identity_opts) == 1


def test_mount_sshfs_allow_other_fallback(mount_instance, mock_portforward_22):
    """Test sshfs mount falls back when allow_other fails"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
        ):
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="", stderr="allow_other: permission denied"),
                MagicMock(returncode=0, stdout="", stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]
            mock_proc.wait.side_effect = [None]

            with pytest.raises(Exception, match="sshfs mount failed"):
                client.mount("/tmp/test-mount")

            second_call_args = mock_run.call_args_list[1][0][0]
            assert "allow_other" not in second_call_args
            for i, arg in enumerate(second_call_args):
                if arg == "-o":
                    assert i + 1 < len(second_call_args), "Orphaned -o flag found"
                    assert not second_call_args[i + 1].startswith("-"), \
                        f"Orphaned -o flag followed by {second_call_args[i + 1]}"


def test_mount_sshfs_generic_failure(mount_instance, mock_portforward_22):
    """Test mount failure with a non-allow_other error"""
    with serve(mount_instance) as client:
        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('os.makedirs'),
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Connection refused")

            with pytest.raises(Exception, match="sshfs mount failed"):
                client.mount("/tmp/test-mount")

            assert mock_run.call_count == 2
            first_call_args = mock_run.call_args_list[0][0][0]
            assert first_call_args[0] == "sshfs"


def test_mount_sshfs_direct_success():
    """Test sshfs mount using direct TCP address"""
    instance = SSHMount(children={"ssh": _make_ssh_child(host="10.0.0.1", port=2222)})

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_proc.wait.side_effect = [None]

            with pytest.raises(Exception, match="sshfs mount failed"):
                client.mount("/tmp/test-mount", direct=True)

            test_run_args = mock_run.call_args_list[0][0][0]
            assert test_run_args[0] == "sshfs"
            assert "testuser@10.0.0.1:/" in test_run_args
            assert "-p" in test_run_args
            assert "2222" in test_run_args


def test_mount_sshfs_direct_fallback_to_portforward(mount_instance, mock_portforward):
    """Test that direct mount falls back to port forwarding on failure"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_proc.wait.side_effect = [None]

            original_ssh = client.ssh

            class FakeTcp:
                def address(self):
                    raise ValueError("not available")

            class FakeSsh:
                def __getattr__(self, name):
                    if name == "tcp":
                        return FakeTcp()
                    return getattr(original_ssh, name)

            with patch.object(client, 'children', {**client.children, "ssh": FakeSsh()}):
                with pytest.raises(Exception, match="sshfs mount failed"):
                    client.mount("/tmp/test-mount", direct=True)

            test_run_args = mock_run.call_args_list[0][0][0]
            assert "2222" in test_run_args


def test_mount_foreground_mode(mount_instance, mock_portforward_22):
    """Test that foreground flag blocks on sshfs without spawning subshell"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0

        poll_calls = [0]
        def poll_side_effect():
            poll_calls[0] += 1
            if poll_calls[0] >= 3:
                return None
            return None
        mock_proc.poll.side_effect = poll_side_effect

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc) as mock_popen,
            patch('os.makedirs'),
            patch('os.path.ismount', return_value=True),
            patch('jumpstarter_driver_ssh_mount.client.time.sleep'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_proc.wait.return_value = None

            client.mount("/tmp/test-mount", foreground=True)

            assert mock_proc.wait.call_count >= 1
            mock_portforward_22.return_value.__exit__.assert_called()
            popen_args = mock_popen.call_args[0][0]
            assert "-f" in popen_args


def test_mount_subshell_mode(mount_instance, mock_portforward_22):
    """Test that default mode spawns a subshell"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
            patch('os.path.ismount', return_value=True),
            patch('jumpstarter_driver_ssh_mount.client.time.sleep'),
            patch.object(client, '_run_subshell') as mock_subshell,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            client.mount("/tmp/test-mount")

            resolved = os.path.realpath("/tmp/test-mount")
            mock_subshell.assert_called_once_with(resolved, "/")


def test_mount_cleanup_on_failure(mount_instance_with_identity, mock_portforward_22):
    """Test that identity file is cleaned up when mount fails"""
    with serve(mount_instance_with_identity) as client:
        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('os.makedirs'),
            patch('os.unlink') as mock_unlink,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Connection refused")

            with pytest.raises(Exception, match="sshfs mount failed"):
                client.mount("/tmp/test-mount")

            assert mock_unlink.called
            unlink_path = mock_unlink.call_args_list[-1][0][0]
            assert unlink_path.endswith("_ssh_key")


# ---------------------------------------------------------------------------
# Unmount tests
# ---------------------------------------------------------------------------

def test_umount_with_fusermount(mount_instance):
    """Test unmount using fusermount"""
    with serve(mount_instance) as client:
        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            client.umount("/tmp/test-mount")

            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "/usr/bin/fusermount3"
            assert "-u" in call_args


def test_umount_with_system_umount_fallback(mount_instance):
    """Test unmount falls back to system umount when fusermount is not available"""
    with serve(mount_instance) as client:
        with (
            patch.object(client, '_find_executable', return_value=None),
            patch('subprocess.run') as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            client.umount("/tmp/test-mount")

            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "umount"


def test_umount_lazy(mount_instance):
    """Test lazy unmount"""
    with serve(mount_instance) as client:
        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            client.umount("/tmp/test-mount", lazy=True)

            call_args = mock_run.call_args[0][0]
            assert "-z" in call_args


def test_umount_failure(mount_instance):
    """Test unmount failure"""
    with serve(mount_instance) as client:
        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not mounted")

            with pytest.raises(Exception, match="Unmount failed"):
                client.umount("/tmp/test-mount")


def test_umount_prefers_fusermount3(mount_instance):
    """Test that fusermount3 is preferred over fusermount when both are available"""
    with serve(mount_instance) as client:
        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            client.umount("/tmp/test-mount")

            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "/usr/bin/fusermount3"


def test_umount_lazy_macos_uses_force(mount_instance):
    """Test that lazy unmount on macOS uses -f instead of -l"""
    with serve(mount_instance) as client:
        with (
            patch.object(client, '_find_executable', return_value=None),
            patch('subprocess.run') as mock_run,
            patch('jumpstarter_driver_ssh_mount.client.sys') as mock_sys,
        ):
            mock_sys.platform = "darwin"
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            client.umount("/tmp/test-mount", lazy=True)

            call_args = mock_run.call_args[0][0]
            assert "-f" in call_args
            assert "-l" not in call_args


def test_umount_passes_timeout(mount_instance):
    """Test that umount subprocess calls include SUBPROCESS_TIMEOUT"""
    with serve(mount_instance) as client:
        with (
            patch.object(client, '_find_executable', return_value=None),
            patch('subprocess.run') as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            client.umount("/tmp/test-mount")

            assert mock_run.call_args[1].get("timeout") == 120


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

def test_cli_has_mount_and_umount_flag(mount_instance):
    """Test that the CLI exposes mount command with --umount and --foreground flags"""
    with serve(mount_instance) as client:
        cli = client.cli()
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "mountpoint" in result.output.lower() or "MOUNTPOINT" in result.output
        assert "--umount" in result.output
        assert "--foreground" in result.output


def test_cli_dispatches_mount(mount_instance):
    """Test that CLI invocation with a mountpoint dispatches to self.mount()"""
    with serve(mount_instance) as client:
        cli = client.cli()
        from click.testing import CliRunner
        runner = CliRunner()

        with patch.object(client, 'mount') as mock_mount:
            result = runner.invoke(cli, ["/tmp/test-cli-mount", "-r", "/home"])
            assert result.exit_code == 0
            mock_mount.assert_called_once_with(
                "/tmp/test-cli-mount",
                remote_path="/home",
                direct=False,
                foreground=False,
                extra_args=[],
            )


def test_cli_dispatches_umount(mount_instance):
    """Test that CLI invocation with --umount dispatches to self.umount()"""
    with serve(mount_instance) as client:
        cli = client.cli()
        from click.testing import CliRunner
        runner = CliRunner()

        with patch.object(client, 'umount') as mock_umount:
            result = runner.invoke(cli, ["--umount", "/tmp/test-cli-mount", "--lazy"])
            assert result.exit_code == 0
            mock_umount.assert_called_once_with("/tmp/test-cli-mount", lazy=True)


# ---------------------------------------------------------------------------
# Polling / mount-readiness tests
# ---------------------------------------------------------------------------

def test_mount_polling_waits_for_mount(mount_instance, mock_portforward_22):
    """Test that the polling loop waits for os.path.ismount to return True"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0

        ismount_calls = [0]
        def ismount_side_effect(path):
            ismount_calls[0] += 1
            return ismount_calls[0] >= 3

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
            patch('os.path.ismount', side_effect=ismount_side_effect),
            patch('jumpstarter_driver_ssh_mount.client.time.sleep') as mock_sleep,
            patch.object(client, '_run_subshell'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            client.mount("/tmp/test-mount")

            assert mock_sleep.call_count >= 2
            mock_sleep.assert_called_with(MOUNT_POLL_INTERVAL)


def test_mount_polling_timeout(mount_instance, mock_portforward_22):
    """Test that mount fails if mountpoint is never mounted within timeout"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
            patch('os.path.ismount', return_value=False),
            patch('jumpstarter_driver_ssh_mount.client.time.sleep'),
            patch('jumpstarter_driver_ssh_mount.client.MOUNT_POLL_TIMEOUT', 0),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            with pytest.raises(Exception, match="is not mounted"):
                client.mount("/tmp/test-mount", foreground=True)

            mock_proc.terminate.assert_called()


def test_mount_sshfs_not_mounted_after_startup(mount_instance, mock_portforward_22):
    """Test that mount fails if sshfs starts but mountpoint is not actually mounted"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
            patch('os.path.ismount', return_value=False),
            patch('jumpstarter_driver_ssh_mount.client.time.sleep'),
            patch('jumpstarter_driver_ssh_mount.client.MOUNT_POLL_TIMEOUT', 0),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            with pytest.raises(Exception, match="is not mounted"):
                client.mount("/tmp/test-mount", foreground=True)

            mock_proc.terminate.assert_called()


# ---------------------------------------------------------------------------
# Foreground / KeyboardInterrupt tests
# ---------------------------------------------------------------------------

def test_mount_foreground_keyboard_interrupt(mount_instance, mock_portforward_22):
    """Test that KeyboardInterrupt during foreground mode terminates sshfs and unmounts"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.returncode = 0

        mock_proc.wait.side_effect = [
            KeyboardInterrupt(),
            None,
        ]

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
            patch('os.path.ismount', return_value=True),
            patch('jumpstarter_driver_ssh_mount.client.time.sleep'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            client.mount("/tmp/test-mount", foreground=True)

            mock_proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# Extra args and port tests
# ---------------------------------------------------------------------------

def test_extra_args_prefixed_with_dash_o(mount_instance, mock_portforward_22):
    """Test that extra_args are correctly prefixed with -o in sshfs command"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_proc.wait.side_effect = [None]

            with pytest.raises(Exception, match="sshfs mount failed"):
                client.mount("/tmp/test-mount", extra_args=["reconnect", "cache=yes"])

            test_run_args = mock_run.call_args_list[0][0][0]
            for extra in ["reconnect", "cache=yes"]:
                idx = test_run_args.index(extra)
                assert test_run_args[idx - 1] == "-o"


def test_mount_port_22_omits_p_flag(mount_instance, mock_portforward_22):
    """Test that port 22 does not add -p flag to sshfs args"""
    with serve(mount_instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with (
            patch.object(client, '_find_executable', side_effect=_fake_find_executable),
            patch('subprocess.run') as mock_run,
            patch('subprocess.Popen', return_value=mock_proc),
            patch('os.makedirs'),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            mock_proc.wait.side_effect = [None]

            with pytest.raises(Exception, match="sshfs mount failed"):
                client.mount("/tmp/test-mount")

            test_run_args = mock_run.call_args_list[0][0][0]
            assert "-p" not in test_run_args


# ---------------------------------------------------------------------------
# Subshell tests
# ---------------------------------------------------------------------------

def test_subshell_bad_shell_raises_click_exception(mount_instance):
    """Test that _run_subshell raises ClickException when shell binary is not found"""
    with serve(mount_instance) as client:
        with patch.dict(os.environ, {"SHELL": "/nonexistent/shell"}):
            with patch('subprocess.run', side_effect=FileNotFoundError("No such file")):
                with pytest.raises(Exception, match="Shell .* not found"):
                    client._run_subshell("/tmp/test-mount", "/")
