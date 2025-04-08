from typing import Optional

import requests

from .exceptions import CorelliumApiException
from .types import Device, Instance, Project, Session


class ApiClient:
    """
    Corellium ReST API client used by the Corellium driver.
    """
    session: Session
    req: requests.Session

    def __init__(self, host: str, token: str) -> None:
        """
        Initializes a new client, containing a
        """
        self.host = host
        self.token = token
        self.session = None
        self.req = requests.Session()

    @property
    def baseurl(self) -> str:
        """
        Return the baseurl path for API calls.
        """
        return f'https://{self.host}/api'

    def login(self) -> None:
        """
        Login against Corellium's ReST API.

        Set an internal Session object instance to be used
        in other API calls that require authentication.

        It uses the global requests objects so a new session can be generated.
        """
        data = None
        req_data = {
            'apiToken': self.token
        }

        try:
            res = requests.post(f'{self.baseurl}/v1/auth/login', json=req_data)
            data = res.json()
            res.raise_for_status()
        except (requests.exceptions.RequestException, requests.exceptions.HTTPError) as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

        self.session = Session(**data)
        self.req.headers.update(self.session.as_header())

    def get_project(self, project_ref: str = 'Default Project') -> Optional[Project]:
        """
        Retrieve a project based on project_ref, which is either its id or name.
        """
        data = None

        try:
            res = self.req.get(f'{self.baseurl}/v1/projects')
            data = res.json()
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

        for project in data:
            if project['name'] == project_ref or project['id'] == project_ref:
                return Project(id=project['id'], name=project['name'])

        return None

    def get_device(self, model: str) -> Optional[Device]:
        """
        Get a device spec from Corellium's list based on the model name.

        A device object is used to create a new virtual instance.
        """
        data = None

        try:
            res = self.req.get(f'{self.baseurl}/v1/models')
            data = res.json()
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

        for device in data:
            if device['model'] == model:
                return Device(**device)

        return None

    def create_instance(self, name: str, project: Project, device: Device, os_version: str, os_build: str) -> Instance:
        """
        Create a new virtual instance from a device spec.
        """
        data = None
        req_data = {
            'name': name,
            'project': project.id,
            'flavor': device.flavor,
            'os': os_version,
            'osbuild': os_build,
        }

        try:
            res = self.req.post(f'{self.baseurl}/v1/instances', json=req_data)
            data = res.json()
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

        return Instance(**data)

    def get_instance(self, instance_ref: str) -> Optional[Instance]:
        """
        Retrieve an existing instance by its name.

        Return None if it does not exist.
        """
        data = None

        try:
            res = self.req.get(f'{self.baseurl}/v1/instances')
            data = res.json()
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

        for instance in data:
            if instance['name'] == instance_ref or instance['id'] == instance_ref:
                return Instance(id=instance['id'], state=instance['state'])

        return None

    def set_instance_state(self, instance: Instance, instance_state: str) -> None:
        """
        Set the virtual instance state from corellium.

        Valid instance state values:

        - on
        - off
        - booting
        - deleting
        - creating
        - restoring
        - paused
        - rebooting
        - error
        """
        data = None
        req_data = {
            'state': instance_state
        }

        try:
            res = self.req.put(f'{self.baseurl}/v1/instances/{instance.id}/state', json=req_data)
            data = res.json() if res.status_code != 204 else None
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

    def destroy_instance(self, instance: Instance) -> None:
        """
        Delete a virtual instance.

        Does not return anything since Corellium's API return a HTTP 204 response.
        """
        try:
            res = self.req.delete(f'{self.baseurl}/v1/instances/{instance.id}')
            data = res.json() if res.status_code != 204 else None
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

    def get_instance_console_id(self, instance: Instance, console_name: str) -> Optional[str]:
        """
        Retrieve an instance's console id by its name.

        Return None if it does not exist.
        """
        data = None

        try:
            res = self.req.get(f'{self.baseurl}/v1/instances/{instance.id}')
            data = res.json()
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

        for console in data.get('consoles', []):
            if console['name'] == console_name:
                return console['id']

        return None

    def get_instance_console_url(self, instance: Instance, console_id: str) -> Optional[str]:
        """
        Get a a console URL (websocket) to stream logs from.
        """
        data = None

        try:
            res = self.req.get(f'{self.baseurl}/v1/instances/{instance.id}/console',
                               params={'type': console_id.replace('port-', '')})
            data = res.json()
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            msgerr = data.get('error') if data is not None else str(e)

            raise CorelliumApiException(msgerr) from e

        return data['url']
