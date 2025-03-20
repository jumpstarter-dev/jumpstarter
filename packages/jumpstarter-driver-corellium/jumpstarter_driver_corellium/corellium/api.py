from typing import Optional

import requests

from .types import *
from .exceptions import CorelliumApiException


class ApiClient:
    """
    Corellium ReST API client used by the Corellium driver.
    """
    session: Session
    req: requests.Session = field(init=False)

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
        data = {
            'apiToken': self.token
        }

        try:
            res = requests.post(f'{self.baseurl}/v1/auth/login', json=data)
            res.raise_for_status()
            data = res.json()
        # except (requests.exceptions.RequestException, requests.exceptions.HTTPError) as e:
        # except requests.exceptions.HTTPError as e:
        except Exception as e:
            raise CorelliumApiException(str(e))

        self.session = Session(**data)
        self.req.headers.update(self.session.as_header())

    def get_project(self, project_ref: str = 'Default Project') -> Optional[Project]:
        """
        Retrieve a project based on project_ref, which is either its id or name.
        """
        try:
            res = self.req.get(f'{self.baseurl}/v1/projects')
            res.raise_for_status()
            projects = res.json()
        except requests.exceptions.RequestException as e:
            raise CorelliumApiException(str(e))

        for project in projects:
            if project['name'] == project_ref or project['id'] == project_ref:
                return Project(id=project['id'], name=project['name'])

        return None

    def get_device(self, model: str) -> Optional[Device]:
        """
        Get a device spec from Corellium's list based on the model name.

        A device object is used to create a new virtual instance.
        """
        try:
            res = self.req.get(f'{self.baseurl}/v1/models')
            res.raise_for_status()
            devices = res.json()
        except requests.exceptions.RequestException as e:
            raise CorelliumApiException(str(e))

        for device in devices:
            if device['model'] == model:
                return Device(**device)

        return None

    def create_instance(self, name: str, project: Project, device: Device,
                        os_version: str = '1.0', os_build: str = 'Critical Application Monitor (Baremetal)') -> Instance:
        """
        Create a new virtual instance from a device spec.
        """
        data = {
            'name': name,
            'project': project.id,
            'flavor': device.flavor,
            'os': os_version,
            'osbuild': os_build,
        }

        try:
            res = self.req.post(f'{self.baseurl}/v1/instances', json=data)
            res.raise_for_status()
            instance = res.json()
        except requests.exceptions.RequestException as e:
            raise CorelliumApiException(str(e))

        return Instance(**instance)

    def get_instance(self, instance_ref: str) -> Optional[Instance]:
        """
        Retrieve an existing instance by its name.

        Return None if it does not exist.
        """
        try:
            res = self.req.get(f'{self.baseurl}/v1/instances')
            res.raise_for_status()
            instances = res.json()
        except requests.exceptions.RequestException as e:
            raise CorelliumApiException(str(e))

        for instance in instances:
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
        data = {
            'state': instance_state
        }

        try:
            res = self.req.put(f'{self.baseurl}/v1/instances/{instance.id}/state', json=data)
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise CorelliumApiException(str(e))

    def destroy_instance(self, instance: Instance) -> None:
        """
        Delete a virtual instance.

        Does not return anything since Corellium's API return a HTTP 204 response.
        """
        try:
            res = self.req.delete(f'{self.baseurl}/v1/instances/{instance.id}')
            res.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise CorelliumApiException(str(e))
