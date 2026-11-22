"""One server for every project: the tools run in the Python environment that has a lease's drivers.

What a leased device can do is in the client classes of its drivers, and those are Python packages: a project
installs the ones its devices need, at the versions it needs, in its own environment. A server that had to live in
that environment would have to be set up again in an agent's host for every project. So the server an agent's host
starts may be installed once, anywhere, with no driver at all. When a tool needs the drivers, the server starts a
second copy of itself in the environment that has them (``<environment>/bin/jmp mcp serve``) and passes the tool
call on to it, over the same protocol an agent's host speaks to this one.

* An **environment** is anywhere that server can be started (:func:`resolve`):

  - a Python environment on this machine: a virtual environment's directory, or a project directory that has a
    ``.venv``;
  - a container: ``container:<name>`` is one that runs (the server is started in it with ``exec``), and
    ``image:<reference>`` one that is started for it;
  - any command that starts the server and speaks to it on its standard input and output, given by name in the
    environments file: a remote machine over ``ssh``, a container with the mounts it needs.

* The server **finds** environments (:func:`discover`): the ones a person named in the environments file
  (``mcp-environments.yaml`` beside the client configurations, or the file ``JMP_MCP_ENVIRONMENTS`` names), the one
  named by ``JMP_MCP_ENV``, the active one (``VIRTUAL_ENV``), and the ``.venv`` of the working directory and of the
  directories above it.
* One is **selected** for what has no connection yet (listing leases, connecting): the one the server was started
  with, or the one ``jmp_use_environment`` names. Without one, the server does the work itself, as it always did.
* A **connection belongs to the environment it was made in**: every later call that names it goes there, so two
  leases may be driven from two environments at once without the agent having to say which each time.

**What an agent may choose.** Using an environment starts a program, and the one who asks is a model: what it reads
(a file of a repository, the output of a device) may have told it what to ask. So an agent chooses among
environments, and never names one into being:

* the environments of the environments file, and the one the server was started with, are a person's own words:
  they may be used;
* a Python environment that was only *found* (the ``.venv`` of the directory the server happens to run in) may be
  used only if the server was started with ``--allow-found``: a repository can carry a ``.venv`` of its own;
* a path, a ``container:`` or an ``image:`` that an agent gives and that is none of these is refused, with what there
  is to choose from. Nothing is started for it, and nothing is pulled.

The environments file is a list of commands to run, so it is read only when it is its owner's alone to write (as
``ssh`` reads its configuration). The other server is started without a shell, from a list of arguments. A call that
is passed on is written to the server's log by name, without its arguments.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import anyio.abc
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from jumpstarter_mcp import telemetry

logger = logging.getLogger(__name__)

ENV_VARIABLE = "JMP_MCP_ENV"
#: The file that names environments, when it is not ``mcp-environments.yaml`` in the configuration directory.
FILE_VARIABLE = "JMP_MCP_ENVIRONMENTS"
FILE_NAME = "mcp-environments.yaml"
CONTAINER = "container:"
IMAGE = "image:"
SERVE = ("jmp", "mcp", "serve")
#: Set for a server that was started by another to do its work: it never passes a call on itself.
WORKER_VARIABLE = "JMP_MCP_WORKER"
START_TIMEOUT_SECONDS = 60.0
#: How far above the working directory a project's ``.venv`` is looked for.
MAX_PARENTS = 6
#: How many other servers may run at once: each is a process, and a lease is rarely in more than a few places.
MAX_WORKERS = 8
NOT_DELEGATED: Any = object()


class EnvironmentError_(Exception):
    """An environment cannot be used, and why."""


class ToolFailed(Exception):
    """A tool that was passed on answered that it failed: the text is what it said."""


@dataclass(frozen=True)
class Environment:
    """Somewhere the server can be started: a Python environment, a container, or a command."""

    #: what a person calls it
    name: str
    #: the command that starts the server there, speaking on its standard input and output
    command: tuple[str, ...]
    #: ``venv``, ``container``, ``image`` or ``command``
    kind: str = "venv"
    #: a Python environment's directory (what ``sys.prefix`` is inside it); ``None`` for the other kinds
    path: Path | None = None

    @property
    def key(self) -> tuple[str, ...]:
        """What makes two environments the same one: the command that starts them."""
        return self.command

    @property
    def where(self) -> str:
        """Where it is, as an answer says it: the directory, or ``container:<name>``, or the command."""
        if self.path is not None:
            return str(self.path)
        return self.name if self.kind in ("container", "image") else " ".join(self.command)

    @property
    def is_own(self) -> bool:
        """Whether it is the environment this server runs in: then there is nobody to pass a call on to."""
        return self.path is not None and same_path(self.path, Path(sys.prefix))

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "where": self.where, "own": self.is_own}


def same_path(one: Path, other: Path) -> bool:
    try:
        return one.resolve() == other.resolve()
    except OSError:
        return one == other


def _scripts(path: Path) -> Path:
    return path / ("Scripts" if os.name == "nt" else "bin")


def _engine() -> str:
    for engine in ("podman", "docker"):
        if shutil.which(engine):
            return engine
    raise EnvironmentError_("Neither podman nor docker is installed on this machine: a container cannot be used.")


def _venv(given: Path, name: str | None = None) -> Environment:
    if not given.exists():
        raise EnvironmentError_(f"{given} does not exist")
    candidates = [given.parent.parent] if given.is_file() else [given, given / ".venv", given / "venv"]
    for candidate in candidates:
        for script in ("jmp", "jmp.exe"):
            jmp = _scripts(candidate) / script
            if jmp.is_file():
                called = name or (candidate.parent.name if candidate.name in (".venv", "venv") else candidate.name)
                return Environment(name=called, command=(str(jmp), "mcp", "serve"), kind="venv", path=candidate)
    raise EnvironmentError_(
        f"{given} is not a Python environment with Jumpstarter: there is no `jmp` command in it (or in its .venv). "
        "Install jumpstarter-cli and jumpstarter-mcp there, with the driver packages of your devices."
    )


def _container(spec: str, name: str | None = None) -> Environment:
    """A container that runs (``container:<name>``) or an image to run (``image:<reference>``)."""
    worker = ("-e", f"{WORKER_VARIABLE}=1")
    if spec.startswith(CONTAINER):
        target = spec[len(CONTAINER) :].strip()
        command = (_engine(), "exec", "-i", *worker, target, *SERVE)
        kind = "container"
    else:
        target = spec[len(IMAGE) :].strip()
        command = (_engine(), "run", "--rm", "-i", *worker, target, *SERVE)
        kind = "image"
    if not target:
        raise EnvironmentError_(f"{spec!r} names no container: write container:<name> or image:<reference>")
    return Environment(name=name or spec, command=command, kind=kind)


def environments_file(environ: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    if environ.get(FILE_VARIABLE):
        return Path(environ[FILE_VARIABLE]).expanduser()
    from jumpstarter.config.common import CONFIG_PATH

    return Path(CONFIG_PATH) / FILE_NAME


def unsafe_file(file: Path) -> str | None:
    """Why the environments file is not to be trusted, or ``None``: it names commands to run, so it has to be its
    owner's alone to write, and the owner has to be whoever runs the server."""
    if os.name == "nt":
        return None
    status = file.stat()
    if status.st_uid not in (os.getuid(), 0):
        return "it belongs to someone else"
    if status.st_mode & 0o022:
        return "others may write to it (chmod go-w it)"
    return None


def named(environ: Mapping[str, str] | None = None) -> dict[str, Environment]:
    """The environments a person named in the environments file; one that cannot be used is left out, with a log.

    The file is YAML: ``environments:`` maps a name to ``path:`` (a Python environment or a project), to
    ``container:`` or ``image:``, or to ``command:`` (a list: whatever starts ``jmp mcp serve`` and speaks to it on
    its standard input and output, such as ``[ssh, lab, jmp, mcp, serve]``).
    """
    file = environments_file(environ)
    try:
        import yaml

        problem = unsafe_file(file)
        if problem is not None:
            logger.warning("%s is not read: %s", file, problem)
            return {}
        listed = (yaml.safe_load(file.read_text()) or {}).get("environments") or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - a file that cannot be read names nothing
        logger.warning("%s cannot be read: %s", file, exc)
        return {}
    found: dict[str, Environment] = {}
    for name, entry in listed.items() if isinstance(listed, dict) else ():
        try:
            found[str(name)] = _entry(str(name), entry)
        except EnvironmentError_ as exc:
            logger.warning("environment %r of %s is left out: %s", name, file, exc)
    return found


def _entry(name: str, entry: Any) -> Environment:
    """One environment of the environments file."""
    if isinstance(entry, dict):
        if entry.get("command"):
            return Environment(name=name, command=tuple(str(part) for part in entry["command"]), kind="command")
        if entry.get("container"):
            return _container(f"{CONTAINER}{entry['container']}", name)
        if entry.get("image"):
            return _container(f"{IMAGE}{entry['image']}", name)
        if entry.get("path"):
            return _venv(Path(str(entry["path"])).expanduser(), name)
    raise EnvironmentError_("give path:, container:, image: or command:")


def resolve(spec: str | os.PathLike[str], environ: Mapping[str, str] | None = None) -> Environment:
    """The environment a name means: one of the environments file, ``container:<name>``, ``image:<reference>``, or
    a path (an environment's directory, a project directory with a ``.venv``, or the ``jmp`` or ``python`` inside
    one). Raises :class:`EnvironmentError_` saying what is missing."""
    text = os.fspath(spec).strip()
    known = named(environ)
    if text in known:
        return known[text]
    if text.startswith((CONTAINER, IMAGE)):
        return _container(text)
    try:
        return _venv(Path(text).expanduser())
    except EnvironmentError_ as exc:
        have = f" The environments file names: {', '.join(known)}." if known else ""
        raise EnvironmentError_(f"{exc}{have}") from exc


def discover(cwd: Path | None = None, environ: Mapping[str, str] | None = None) -> list[Environment]:
    """The environments there are to choose from, the likeliest first, each once: the ones a person named, then the
    Python environments near where the server was started."""
    environ = os.environ if environ is None else environ
    found: list[Environment] = list(named(environ).values())
    wanted: list[str | Path] = [
        environ[variable] for variable in (ENV_VARIABLE, "VIRTUAL_ENV") if environ.get(variable)
    ]
    here = (cwd or Path.cwd()).resolve()
    wanted += [here, *here.parents][: MAX_PARENTS + 1]
    for path in wanted:
        try:
            environment = resolve(path, environ)
        except EnvironmentError_:
            continue
        if not any(environment.key == known.key for known in found):
            found.append(environment)
    return found


@dataclass
class _Call:
    tool: str
    arguments: dict[str, Any]
    done: anyio.Event = field(default_factory=anyio.Event)
    #: the trace the caller is in: the call is passed on as part of it
    trace: Any = field(default_factory=telemetry.current)
    answer: str | None = None
    failed: bool = False
    error: BaseException | None = None


class Worker:
    """A copy of this server in another environment, and the calls passed on to it."""

    def __init__(self, environment: Environment) -> None:
        self.environment = environment
        self._send, self._receive = anyio.create_memory_object_stream[_Call](32)
        self.ready = anyio.Event()
        self.failed: BaseException | None = None
        self.tools: list[str] = []

    async def serve(self) -> None:
        """Runs the other server and passes it the calls, one after the other, until this one ends.

        It is one task from start to end: the other server's streams are opened and closed in it.
        """
        env = dict(os.environ)
        env[WORKER_VARIABLE] = "1"
        env.pop(ENV_VARIABLE, None)
        if self.environment.path is not None:
            # as if it had been activated: its own commands (`j`) are the ones found
            env["PATH"] = f"{_scripts(self.environment.path)}{os.pathsep}{env.get('PATH', '')}"
            env["VIRTUAL_ENV"] = str(self.environment.path)
        command, *arguments = self.environment.command
        if self.environment.kind in ("container", "image") and "-i" in arguments:
            # a container is told to send its traces where this server sends its own: the names only, so that
            # nothing of their values is on a command line
            at = arguments.index("-i") + 1
            wanted = [name for name in sorted(env) if name.startswith("OTEL_")]
            arguments[at:at] = [part for name in wanted for part in ("-e", name)]
        params = StdioServerParameters(command=command, args=arguments, env=env)
        try:
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                with anyio.fail_after(START_TIMEOUT_SECONDS):
                    await session.initialize()
                    self.tools = [tool.name for tool in (await session.list_tools()).tools]
                logger.info("environment %s serves %d tools", self.environment.where, len(self.tools))
                self.ready.set()
                async with self._receive:
                    async for call in self._receive:
                        try:
                            # (the MCP library sends the trace on with the call: the other server's work is
                            # part of the same trace as what the agent asked of this one)
                            with telemetry.within(call.trace):
                                result = await session.call_tool(call.tool, call.arguments)
                            call.answer = "\n".join(getattr(part, "text", "") for part in result.content)
                            call.failed = bool(getattr(result, "is_error", False))
                        except Exception as exc:  # noqa: BLE001 - the caller is told, and the next call is served
                            call.error = exc
                        call.done.set()
        except BaseException as exc:
            self.failed = exc
            self.ready.set()
            if not isinstance(exc, anyio.get_cancelled_exc_class()):
                logger.warning("the server of %s ended: %s", self.environment.where, exc)
            raise
        finally:
            self._send.close()

    async def call(self, tool: str, arguments: dict[str, Any]) -> str:
        await self.ready.wait()
        if self.failed is not None:
            raise EnvironmentError_(f"The server of {self.environment.where} could not be started: {self.failed}")
        if self.tools and tool not in self.tools:
            raise EnvironmentError_(
                f"The Jumpstarter in {self.environment.where} has no {tool} tool: its jumpstarter-mcp is older than "
                "this server's. Update it there."
            )
        call = _Call(tool, arguments)
        try:
            await self._send.send(call)
        except (anyio.ClosedResourceError, anyio.BrokenResourceError) as exc:
            raise EnvironmentError_(f"The server of {self.environment.where} has ended.") from exc
        await call.done.wait()
        if call.error is not None:
            raise EnvironmentError_(f"{tool} failed in {self.environment.where}: {call.error}")
        if call.failed:
            raise ToolFailed(call.answer or f"{tool} failed in {self.environment.where}")
        return call.answer or ""


class Router:
    """Which environment a tool call goes to, and the servers running in them."""

    def __init__(
        self,
        selected: Environment | None = None,
        *,
        worker: bool | None = None,
        allow_found: bool = False,
        cwd: Path | None = None,
    ) -> None:
        #: a server started by another does the work itself, whatever it finds around it
        self.is_worker = os.environ.get(WORKER_VARIABLE) == "1" if worker is None else worker
        #: the environment the server was started with: a person's choice, so an agent may use it
        self.started_with = None if self.is_worker else selected
        self.selected = self.started_with
        #: whether a Python environment that was only found near the server may be used
        self.allow_found = allow_found
        self.cwd = cwd
        self.connections: dict[str, Environment] = {}
        self._workers: dict[tuple[str, ...], Worker] = {}
        self._tasks: anyio.abc.TaskGroup | None = None

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[Router]:
        """Keeps the other servers for as long as this one runs, and ends them with it."""
        async with anyio.create_task_group() as tasks:
            self._tasks = tasks
            try:
                yield self
            finally:
                self._tasks = None
                tasks.cancel_scope.cancel()

    def choices(self) -> tuple[list[Environment], list[Environment]]:
        """What an agent may use, and what was found but is not its to use: each once, a person's own first."""
        if self.is_worker:
            return [], []
        allowed: list[Environment] = list(named().values())
        if self.started_with is not None and not any(self.started_with.key == e.key for e in allowed):
            allowed.insert(0, self.started_with)
        found = [e for e in discover(self.cwd) if not any(e.key == known.key for known in allowed)]
        if self.allow_found:
            return [*allowed, *found], []
        return allowed, found

    def choose(self, name: str) -> Environment:
        """The environment an agent means by ``name``: one it may use, by its name or by where it is.

        Raises :class:`EnvironmentError_` for anything else, saying what there is to choose from. What an agent
        gives is only ever compared with what a person allowed: nothing is made of it.
        """
        wanted = name.strip()
        allowed, found = self.choices()
        for environment in allowed:
            if wanted in (environment.name, environment.where):
                return environment
        have = ", ".join(sorted({e.name for e in allowed})) or "none"
        if any(wanted in (e.name, e.where) for e in found):
            raise EnvironmentError_(
                f"{wanted!r} was found on this machine but nobody said it may be used. A person can allow it: name it "
                f"in {environments_file()}, start the server with --env, or start it with --allow-found. "
                f"Allowed now: {have}."
            )
        raise EnvironmentError_(
            f"{wanted!r} is not an environment this server may use. An environment is chosen by a person, in "
            f"{environments_file()} or with --env when the server is started; a path, container or image given "
            f"here is not used. Allowed now: {have}."
        )

    def use(self, name: str | None) -> Environment | None:
        """Selects the environment an agent names; ``None`` or ``""`` selects this server's own."""
        if self.is_worker:
            raise EnvironmentError_("This server was started by another to do its work: it uses its own environment.")
        self.selected = self.choose(name) if name else None
        return self.selected

    def target(self, arguments: Mapping[str, Any]) -> Environment | None:
        """The environment a call with these arguments goes to; ``None`` is this server itself."""
        if self.is_worker:
            return None
        given = arguments.get("environment")
        if isinstance(given, str) and given:
            environment: Environment | None = self.choose(given)
        else:
            connection = arguments.get("connection_id")
            if isinstance(connection, str) and connection in self.connections:
                environment = self.connections[connection]
            else:
                environment = self.selected
        return None if environment is None or environment.is_own else environment

    async def _worker(self, environment: Environment) -> Worker:
        key = environment.key
        worker = self._workers.get(key)
        if worker is None or worker.failed is not None:
            if self._tasks is None:
                raise EnvironmentError_("The server is not running.")
            running = sum(1 for other in self._workers.values() if other.failed is None)
            if worker is None and running >= MAX_WORKERS:
                raise EnvironmentError_(f"{MAX_WORKERS} environments are in use already: disconnect from one first.")
            worker = Worker(environment)
            self._workers[key] = worker
            self._tasks.start_soon(_quietly, worker)
        return worker

    async def forward(self, tool: str, arguments: dict[str, Any]) -> Any:
        """What the tool answered in the environment the call belongs to, or :data:`NOT_DELEGATED` when that is
        this server. An answer that names a connection (``jmp_connect``) is remembered as that environment's.

        Raises :class:`EnvironmentError_` when the call cannot be passed on, and :class:`ToolFailed` when the tool
        failed there: a failure is a failure here too, not an answer.
        """
        environment = self.target(arguments)
        if environment is None:
            return NOT_DELEGATED
        passed = {name: value for name, value in arguments.items() if name != "environment"}
        # what was asked of which environment, for whoever reads the log afterwards: never the arguments
        logger.info("%s is passed on to %s (connection %s)", tool, environment.where, passed.get("connection_id"))
        telemetry.annotate(
            environment__name=environment.name,
            environment__kind=environment.kind,
            connection__id=passed.get("connection_id") if isinstance(passed.get("connection_id"), str) else None,
        )
        answer = await (await self._worker(environment)).call(tool, passed)
        try:
            said = json.loads(answer)
        except ValueError:
            return answer
        if isinstance(said, dict):
            connection = said.get("connection_id")
            if isinstance(connection, str) and "error" not in said:
                if tool == "jmp_disconnect":
                    self.connections.pop(connection, None)
                else:
                    self.connections[connection] = environment
            # every answer says where it came from: with several environments in use, none is taken for another
            said.setdefault("environment", environment.name)
            return json.dumps(said, indent=2)
        return answer

    def describe(self) -> dict[str, Any]:
        allowed, found = self.choices()
        described: dict[str, Any] = {
            "selected": self.selected.describe() if self.selected is not None else None,
            "own": {"path": sys.prefix, "note": "this server's own environment: used when none is selected"},
            "allowed": [environment.describe() for environment in allowed],
            "connections": {connection: env.name for connection, env in self.connections.items()},
        }
        if found:
            described["found_not_allowed"] = [environment.describe() for environment in found]
            described["found_not_allowed_note"] = (
                "Found on this machine, and not used unless a person allows them: by naming them in "
                f"{environments_file()}, or by starting the server with --env or --allow-found."
            )
        return described


async def _quietly(worker: Worker) -> None:
    """A server that ends, or cannot start, is its callers' matter (they are told): it does not end this one."""
    try:
        await worker.serve()
    except Exception:
        logger.debug("the server of %s ended", worker.environment.where, exc_info=True)
