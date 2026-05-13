import os
import signal
import subprocess
from dataclasses import dataclass, field
from subprocess import PIPE
from typing import AsyncGenerator

import anyio
from anyio import move_on_after

from jumpstarter.driver import Driver, export

BLOCKED_ENV_VARS: set[str] = {
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PATH",
    "PYTHONPATH",
    "BASH_ENV",
    "KUBECONFIG",
    "HOME",
}

BLOCKED_ENV_PREFIXES: tuple[str, ...] = (
    "LD_",
    "BASH_FUNC_",
)


@dataclass(kw_only=True)
class Shell(Driver):
    """shell driver for Jumpstarter"""

    methods: dict[str, str | dict[str, str | int]]
    shell: list[str] = field(default_factory=lambda: ["bash", "-c"])
    timeout: int = 300
    cwd: str | None = None

    def __post_init__(self):
        super().__post_init__()
        for method_name, method_config in self.methods.items():
            if isinstance(method_config, dict) and "description" in method_config:
                self.methods_description[method_name] = method_config["description"]

    def _get_method_command(self, method: str) -> str:
        """Extract the command string from a method configuration"""
        method_config = self.methods[method]
        if isinstance(method_config, str):
            return method_config
        return method_config.get("command", "echo Hello")

    def _get_method_timeout(self, method: str) -> int:
        """Extract the timeout from a method configuration, fallback to global timeout"""
        method_config = self.methods[method]
        if isinstance(method_config, str):
            return self.timeout
        return method_config.get("timeout", self.timeout)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_shell.client.ShellClient"

    @export
    def get_methods(self) -> list[str]:
        methods = list(self.methods.keys())
        self.logger.debug(f"get_methods called, returning methods: {methods}")
        return methods

    @export
    async def call_method(self, method: str, env, *args) -> AsyncGenerator[tuple[str, str, int | None], None]:
        """
        Execute a shell method with live streaming output.
        Yields (stdout_chunk, stderr_chunk, returncode) tuples.
        returncode is None until the process completes, then it's the final return code.
        """
        self.logger.info(f"calling {method} with args: {args} and kwargs as env: {env}")
        if method not in self.methods:
            raise ValueError(f"Method '{method}' not found in available methods: {list(self.methods.keys())}")
        script = self._get_method_command(method)
        timeout = self._get_method_timeout(method)
        self.logger.debug(f"running script: {script} with timeout: {timeout}")

        try:
            async for stdout_chunk, stderr_chunk, returncode in self._run_inline_shell_script(
                method, script, *args, env_vars=env, timeout=timeout
            ):
                if stdout_chunk:
                    self.logger.debug(f"{method} stdout:\n{stdout_chunk.rstrip()}")
                if stderr_chunk:
                    self.logger.debug(f"{method} stderr:\n{stderr_chunk.rstrip()}")

                if returncode is not None and returncode != 0:
                    self.logger.info(f"{method} return code: {returncode}")

                yield stdout_chunk, stderr_chunk, returncode
        except subprocess.TimeoutExpired as e:
            self.logger.error(f"Timeout expired while running {method}: {e}")
            yield "", f"\nTimeout expired while running {method}: {e}\n", 199

    def _validate_script_params(self, script, args, env_vars):
        """Validate script parameters and return combined environment."""
        combined_env = os.environ.copy()
        if env_vars:
            for key in env_vars:
                if not isinstance(key, str) or not key.isidentifier():
                    raise ValueError(f"Invalid environment variable name: {key}")
                if key in BLOCKED_ENV_VARS or key.startswith(BLOCKED_ENV_PREFIXES):
                    raise ValueError(
                        f"Environment variable '{key}' is blocked for security reasons"
                    )
            combined_env.update(env_vars)

        if not isinstance(script, str) or not script.strip():
            raise ValueError("Shell script must be a non-empty string")

        for arg in args:
            if not isinstance(arg, str):
                raise ValueError(f"All arguments must be strings, got {type(arg)}")

        if self.cwd and not os.path.isdir(self.cwd):
            raise ValueError(f"Working directory does not exist: {self.cwd}")

        return combined_env

    @staticmethod
    async def _read_stream(stream, read_all: bool) -> str:
        """Read from a single byte stream and return decoded text."""
        if stream is None:
            return ""
        try:
            if read_all:
                chunks = []
                try:
                    while True:
                        chunks.append(await stream.receive())
                except anyio.EndOfStream:
                    pass
                chunk = b"".join(chunks)
            else:
                chunk = None
                with move_on_after(0.01):
                    chunk = await stream.receive(1024)
            if chunk:
                return chunk.decode('utf-8', errors='replace')
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            pass
        return ""

    async def _read_process_output(self, process, read_all=False):
        """Read data from stdout and stderr streams.

        :param process: The subprocess to read from
        :param read_all: If True, read all remaining data. If False, read with timeout.
        :return: Tuple of (stdout_data, stderr_data)
        """
        stdout_data = await self._read_stream(process.stdout, read_all)
        stderr_data = await self._read_stream(process.stderr, read_all)
        return stdout_data, stderr_data

    async def _run_inline_shell_script(
        self, method, script, *args, env_vars=None, timeout=None
    ) -> AsyncGenerator[tuple[str, str, int | None], None]:
        """
        Run the given shell script with live streaming output.

        :param method:      The method name (for logging).
        :param script:      The shell script contents as a string.
        :param args:        Arguments to pass to the script (mapped to $1, $2, etc. in the script).
        :param env_vars:    A dict of environment variables to make available to the script.
        :param timeout:     Customized command timeout in seconds. If None, uses global timeout.

        :yields:            Tuples of (stdout_chunk, stderr_chunk, returncode).
                           returncode is None until the process completes.
        """
        combined_env = self._validate_script_params(script, args, env_vars)
        cmd = self.shell + [script, method] + list(args)

        self.logger.debug(f"running {method} with cmd: {cmd} and env: {combined_env} and args: {args}")
        process = await anyio.open_process(
            cmd,
            stdout=PIPE,
            stderr=PIPE,
            env=combined_env,
            cwd=self.cwd,
            start_new_session=True,
        )

        start_time = anyio.current_time()

        if timeout is None:
            timeout = self.timeout

        try:
            while process.returncode is None:
                if anyio.current_time() - start_time > timeout:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
                    with move_on_after(5.0):
                        await process.wait()
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                            self.logger.warning(f"SIGTERM failed to terminate {process.pid}, sending SIGKILL")
                        except (ProcessLookupError, OSError):
                            pass
                    raise subprocess.TimeoutExpired(cmd, timeout) from None

                try:
                    stdout_data, stderr_data = await self._read_process_output(process, read_all=False)

                    if stdout_data or stderr_data:
                        yield stdout_data, stderr_data, None

                    await anyio.sleep(0.1)

                except (anyio.EndOfStream, anyio.ClosedResourceError):
                    break

            returncode = process.returncode
            remaining_stdout, remaining_stderr = await self._read_process_output(process, read_all=True)
            yield remaining_stdout, remaining_stderr, returncode
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
