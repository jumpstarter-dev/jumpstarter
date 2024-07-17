import os

import yaml

_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".config", "jumpstarter")


class Config:
    def __init__(self, context: str = None):
        self.endpoint = os.environ.get("JUMPSTARTER_ENDPOINT") or None
        self.token = os.environ.get("JUMPSTARTER_TOKEN") or None
        self.name = "client"
        self.context = context or os.environ.get("JUMPSTARTER_CONTEXT") or None
        if self.endpoint == None or self.token == None:
            self._load(self._filename)

    @property
    def _filename(self):
        if os.environ.get("JUMPSTARTER_CONFIG"):
            return os.environ.get("JUMPSTARTER_CONFIG")

        if self.context:
            return os.path.join(_CONFIG_PATH, "config_{}.yaml".format(self.context))
        return os.path.join(_CONFIG_PATH, "config.yaml")

    def _load(self, path):
        with open(path) as f:
            config = yaml.safe_load(f)
            client = config.get("client")
            print(client)
            if client is None:
                raise ValueError(
                    "config file {} does not contain a 'client' key".format(path)
                )

            self.endpoint = client.get("endpoint")
            self.token = client.get("token")
            self.name = client.get("name") or "client"

            if self.endpoint is None or self.token is None:
                raise ValueError(
                    "config file {} does not contain 'client.endpoint' or 'client.token' keys".format(
                        path
                    )
                )
