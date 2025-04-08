from unittest.mock import patch

import pytest

from .corellium.exceptions import CorelliumApiException
from .corellium.types import Device, Instance, Project, Session
from .driver import Corellium, CorelliumPower
from jumpstarter.common import exceptions as jmp_exceptions


def test_driver_corellium_init_ok(monkeypatch):
    monkeypatch.setenv('CORELLIUM_API_HOST', 'api-host')
    monkeypatch.setenv('CORELLIUM_API_TOKEN', 'api-token')

    c = Corellium(project_id='1', device_name='jmp', device_flavor='kronos', device_os='1.0')

    assert '1' == c.project_id
    assert 'jmp'  == c.device_name
    assert 'kronos' == c.device_flavor
    assert '1.0' == c.device_os
    assert 'api-host'  == c._api.host
    assert 'api-token' == c._api.token


@pytest.mark.parametrize(
        'env,err',
        [
            (
                {},
                jmp_exceptions.ConfigurationError('Missing "CORELLIUM_API_HOST" environment variable')
            ),
            (
                {'CORELLIUM_API_HOST': '  '},
                jmp_exceptions.ConfigurationError('"CORELLIUM_API_HOST" environment variable is empty')
            ),
            (
                {'CORELLIUM_API_HOST': 'api-host'},
                jmp_exceptions.ConfigurationError('Missing "CORELLIUM_API_TOKEN" environment variable')
            ),
            (
                {'CORELLIUM_API_HOST': 'api-host', 'CORELLIUM_API_TOKEN': '   '},
                jmp_exceptions.ConfigurationError('"CORELLIUM_API_TOKEN" environment variable is empty')
            ),
        ])
def test_driver_corellium_init_error(monkeypatch, env, err):
    monkeypatch.delenv('CORELLIUM_API_HOST', raising=False)
    monkeypatch.delenv('CORELLIUM_API_TOKEN', raising=False)

    for k, v in env.items():
        monkeypatch.setenv(k, v)

    with pytest.raises(type(err)) as e:
        Corellium(project_id='1', device_name='jmp', device_flavor='kronos', device_os='1.0')

    assert str(err) == str(e.value)


def test_driver_api_client_ok(monkeypatch, requests_mock):
    requests_mock.post('https://api-host/api/v1/auth/login',
                       text='{"token": "token", "expiration": "2022-03-20T01:50:10.000Z"}')
    monkeypatch.setenv('CORELLIUM_API_HOST', 'api-host')
    monkeypatch.setenv('CORELLIUM_API_TOKEN', 'api-token')

    c = Corellium(project_id='1', device_name='jmp', device_flavor='kronos', device_os='1.0')

    assert Session('token', '2022-03-20T01:50:10.000Z') == c.api.session


def test_driver_power_on_ok(monkeypatch):
    monkeypatch.setenv('CORELLIUM_API_HOST', 'api-host')
    monkeypatch.setenv('CORELLIUM_API_TOKEN', 'api-token')

    project = Project('1', 'Default Project')
    device = Device(name='rd1ae', type='automotive', flavor='kronos',
                    description='', model='kronos', peripherals=False, quotas={})
    instance = Instance(id='7f4f241c-821f-4219-905f-c3b50b0db5dd', state='on')
    root = Corellium(project_id='1', device_name='jmp', device_flavor='kronos', device_os='1.0')
    power = CorelliumPower(parent=root)

    with (patch.object(root._api, 'login', return_value=None),
          patch.object(root._api, 'get_project', return_value=project),
          patch.object(root._api, 'get_device', return_value=device),
          patch.object(root._api, 'get_instance', side_effect=[None, instance]),
          patch.object(root._api, 'create_instance', return_value=instance)):
        power.on()


@pytest.mark.parametrize('mock_data', [
    ({'login': {'side_effect': CorelliumApiException('login error')}}),
    ({'get_project': {'return_value': None}}),
    ({'get_instance': {'return_value': None}}),
    ({'create_instance': {'side_effect': CorelliumApiException('create error')}}),
])
def test_driver_power_on_error(monkeypatch, mock_data):
    monkeypatch.setenv('CORELLIUM_API_HOST', 'api-host')
    monkeypatch.setenv('CORELLIUM_API_TOKEN', 'api-token')

    project = Project('1', 'Default Project')
    instance = Instance(id='7f4f241c-821f-4219-905f-c3b50b0db5dd', state='on')
    root = Corellium(project_id='1', device_name='jmp', device_flavor='kronos', device_os='1.0')
    power = CorelliumPower(parent=root)

    with pytest.raises((CorelliumApiException, ValueError)):
        with (patch.object(root._api, 'login',
                           **mock_data.get('login', {'return_value': None})),
              patch.object(root._api, 'get_project',
                           **mock_data.get('get_project', {'return_value': project})),
              patch.object(root._api, 'get_instance',
                           **mock_data.get('get_instance', {'return_value': instance})),
              patch.object(root._api, 'create_instance',
                           **mock_data.get('create_instance', {'return_value': instance}))):
            power.off()


def test_driver_power_off_ok(monkeypatch):
    monkeypatch.setenv('CORELLIUM_API_HOST', 'api-host')
    monkeypatch.setenv('CORELLIUM_API_TOKEN', 'api-token')

    project = Project('1', 'Default Project')
    instance = Instance(id='7f4f241c-821f-4219-905f-c3b50b0db5dd', state='on')
    root = Corellium(project_id='1', device_name='jmp', device_flavor='kronos', device_os='1.0')
    power = CorelliumPower(parent=root)

    with (patch.object(root._api, 'login', return_value=None),
          patch.object(root._api, 'get_project', return_value=project),
          patch.object(root._api, 'set_instance_state', return_value=None),
          patch.object(root._api, 'get_instance',
                       side_effect=[instance, Instance(id=instance.id, state='off')])):
        power.off()


@pytest.mark.parametrize('mock_data',[
    ({'login': {'side_effect': CorelliumApiException('login error')}}),
    ({'get_project': {'return_value': None}}),
    ({'get_instance': {'return_value': None}}),
    ({'destroy_instance': {'side_effect': CorelliumApiException('destroy error')}}),
])
def test_driver_power_off_error(monkeypatch, mock_data):
    monkeypatch.setenv('CORELLIUM_API_HOST', 'api-host')
    monkeypatch.setenv('CORELLIUM_API_TOKEN', 'api-token')

    project = Project('1', 'Default Project')
    instance = Instance(id='7f4f241c-821f-4219-905f-c3b50b0db5dd', state='on')
    root = Corellium(project_id='1', device_name='jmp', device_flavor='kronos', device_os='1.0')
    power = CorelliumPower(parent=root)

    with pytest.raises((CorelliumApiException, ValueError)):
        with (patch.object(root._api, 'login',
                           **mock_data.get('login', {'return_value': None})),
              patch.object(root._api, 'get_project',
                           **mock_data.get('get_project', {'return_value': project})),
              patch.object(root._api, 'get_instance',
                           **mock_data.get('get_instance', {'side_effect': [instance, None]})),
              patch.object(root._api, 'destroy_instance',
                           **mock_data.get('destroy_instance', {'return_value': instance}))):
            power.off()
