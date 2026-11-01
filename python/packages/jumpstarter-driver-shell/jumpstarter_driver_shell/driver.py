import codecs
import os
import subprocess
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from anyio import EndOfStream, current_time, move_on_after

from jumpstarter.common.process import OutputChunk, StreamingProcess, open_streaming_process
from jumpstarter.driver import Driver, export

# How long a script's output may stay quiet after it exits before the method
# returns; a background job the script started may keep the pipes open.
_QUIET_AFTER_EXIT_SECONDS = 0.1

# Environment variables that are blocked because they allow privilege escalation.
# A client that can set these can hijack the subprocess (e.g. LD_PRELOAD to load
# arbitrary shared libraries, PATH to redirect commands to attacker binaries).
BLOCKED_ENV_VARS: set[str] = {
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PATH",
    "PYTHONPATH",
    "BASH_ENV",
    "KUBECONFIG",
    "HOME",
}

# Prefixes that are also blocked (matched with str.startswith).
BLOCKED_ENV_PREFIXES: tuple[str, ...] = (
    "LD_",
    "BASH_FUNC_",
)


@dataclass(kw_only=True)
class Shell(Driver):
    """shell driver for Jumpstarter"""

    driver_type = "shell"

    # methods field defines the methods exported and their shell scripts
    # Supports two formats:
    # 1. Simple string: method_name: "command"
    # 2. Dict with description: method_name: {command: "...", description: "...", timeout: ...}
    methods: dict[str, str | dict[str, str | int]]
    shell: list[str] = field(default_factory=lambda: ["bash", "-c"])
    timeout: int = 300
    cwd: str | None = None

    def __post_init__(self):
        super().__post_init__()
        # Extract descriptions from methods configuration and populate methods_description
        for method_name, method_config in self.methods.items():
            if isinstance(method_config, dict) and "description" in method_config:
                self.methods_description[method_name] = str(method_config["description"])

    def _get_method_command(self, method: str) -> str:
        """Extract the command string from a method configuration"""
        method_config = self.methods[method]
        if isinstance(method_config, str):
            return method_config
        return str(method_config.get("command", "echo Hello"))

    def _get_method_timeout(self, method: str) -> int:
        """Extract the timeout from a method configuration, fallback to global timeout"""
        method_config = self.methods[method]
        if isinstance(method_config, str):
            return self.timeout
        return int(method_config.get("timeout", self.timeout))

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
        # Merge parent environment with the user-supplied env_vars
        combined_env = os.environ.copy()
        if env_vars:
            # Validate environment variable names
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

        # Validate arguments
        for arg in args:
            if not isinstance(arg, str):
                raise TypeError(f"All arguments must be strings, got {type(arg)}")  # pragma: no cover

        # Validate working directory if set
        if self.cwd and not os.path.isdir(self.cwd):
            raise ValueError(f"Working directory does not exist: {self.cwd}")

        return combined_env

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
        if timeout is None:
            timeout = self.timeout

        self.logger.debug(f"running {method} with cmd: {cmd} and env: {combined_env} and args: {args}")
        deadline = current_time() + timeout
        decoders = {source: codecs.getincrementaldecoder("utf-8")(errors="replace") for source in ("stdout", "stderr")}
        async with open_streaming_process(
            cmd, env=combined_env, cwd=self.cwd, new_process_group=True, stop_grace=5.0
        ) as process:
            while True:
                try:
                    chunk = await self._next_chunk(process, deadline)
                except TimeoutError:
                    await process.stop()
                    raise subprocess.TimeoutExpired(cmd, timeout) from None
                if chunk is None:
                    break
                text = decoders[chunk.source].decode(chunk.data)
                if text:
                    yield (text, "", None) if chunk.source == "stdout" else ("", text, None)
            returncode = await process.wait()
        yield decoders["stdout"].decode(b"", final=True), decoders["stderr"].decode(b"", final=True), returncode

    @staticmethod
    async def _next_chunk(process: StreamingProcess, deadline: float) -> OutputChunk | None:
        """The next output chunk, or None once the script has exited and its output has ended or gone quiet."""
        while True:
            remaining = deadline - current_time()
            if remaining <= 0:
                raise TimeoutError
            with move_on_after(min(remaining, _QUIET_AFTER_EXIT_SECONDS)):
                try:
                    return await process.receive()
                except EndOfStream:
                    return None
            if process.returncode is not None:
                return None
