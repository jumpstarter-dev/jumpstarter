import os
import subprocess
import tempfile
from dataclasses import dataclass

from anyio.streams.file import FileWriteStream

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class ProbeRs(Driver):
    """probe-rs driver for Jumpstarter"""

    probe: str | None = None
    probe_rs_path: str = "probe-rs"
    chip: str | None = None
    protocol: str | None = None
    connect_under_reset: bool = False

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        # some initialization here.

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_probe_rs.client.ProbeRsClient"

    @export
    def info(self) -> str:
        return self._run_cmd(["info"])

    @export
    def reset_target(self) -> str:
        return self._run_cmd(["reset"])

    @export
    def erase(self) -> str:
        return self._run_cmd(["erase"])

    @export
    async def download(self, src: str):
        with TemporaryFilename() as filename:
            async with await FileWriteStream.from_path(filename) as stream:
                async with self.resource(src) as res:
                    async for chunk in res:
                        await stream.send(chunk)
            return self._run_cmd(["download", filename])

    @export
    def read(self, width: str, address: str, words: str) -> list[int]:
        res = self._run_cmd(["read", width, address, words])
        res = res.replace("\n", " ")
        # remove any leading or trailing whitespace
        res = res.strip()
        parts = res.split(" ")
        return parts

    def _run_cmd(self, cmd):
        cmd = [self.probe_rs_path or "probe-rs", *cmd]
        self.logger.debug("Running command: %s", cmd)
        result = subprocess.run(
            cmd,
            capture_output=True,  # Captures stdout and stderr
            text=True,  # Returns stdout/stderr as strings (not bytes)
            env=self.env_from_cfg(),
        )

        if result.returncode != 0:
            self.logger.error(f"Error running {cmd}: {result.stderr}")
            return ""
        self.logger.debug("Command output: %s", result.stdout)
        return result.stdout

    def env_from_cfg(self):
        env = {"PROBE_RS_NON_INTERACTIVE": "true"}
        if self.probe:
            env["PROBE_RS_PROBE"] = self.probe
        if self.chip:
            env["PROBE_RS_CHIP"] = self.chip
        if self.protocol:
            env["PROBE_RS_PROTOCOL"] = self.protocol
        if self.connect_under_reset:
            env["PROBE_RS_CONNECT_UNDER_RESET"] = "true"
        return env


class TemporaryFilename:
    def __enter__(self):
        self.tempfile = tempfile.NamedTemporaryFile(delete=False)
        self.name = self.tempfile.name
        self.tempfile.close()  # Close it immediately since we only want the name
        return self.name

    def __exit__(self, exc_type, exc_value, traceback):
        os.unlink(self.name)
