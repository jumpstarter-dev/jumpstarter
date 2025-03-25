import os
import subprocess
from dataclasses import dataclass, field

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Shell(Driver):
    """shell driver for Jumpstarter"""

    # methods field is used to define the methods exported, and the shell script
    # to be executed by each method
    methods: dict[str, str]
    shell: list[str] = field(default_factory=lambda: ["bash", "-c"])
    timeout: int = 300
    cwd: str | None = None

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_shell.client.ShellClient"

    @export
    def get_methods(self) -> list[str]:
        methods = list(self.methods.keys())
        self.logger.debug(f"get_methods called, returning methods: {methods}")
        return methods

    @export
    def call_method(self, method: str, env, *args):
        self.logger.info(f"calling {method} with args: {args} and kwargs as env: {env}")
        if method not in self.methods:
            raise ValueError(f"Method '{method}' not found in available methods: {list(self.methods.keys())}")
        script = self.methods[method]
        self.logger.debug(f"running script: {script}")
        try:
            result = self._run_inline_shell_script(method, script, *args, env_vars=env)
            if result.returncode != 0:
                self.logger.info(f"{method} return code: {result.returncode}")
            if result.stderr != "":
                stderr = result.stderr.rstrip("\n")
                self.logger.debug(f"{method} stderr:\n{stderr}")
            if result.stdout != "":
                stdout = result.stdout.rstrip("\n")
                self.logger.debug(f"{method} stdout:\n{stdout}")
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as e:
            self.logger.error(f"Timeout expired while running {method}: {e}")
            return "", f"Timeout expired while running {method}: {e}", 199

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
            # Validate environment variable names
            for key in env_vars:
                if not isinstance(key, str) or not key.isidentifier():
                    raise ValueError(f"Invalid environment variable name: {key}")
            combined_env.update(env_vars)

        if not isinstance(script, str) or not script.strip():
            raise ValueError("Shell script must be a non-empty string")

        # Validate arguments
        for arg in args:
            if not isinstance(arg, str):
                raise ValueError(f"All arguments must be strings, got {type(arg)}")

        # Validate working directory if set
        if self.cwd and not os.path.isdir(self.cwd):
            raise ValueError(f"Working directory does not exist: {self.cwd}")

        cmd = self.shell + [script, method] + list(args)

        # Run the command
        result = subprocess.run(
            cmd,
            capture_output=True,  # Captures stdout and stderr
            text=True,  # Returns stdout/stderr as strings (not bytes)
            env=combined_env,  # Pass our merged environment
            cwd=self.cwd,  # Run in the working directory (if set)
            timeout=self.timeout,
        )

        return result
