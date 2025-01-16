import logging
import os
import subprocess
from dataclasses import dataclass, field

from jumpstarter.driver import Driver, export

logger = logging.getLogger(__name__)

@dataclass(kw_only=True)
class Shell(Driver):
    """shell driver for Jumpstarter"""

    # methods field is used to define the methods exported, and the shell script
    # to be executed by each method
    methods: dict[str, str]
    shell: list[str] = field(default_factory=lambda: ["bash", "-c"])
    timeout: int = 300
    log_level: str = "INFO"
    cwd: str | None = None

    def __post_init__(self):
        super().__post_init__()
        # set logger log level
        logger.setLevel(self.log_level)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_shell.client.ShellClient"

    @export
    def get_methods(self) -> list[str]:
        methods = list(self.methods.keys())
        logger.debug(f"get_methods called, returning methods: {methods}")
        return methods

    @export
    def call_method(self, method: str, env, *args):
        logger.info(f"calling {method} with args: {args} and kwargs as env: {env}")
        if method not in self.methods:
            raise ValueError(f"Method '{method}' not found in available methods: {list(self.methods.keys())}")
        script = self.methods[method]
        logger.debug(f"running script: {script}")
        result = self._run_inline_shell_script(method, script, *args, env_vars=env)
        if result.returncode != 0:
            logger.info(f"{method} return code: {result.returncode}")
        if result.stderr != "":
            logger.debug(f"{method} stderr:\n{result.stderr.rstrip("\n")}")
        if result.stdout != "":
            logger.debug(f"{method} stdout:\n{result.stdout.rstrip("\n")}")
        return result.stdout, result.stderr, result.returncode

    def _run_inline_shell_script(self, method, script, *args, env_vars=None):
        """
        Run the given shell script (as a string) with optional arguments and
        environment variables. Returns a CompletedProcess with stdout, stderr, and returncode.

        :param script:      The shell script contents as a string.
        :param args:        Arguments to pass to the script (mapped to $1, $2, etc. in the script).
        :param env_vars:    A dict of environment variables to make available to the script.

        :return:            A subprocess.CompletedProcess object (Python 3.5+).
        """

        # Merge parent environment with the user-supplied env_vars
        # so that we don't lose existing environment variables.
        combined_env = os.environ.copy()
        if env_vars:
            combined_env.update(env_vars)

        if not isinstance(script, str) or not script.strip():
            raise ValueError("Shell script must be a non-empty string")

        cmd = self.shell + [script, method] + list(args)

        # Run the command
        result = subprocess.run(
            cmd,
            capture_output=True,  # Captures stdout and stderr
            text=True,            # Returns stdout/stderr as strings (not bytes)
            env=combined_env,     # Pass our merged environment
            cwd=self.cwd,         # Run in the working directory (if set)
            timeout=self.timeout,
        )

        return result
