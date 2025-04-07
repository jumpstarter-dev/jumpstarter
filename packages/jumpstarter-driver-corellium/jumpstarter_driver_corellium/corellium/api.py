from typing import Optional

import corellium_api
from corellium_api import Instance, Model, Project


class ApiClient:
    """
    Corellium ReST API client used by the Corellium driver.
    """

    def __init__(self, host: str, token: str) -> None:
        """
        Initializes a new client, containing a
        """
        self.host = host

        configuration = corellium_api.Configuration(host=self.baseurl)
        configuration.access_token = token
        self.api = corellium_api.CorelliumApi(corellium_api.ApiClient(configuration))

    @property
    def baseurl(self) -> str:
        """
        Return the baseurl path for API calls.
        """
        return f"https://{self.host}/api"

    async def get_project(self, project_ref: str = "Default Project") -> Optional[Project]:
        """
        Retrieve a project based on project_ref, which is either its id or name.
        """

        projects = await self.api.v1_get_projects()
        for project in projects:
            if project.name == project_ref or project.id == project_ref:
                return project

        return None

    async def get_device(self, model: str) -> Optional[Model]:
        """
        Get a device spec from Corellium's list based on the model name.

        A device object is used to create a new virtual instance.
        """

        models = await self.api.v1_get_models()
        for device in models:
            if device.model == model:
                return device

        return None

    async def create_instance(
        self, name: str, project: Project, device: Model, os_version: str, os_build: str
    ) -> Instance:
        """
        Create a new virtual instance from a device spec.
        """

        return await self.api.v1_create_instance(
            corellium_api.InstanceCreateOptions(
                name=name,
                project=project.id,
                flavor=device.flavor,
                os=os_version,
                osbuild=os_build,
            )
        )

    async def get_instance(self, instance_ref: str) -> Optional[Instance]:
        """
        Retrieve an existing instance by its name.

        Return None if it does not exist.
        """
        instances = await self.api.v1_get_instances()
        for instance in instances:
            if instance.name == instance_ref or instance.id == instance_ref:
                return instance

        return None

    async def set_instance_state(self, instance: Instance, instance_state: str) -> None:
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

        await self.api.v1_set_instance_state(
            instance.id,
            corellium_api.V1SetStateBody(state=instance_state),
        )

    async def destroy_instance(self, instance: Instance) -> None:
        """
        Delete a virtual instance.

        Does not return anything since Corellium's API return a HTTP 204 response.
        """

        await self.api.v1_delete_instance(instance.id)
