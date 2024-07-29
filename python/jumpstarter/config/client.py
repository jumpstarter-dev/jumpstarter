import os
from dataclasses import dataclass
from typing import Optional

import yaml
from typing_extensions import Self

from .common import CONFIG_API_VERSION, CONFIG_PATH
from .env import JMP_DRIVERS_ALLOW, JMP_ENDPOINT, JMP_TOKEN


@dataclass
class ClientConfigDrivers:
    """Jumpstarter client drivers configuration."""

    allow: Optional[list[str]]
    """A list of allowed driver client packages to load from."""

    unsafe: bool
    """Allow any required driver client packages to load without restriction."""

    def from_dict(values: dict) -> Self:
        """Load the client config driver options from a YAML dict."""
        allow = values.get("allow")
        unsafe = values.get("unsafe", False)

        if isinstance(allow, list) is False:
            raise ValueError(
                "Key 'client.drivers.allow' should be a list of strings.")

        return ClientConfigDrivers(allow, unsafe)


@dataclass
class ClientConfig:
    """A Jumpstarter client configuration."""

    # The directory path for client configs
    CLIENT_CONFIGS_PATH = os.path.expanduser(f"{CONFIG_PATH}/clients")

    CONFIG_KIND = "Client"

    name: str
    """The name of the client config."""

    endpoint: str
    """A Jumpstarter service gRPC endpoint."""

    token: str
    """A valid Jumpstarter access token."""

    drivers: ClientConfigDrivers
    """A client drivers configuration."""

    path: Optional[str] = None
    """The path the config was loaded from."""

    def _get_path(name: str) -> str:
        """Get the regular path of a client config given a name."""

        return f"{ClientConfig.CLIENT_CONFIGS_PATH}/{name}.yaml"

    def ensure_exists():
        """Check if the clients config dir exists, otherwise create it."""
        if os.path.exists(ClientConfig.CLIENT_CONFIGS_PATH) is False:
            os.makedirs(ClientConfig.CLIENT_CONFIGS_PATH)

    def try_from_env() -> Optional[Self]:
        """Attempt to load the config from the environment variables, returns `None` if all are not set."""

        if (
            os.environ.get(JMP_TOKEN) is not None
            and os.environ.get(JMP_ENDPOINT) is not None
            and os.environ.get(JMP_DRIVERS_ALLOW) is not None
        ):
            return ClientConfig.from_env()
        else:
            return None

    def from_env() -> Self:
        """Constructs a client config from environment variables."""

        token = os.environ.get(JMP_TOKEN)
        endpoint = os.environ.get(JMP_ENDPOINT)
        drivers_val = os.environ.get(JMP_DRIVERS_ALLOW)
        allow_unsafe = drivers_val == "UNSAFE"

        if token is None:
            raise ValueError(f"Environment variable '{JMP_TOKEN}' is not set.")
        if endpoint is None:
            raise ValueError(
                f"Environment variable '{JMP_ENDPOINT}' is not set.")

        # Split allowed driver packages as a comma-separated list
        drivers = ClientConfigDrivers(drivers_val.split(
            ",") if allow_unsafe is False else [], allow_unsafe)

        return ClientConfig("default", endpoint, token, drivers, None)

    def from_file(path: str) -> Self:
        """Constructs a client config from a YAML file."""
        name = os.path.basename(path).split(".")[0]

        with open(path) as f:
            config: dict = yaml.safe_load(f)

            api_version = config.get("apiVersion")
            kind = config.get("kind")
            client: Optional[dict] = config.get("client", None)

            if api_version != CONFIG_API_VERSION:
                raise ValueError(
                    f"Incorrect config API version {api_version}, expected {CONFIG_API_VERSION}.")
            if kind != ClientConfig.CONFIG_KIND:
                raise ValueError(
                    f"Invalid config type {kind}, expected '{ClientConfig.CONFIG_KIND}'.")
            if client is None:
                raise ValueError("Config does not contain a 'client' key.")

            token = client.get("token")
            endpoint = client.get("endpoint")
            drivers_val: Optional[dict] = client.get("drivers", None)

            if token is None:
                raise ValueError(
                    "Config does not contain a 'client.token' key.")
            if endpoint is None:
                raise ValueError(
                    "Config does not contain a 'client.endpoint' key.")
            if drivers_val is None:
                raise ValueError(
                    "Config does not contain a 'client.drivers' key.")

            drivers = ClientConfigDrivers.from_dict(drivers_val)

            config = ClientConfig(name, endpoint, token, drivers, path)
            return config

    def load(name: str) -> Self:
        """Load a client config by name."""
        path = ClientConfig._get_path(name)
        if os.path.exists(path) is False:
            raise FileNotFoundError(f"Client config '{path}' does not exist.")

        return ClientConfig.from_file(path)

    def save(config: Self, path: Optional[str] = None):
        """Saves a client config as YAML."""
        value = {
            "apiVersion": CONFIG_API_VERSION,
            "kind": ClientConfig.CONFIG_KIND,
            "client": {
                "endpoint": config.endpoint,
                "token": config.token,
                "drivers": {},
            },
        }

        # Only add the unsafe key if unsafe is true, ignore allow
        if config.drivers.unsafe is True:
            value["client"]["drivers"]["unsafe"] = True
        else:
            value["client"]["drivers"]["allow"] = config.drivers.allow

        # Ensure the clients dir exists
        if path is None:
            ClientConfig.ensure_exists()

        with open(path or ClientConfig._get_path(config.name), "w") as f:
            yaml.safe_dump(value, f, sort_keys=False)

    def exists(name: str) -> bool:
        """Check if a client config exists by name."""
        return os.path.exists(ClientConfig._get_path(name))

    def list() -> list[Self]:
        """List the available client configs."""
        if os.path.exists(ClientConfig.CLIENT_CONFIGS_PATH) is False:
            # Return an empty list if the dir does not exist
            return []

        results = os.listdir(ClientConfig.CLIENT_CONFIGS_PATH)
        # Only accept YAML files in the list
        files = filter(lambda x: x.endswith(".yaml"), results)

        def make_config(file: str):
            path = f"{ClientConfig.CLIENT_CONFIGS_PATH}/{file}"
            return ClientConfig.from_file(path)

        return list(map(make_config, files))

    def delete(name: str):
        """Delete a client config by name."""
        path = ClientConfig._get_path(name)
        if os.path.exists(path) is False:
            raise FileNotFoundError(f"Client config '{path}' does not exist.")
        os.unlink(path)
