import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_network.driver import TcpNetwork
from jumpstarter_driver_ssh.driver import SSHWrapper

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


def test_ssh_mount_requires_ssh_child():
    with pytest.raises(ConfigurationError, match="'ssh' child is required"):
        SSHMount()


def test_mount_sshfs_not_installed():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value=None):
            with pytest.raises(Exception, match="sshfs is not installed"):
                client.mount("/tmp/test-mount")


def test_mount_sshfs_constructs_correct_args_and_detects_immediate_exit():
    """Verify sshfs test-run args are correct and immediate exit is detected as failure."""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # sshfs already exited
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    # Test run succeeds, then foreground popen exits immediately (simulated)
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 2222))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount", remote_path="/home/user")

                            test_run_args = mock_run.call_args_list[0][0][0]
                            assert test_run_args[0] == "sshfs"
                            assert "testuser@127.0.0.1:/home/user" in test_run_args
                            assert os.path.realpath("/tmp/test-mount") in test_run_args
                            assert "-p" in test_run_args
                            assert "2222" in test_run_args
                            assert "-f" not in test_run_args


def test_mount_sshfs_identity_in_args():
    """Verify IdentityFile option is included in sshfs args when identity is set"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child(ssh_identity=TEST_SSH_KEY)},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount")

                            test_run_args = mock_run.call_args_list[0][0][0]
                            identity_opts = [
                                test_run_args[i + 1] for i in range(len(test_run_args) - 1)
                                if test_run_args[i] == "-o" and test_run_args[i + 1].startswith("IdentityFile=")
                            ]
                            assert len(identity_opts) == 1


def test_mount_sshfs_allow_other_fallback():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    # First test run fails with allow_other, second succeeds
                    mock_run.side_effect = [
                        MagicMock(returncode=1, stdout="", stderr="allow_other: permission denied"),
                        MagicMock(returncode=0, stdout="", stderr=""),  # retry without allow_other
                        MagicMock(returncode=0, stdout="", stderr=""),  # force_umount
                    ]
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount", extra_args=["allow_other"])

                            # First test run should have allow_other (from extra_args)
                            first_call_args = mock_run.call_args_list[0][0][0]
                            assert "allow_other" in first_call_args

                            # Second test run should not have allow_other
                            second_call_args = mock_run.call_args_list[1][0][0]
                            assert "allow_other" not in second_call_args
                            # Verify no orphaned -o flags
                            for i, arg in enumerate(second_call_args):
                                if arg == "-o":
                                    assert i + 1 < len(second_call_args), "Orphaned -o flag found"
                                    assert not second_call_args[i + 1].startswith("-"), \
                                        f"Orphaned -o flag followed by {second_call_args[i + 1]}"


def test_mount_sshfs_generic_failure():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="Connection refused"
                )
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                        with pytest.raises(Exception, match="sshfs mount failed"):
                            client.mount("/tmp/test-mount")

                        # First call is the sshfs test run (should not retry since
                        # error is not allow_other). Second call is _force_umount
                        # in the finally block cleanup.
                        assert mock_run.call_count == 2
                        # Verify the first call was the sshfs test run
                        first_call_args = mock_run.call_args_list[0][0][0]
                        assert first_call_args[0] == "sshfs"


def test_mount_sshfs_direct_constructs_correct_args_and_detects_immediate_exit():
    instance = SSHMount(
        children={"ssh": _make_ssh_child(host="10.0.0.1", port=2222)},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with pytest.raises(Exception, match="sshfs mount failed"):
                            client.mount("/tmp/test-mount", direct=True)

                        test_run_args = mock_run.call_args_list[0][0][0]
                        assert test_run_args[0] == "sshfs"
                        assert "testuser@10.0.0.1:/" in test_run_args
                        assert "-p" in test_run_args
                        assert "2222" in test_run_args


def test_mount_sshfs_direct_fallback_to_portforward():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 3333))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            original_ssh = client.ssh

                            class FakeTcp:
                                def address(self):
                                    raise ValueError("not available")

                            class FakeSsh:
                                def __getattr__(self, name):
                                    if name == "tcp":
                                        return FakeTcp()
                                    return getattr(original_ssh, name)

                            with patch.object(client, 'ssh', FakeSsh()):
                                with pytest.raises(Exception, match="sshfs mount failed"):
                                    client.mount("/tmp/test-mount", direct=True)

                            test_run_args = mock_run.call_args_list[0][0][0]
                            # Should have used port forwarding (port 3333)
                            assert "3333" in test_run_args


def test_mount_foreground_mode():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running when cleanup checks
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired("sshfs", 1),  # First wait (startup check) - still running
            None,  # Second wait (foreground blocking) - exited
            None,  # Third wait (cleanup after terminate) - exited
        ]
        mock_proc.returncode = 0

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc) as mock_popen:
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                    with patch('os.makedirs'):
                        # First call: post-unmount check in _start_sshfs_with_fallback (must be False)
                        # Second call: mount readiness poll (must be True to proceed)
                        # Third call: cleanup check in _run_sshfs finally block
                        with patch('os.path.ismount', side_effect=[False, True, False]):
                            with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                                mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                                mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                                client.mount("/tmp/test-mount", foreground=True)

                                # Should have waited on sshfs (foreground mode)
                                assert mock_proc.wait.call_count >= 2
                                # Port forward should be cleaned up
                                mock_adapter.return_value.__exit__.assert_called()
                                # Verify -f flag is in the Popen args
                                popen_args = mock_popen.call_args[0][0]
                                assert "-f" in popen_args


def test_mount_subshell_mode():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running when cleanup checks
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired("sshfs", 1),  # Startup check - still running
            None,  # Cleanup wait after terminate - exited
        ]
        mock_proc.returncode = 0

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                    with patch('os.makedirs'):
                        # First call: post-unmount check in _start_sshfs_with_fallback (must be False)
                        # Second call: mount readiness poll (must be True to proceed)
                        # Third call: cleanup check in _run_sshfs finally block
                        with patch('os.path.ismount', side_effect=[False, True, False]):
                            with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                                mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                                mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                                with patch.object(client, '_run_subshell') as mock_subshell:
                                    client.mount("/tmp/test-mount")

                                    # Subshell should have been called
                                    resolved = os.path.realpath("/tmp/test-mount")
                                    mock_subshell.assert_called_once_with(resolved, "/")
                                    # sshfs process should be terminated after subshell exits
                                    mock_proc.terminate.assert_called_once()


def test_mount_cleanup_on_failure():
    instance = SSHMount(
        children={"ssh": _make_ssh_child(ssh_identity=TEST_SSH_KEY)},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="Connection refused"
                )
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                        with patch('os.unlink') as mock_unlink:
                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount")

                            # Identity file should be cleaned up on failure
                            # Verify unlink was called with a path ending in _ssh_key
                            assert mock_unlink.called
                            unlink_path = mock_unlink.call_args_list[-1][0][0]
                            assert unlink_path.endswith("_ssh_key")


def test_umount_with_fusermount():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        def _fake_find(name):
            return "/usr/bin/fusermount" if name == "fusermount" else None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount("/tmp/test-mount")

                assert mock_run.called
                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "/usr/bin/fusermount"
                assert "-u" in call_args


def test_umount_with_system_umount_fallback():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value=None):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount("/tmp/test-mount")

                assert mock_run.called
                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "umount"


def test_umount_lazy():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        def _fake_find(name):
            return "/usr/bin/fusermount" if name == "fusermount" else None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount("/tmp/test-mount", lazy=True)

                assert mock_run.called
                call_args = mock_run.call_args[0][0]
                assert "-z" in call_args


def test_umount_failure():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        def _fake_find(name):
            return "/usr/bin/fusermount" if name == "fusermount" else None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not mounted")

                with pytest.raises(Exception, match="Unmount failed"):
                    client.umount("/tmp/test-mount")


def test_cli_has_mount_and_umount_flag():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        cli = client.cli()
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "mountpoint" in result.output.lower() or "MOUNTPOINT" in result.output
        assert "--umount" in result.output
        assert "--foreground" in result.output


def test_cli_dispatches_mount():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
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


def test_cli_dispatches_umount():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        cli = client.cli()
        from click.testing import CliRunner
        runner = CliRunner()

        with patch.object(client, 'umount') as mock_umount:
            result = runner.invoke(cli, ["--umount", "/tmp/test-cli-mount", "--lazy"])
            assert result.exit_code == 0
            mock_umount.assert_called_once_with("/tmp/test-cli-mount", lazy=True)


def test_mount_foreground_keyboard_interrupt():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired("sshfs", 1),  # Startup check - still running
            KeyboardInterrupt(),  # Foreground blocking - user presses Ctrl+C
            None,  # Cleanup wait after terminate
        ]
        mock_proc.returncode = 0

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                    with patch('os.makedirs'):
                        # First call: post-unmount check in _start_sshfs_with_fallback (must be False)
                        # Second call: mount readiness poll (must be True to proceed)
                        # Third call: cleanup check in _run_sshfs finally block
                        with patch('os.path.ismount', side_effect=[False, True, False]):
                            with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                                mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                                mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                                client.mount("/tmp/test-mount", foreground=True)

                                # sshfs should have been terminated
                                mock_proc.terminate.assert_called_once()


def test_umount_passes_timeout():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value=None):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount("/tmp/test-mount")

                # Verify timeout=120 is passed
                assert mock_run.call_args[1].get("timeout") == 120


def test_mount_port_22_omits_p_flag():
    instance = SSHMount(
        children={"ssh": _make_ssh_child(port=22)},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount")

                            test_run_args = mock_run.call_args_list[0][0][0]
                            assert "-p" not in test_run_args


def test_umount_prefers_fusermount3():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        def _fake_find(name):
            if name == "fusermount3":
                return "/usr/bin/fusermount3"
            if name == "fusermount":
                return "/usr/bin/fusermount"
            return None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount("/tmp/test-mount")

                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "/usr/bin/fusermount3"


def test_umount_lazy_macos_uses_force():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value=None):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                with patch('jumpstarter_driver_ssh_mount.client.sys') as mock_sys:
                    mock_sys.platform = "darwin"
                    client.umount("/tmp/test-mount", lazy=True)

                    call_args = mock_run.call_args[0][0]
                    assert "-f" in call_args
                    assert "-l" not in call_args


def test_extra_args_prefixed_with_dash_o():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount", extra_args=["reconnect", "cache=yes"])

                            test_run_args = mock_run.call_args_list[0][0][0]
                            # Each extra arg should be preceded by -o
                            for extra in ["reconnect", "cache=yes"]:
                                idx = test_run_args.index(extra)
                                assert test_run_args[idx - 1] == "-o", \
                                    f"Extra arg '{extra}' not preceded by '-o'"


def test_extra_args_override_default_ssh_options():
    """Verify user-supplied options appear before defaults for first-match-wins semantics"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount(
                                    "/tmp/test-mount",
                                    extra_args=["StrictHostKeyChecking=yes"],
                                )

                            test_run_args = mock_run.call_args_list[0][0][0]
                            # Find positions of both StrictHostKeyChecking options
                            user_idx = None
                            default_idx = None
                            for i, arg in enumerate(test_run_args):
                                if arg == "StrictHostKeyChecking=yes":
                                    user_idx = i
                                elif arg == "StrictHostKeyChecking=no":
                                    default_idx = i

                            assert user_idx is not None, "User option not found in args"
                            assert default_idx is not None, "Default option not found in args"
                            assert user_idx < default_idx, (
                                "User-supplied option must appear before default "
                                "for OpenSSH first-match-wins to work"
                            )


def test_mount_ipv6_address_bracketed():
    """Verify IPv6 addresses are wrapped in brackets in the sshfs remote spec"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child(host="::1", port=22)},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("::1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount")

                            test_run_args = mock_run.call_args_list[0][0][0]
                            remote_spec = test_run_args[1]
                            assert "[::1]" in remote_spec, (
                                f"IPv6 not bracketed in remote spec: {remote_spec}"
                            )


def test_mount_sshfs_not_mounted_after_startup():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired("sshfs", 1),  # Startup check - still running
            None,  # _terminate_proc cleanup after polling loop timeout
            None,  # _terminate_proc in except BaseException handler
        ]
        mock_proc.returncode = 0

        # Make polling loop exit quickly by advancing monotonic time
        call_count = [0]
        def fake_monotonic():
            call_count[0] += 1
            return call_count[0] * 100.0  # Jump far ahead to exceed deadline

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                    with patch('os.makedirs'):
                        with patch('os.path.ismount', return_value=False):
                            monotonic_path = 'jumpstarter_driver_ssh_mount.client.time.monotonic'
                            sleep_path = 'jumpstarter_driver_ssh_mount.client.time.sleep'
                            adapter_path = 'jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter'
                            with patch(monotonic_path, side_effect=fake_monotonic):
                                with patch(sleep_path):
                                    with patch(adapter_path) as mock_adapter:
                                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                                        with pytest.raises(Exception, match="is not mounted"):
                                            client.mount("/tmp/test-mount", foreground=True)

                                        mock_proc.terminate.assert_called()


def test_subshell_bad_shell_raises_click_exception():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.dict(os.environ, {"SHELL": "/nonexistent/shell"}):
            with patch('subprocess.run', side_effect=FileNotFoundError("No such file")):
                with pytest.raises(Exception, match="Shell .* not found"):
                    client._run_subshell("/tmp/test-mount", "/")


def test_subshell_fish_prompt():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.dict(os.environ, {"SHELL": "/usr/bin/fish"}):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                client._run_subshell("/tmp/test-mount", "/")

                mock_run.assert_called_once()
                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "/usr/bin/fish"
                assert "--init-command" in call_args
                # The fish_prompt function should contain (mount) and the arrow
                init_cmd = call_args[call_args.index("--init-command") + 1]
                assert "(mount)" in init_cmd
                assert "fish_prompt" in init_cmd


def test_subshell_bash_inserts_mount_tag():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        jmp_ps1 = "\\w ⚡exporter ➤ "
        with patch.dict(os.environ, {"SHELL": "/bin/bash", "PS1": jmp_ps1}):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                client._run_subshell("/tmp/test-mount", "/")

                mock_run.assert_called_once()
                env_passed = mock_run.call_args[1].get("env", {})
                assert "(mount)➤" in env_passed.get("PS1", "")
                assert "sshfs" not in env_passed.get("PS1", "")


def test_subshell_bash_fallback_prefix():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.dict(os.environ, {"SHELL": "/bin/bash", "PS1": r"\$ "}):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                client._run_subshell("/tmp/test-mount", "/home/user")

                env_passed = mock_run.call_args[1].get("env", {})
                assert env_passed.get("PS1", "").startswith("[sshfs:/home/user]")


def test_subshell_zsh_inserts_mount_tag():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        jmp_ps1 = "%~ ⚡exporter ➤ "
        with patch.dict(os.environ, {"SHELL": "/bin/zsh", "PS1": jmp_ps1}):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                client._run_subshell("/tmp/test-mount", "/")

                mock_run.assert_called_once()
                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "/bin/zsh"
                assert "--no-rcs" in call_args
                assert "-i" in call_args
                env_passed = mock_run.call_args[1].get("env", {})
                assert "(mount)➤" in env_passed.get("PS1", "")


def test_create_temp_identity_file_failure():
    instance = SSHMount(
        children={"ssh": _make_ssh_child(ssh_identity=TEST_SSH_KEY)},
    )

    with serve(instance) as client:
        with patch('os.write', side_effect=OSError("disk full")):
            with patch('os.close') as mock_close:
                with patch('os.unlink') as mock_unlink:
                    with pytest.raises(OSError, match="disk full"):
                        client._create_temp_identity_file()

                    # fd and temp file should be cleaned up
                    assert mock_close.called
                    assert mock_unlink.called


def test_allow_other_comma_separated_removal():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        args = ["sshfs", "user@host:/", "/mnt", "-o", "allow_other,reconnect", "-f"]
        result = client._remove_allow_other(args)
        assert "-o" in result
        idx = result.index("-o")
        assert result[idx + 1] == "reconnect"
        assert "allow_other" not in result[idx + 1]

        # When allow_other is the only option, the entire -o pair is removed
        args2 = ["sshfs", "user@host:/", "/mnt", "-o", "allow_other", "-f"]
        result2 = client._remove_allow_other(args2)
        # No -o flag should remain for that option
        for i, a in enumerate(result2):
            if a == "-o":
                assert result2[i + 1] != "allow_other"


def test_subshell_unknown_shell_fallback():
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.dict(os.environ, {"SHELL": "/bin/dash"}):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                client._run_subshell("/tmp/test-mount", "/")

                mock_run.assert_called_once()
                call_args = mock_run.call_args[0][0]
                assert call_args == ["/bin/dash", "-i"]
