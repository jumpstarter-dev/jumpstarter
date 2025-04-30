import os
from pathlib import Path
from tempfile import NamedTemporaryFile

import tomli
import tomli_w
from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.plugin import hookimpl
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet


class PinJumpstarter(BuildHookInterface):
    PLUGIN_NAME = "pin_jumpstarter"

    def initialize(self, version, build_data):
        if self.target_name != "sdist":
            return

        pyproject = Path(self.root) / "pyproject.toml"

        with pyproject.open("rb") as f:
            metadata = tomli.load(f)

        if "project" in metadata and "dependencies" in metadata["project"]:
            for i, dep in enumerate(metadata["project"]["dependencies"]):
                req = Requirement(dep)
                if req.name.startswith("jumpstarter"):
                    req.specifier &= SpecifierSet(f"=={self.metadata.version}")
                    metadata["project"]["dependencies"][i] = str(req)

        f = NamedTemporaryFile(delete=False)
        tomli_w.dump(metadata, f)
        f.close()

        build_data["__hatch_pin_jumpstarter_tempfile"] = f
        build_data["force_include"][f.name] = "pyproject.toml"

    def finalize(self, version, build_data, artifact_path):
        if self.target_name != "sdist":
            return

        f = build_data["__hatch_pin_jumpstarter_tempfile"]
        os.unlink(f.name)


@hookimpl
def hatch_register_build_hook():
    return PinJumpstarter
