from hatchling.metadata.plugin.interface import MetadataHookInterface
from hatchling.plugin import hookimpl
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet


class PinJumpstarter(MetadataHookInterface):
    PLUGIN_NAME = "pin_jumpstarter"

    def update(self, metadata):
        version = metadata["version"]
        dependencies = []
        for dep in metadata["dependencies"]:
            req = Requirement(dep)
            if req.name.startswith("jumpstarter"):
                req.specifier &= SpecifierSet(f"=={version}")
            dependencies.append(str(req))
        metadata["dependencies"] = dependencies


@hookimpl
def hatch_register_metadata_hook():
    return PinJumpstarter
