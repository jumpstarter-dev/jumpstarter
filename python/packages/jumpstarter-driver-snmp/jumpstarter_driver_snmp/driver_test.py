from unittest.mock import MagicMock, patch

import pytest
from pysnmp.entity import config as snmp_config

from jumpstarter_driver_snmp.driver import AuthProtocol, PrivProtocol, SNMPServer


class MockMibObject:
    def getInstIdFromIndices(self, *args):
        return (1, 3, 6)


def setup_mock_snmp_engine():
    mock_engine = MagicMock()
    mock_builder = MagicMock()

    mock_entry = MockMibObject()
    mock_builder.import_symbols.return_value = [mock_entry]
    mock_engine.get_mib_builder.return_value = mock_builder

    mock_engine.transport_dispatcher = MagicMock()
    mock_engine.transport_dispatcher.start = MagicMock()
    mock_engine.transport_dispatcher.stop = MagicMock()

    return mock_engine


@pytest.mark.parametrize(
    "auth_config",
    [
        {
            "user": "usr-no-auth",
            "auth_protocol": AuthProtocol.NONE,
            "auth_key": None,
            "priv_protocol": PrivProtocol.NONE,
            "priv_key": None,
            "expected_args_len": 2,  # only user and engine args for noAuth
        },
        {
            "user": "usr-md5-none",
            "auth_protocol": AuthProtocol.MD5,
            "auth_key": "authkey1",
            "priv_protocol": PrivProtocol.NONE,
            "priv_key": None,
            "expected_args_len": 4,  # engine, user, auth_protocol, auth_key
        },
        {
            "user": "usr-sha-des",
            "auth_protocol": AuthProtocol.SHA,
            "auth_key": "authkey1",
            "priv_protocol": PrivProtocol.DES,
            "priv_key": "privkey1",
            "expected_args_len": 6,  # engine, user, auth_protocol, auth_key, priv_protocol, priv_key
        },
    ],
)
def test_snmp_auth_configurations(auth_config):
    """Test different SNMP authentication configurations"""
    with (
        patch("pysnmp.entity.config.add_v3_user") as mock_add_user,
        patch("pysnmp.entity.engine.SnmpEngine", return_value=setup_mock_snmp_engine()),
        patch("pysnmp.entity.config.add_target_parameters"),
        patch("pysnmp.entity.config.add_target_address"),
        patch("pysnmp.entity.config.add_transport"),
    ):
        server = SNMPServer(
            host="localhost",
            user=auth_config["user"],
            plug=1,
            auth_protocol=auth_config["auth_protocol"],
            auth_key=auth_config["auth_key"],
            priv_protocol=auth_config["priv_protocol"],
            priv_key=auth_config["priv_key"],
        )

        server._setup_snmp()

        args, _ = mock_add_user.call_args

        assert len(args) == auth_config["expected_args_len"]

        assert args[1] == auth_config["user"]

        if auth_config["auth_protocol"] != AuthProtocol.NONE:
            if auth_config["auth_protocol"] == AuthProtocol.MD5:
                expected_auth = snmp_config.USM_AUTH_HMAC96_MD5
            else:
                expected_auth = snmp_config.USM_AUTH_HMAC96_SHA

            assert args[2] == expected_auth
            assert args[3] == auth_config["auth_key"]

        if auth_config["priv_protocol"] != PrivProtocol.NONE:
            if auth_config["priv_protocol"] == PrivProtocol.DES:
                expected_priv = snmp_config.USM_PRIV_CBC56_DES
            else:
                expected_priv = snmp_config.USM_PRIV_CFB128_AES

            assert args[4] == expected_priv
            assert args[5] == auth_config["priv_key"]


@patch("pysnmp.entity.config.add_v3_user")
@patch("pysnmp.entity.engine.SnmpEngine")
def test_power_on_command(mock_engine, mock_add_user):
    """Test power on command execution"""
    mock_engine.return_value = setup_mock_snmp_engine()

    with (
        patch("pysnmp.entity.rfc3413.cmdgen.SetCommandGenerator.send_varbinds") as mock_send,
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
        patch("asyncio.new_event_loop"),
        patch("asyncio.set_event_loop"),
        patch("pysnmp.entity.config.add_target_parameters"),
        patch("pysnmp.entity.config.add_target_address"),
        patch("pysnmp.entity.config.add_transport"),
    ):
        server = SNMPServer(host="localhost", user="testuser", plug=1)

        def side_effect(*args):
            callback = args[-1]
            callback(None, None, None, None, None, [], None)

        mock_send.side_effect = side_effect

        result = server.on()
        assert "Power ON command sent successfully" in result
        mock_send.assert_called_once()


@patch("pysnmp.entity.config.add_v3_user")
@patch("pysnmp.entity.engine.SnmpEngine")
def test_power_off_command(mock_engine, mock_add_user):
    """Test power off command execution"""
    mock_engine.return_value = setup_mock_snmp_engine()

    with (
        patch("pysnmp.entity.rfc3413.cmdgen.SetCommandGenerator.send_varbinds") as mock_send,
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
        patch("asyncio.new_event_loop"),
        patch("asyncio.set_event_loop"),
        patch("pysnmp.entity.config.add_target_parameters"),
        patch("pysnmp.entity.config.add_target_address"),
        patch("pysnmp.entity.config.add_transport"),
    ):
        server = SNMPServer(host="localhost", user="testuser", plug=1)

        def side_effect(*args):
            callback = args[-1]
            callback(None, None, None, None, None, [], None)

        mock_send.side_effect = side_effect

        result = server.off()
        assert "Power OFF command sent successfully" in result
        mock_send.assert_called_once()
