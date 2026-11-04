"""``Driver``: the Robot Framework library of one node of the driver tree.

Keywords come from the node's client class, read statically, so libdoc and the
editor's language server list them with arguments, types and documentation
without a lease. At run time the library binds to the node at ``path`` in its
session and calls the client.

Import one instance per node, named after its path::

    Library    jumpstarter_robot.Driver    dutlink.power    client=jumpstarter_driver_power.client.PowerClient
    ...    prefix=Power    AS    dutlink.power
"""

from __future__ import annotations

import importlib
import inspect
import itertools
import logging
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import context, sessions
from .curation import documentation, is_exposed, matches, method_words, tags, type_overrides
from .errors import mapped_errors
from .extensions import extensions_for
from .naming import keyword_name, normalize, title
from .signatures import ArgumentSpec, argument_spec, source_of
from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient
from jumpstarter.client.introspect import iter_client_members

logger = logging.getLogger(__name__)

CLIENT_LABEL = "jumpstarter.dev/client"


@dataclass
class KeywordSpec:
    name: str
    args: list[Any]
    types: dict[str, Any] | None
    doc: str
    tags: tuple[str, ...]
    source: str | None
    invoke: Callable[[DriverClient | None, list[Any], dict[str, Any]], Any]
    arguments: ArgumentSpec | None = None
    needs_node: bool = True
    kind: str = "method"
    extra: dict[str, Any] = field(default_factory=dict)


def import_client_class(spec: str) -> type:
    """Import ``module.Class`` or ``module:Class``."""
    module_name, sep, class_name = spec.partition(":")
    if not sep:
        module_name, _, class_name = spec.rpartition(".")
    if not module_name or not class_name:
        raise ValueError(f"client must be a dotted class path like package.module.Class, not '{spec}'")
    module = importlib.import_module(module_name)
    cls = module
    for part in class_name.split("."):
        cls = getattr(cls, part)
    if not inspect.isclass(cls):
        raise TypeError(f"'{spec}' is not a class")
    return cls


def class_path(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _in_run() -> bool:
    """Whether tests are executing; a dry run is not, so it never connects to read a client class."""
    from robot.running.context import EXECUTION_CONTEXTS

    current = EXECUTION_CONTEXTS.current
    return current is not None and not current.dry_run


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part) for part in value]


_instances: weakref.WeakSet[Driver] = weakref.WeakSet()


class Driver:
    """Keywords for one driver of a Jumpstarter exporter.

    = Binding =

    ``path`` is the driver's dotted position in the exporter's tree, the
    same as the Python access path (``dutlink.power`` is
    ``client.dutlink.power``). ``client`` names its Python client class,
    which is where the keywords come from; the generated client resource
    fills it in. The library binds lazily: the first keyword opens the
    ``session`` and checks that the driver at ``path`` is that client class.

    = Keyword names =

    Every keyword starts with ``prefix``, by default the words of ``path``:
    ``dutlink.power`` gives ``Dutlink Power On``. The generated client
    resource uses the same full path unless an alias in ``robot.toml``
    chooses another prefix (``Main Power On``).

    = Roles =

    With ``role`` instead of a fixed path, the library can be pointed at
    any matching driver with `Bind Driver` or `Use Device` from the
    Jumpstarter library, which lets one set of unprefixed keywords drive
    several identical devices in turn.
    """

    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LISTENER_API_VERSION = 3

    def __init__(
        self,
        path: str = "",
        client: str | None = None,
        prefix: str | None = None,
        session: str | None = None,
        required: bool = True,
        methods: str | list[str] | None = None,
        role: str | None = None,
        include: str | list[str] | None = None,
        exclude: str | list[str] | None = None,
        max_items: int = 1000,
    ):
        self.ROBOT_LIBRARY_LISTENER = self
        self.role = role or None
        self.default_path = path or (role or "")
        self.path = self.default_path
        self.session_name = session or None
        self.required = required
        self.max_items = max_items
        self.include = _as_list(include)
        self.exclude = _as_list(exclude)
        self.shell_methods = _as_list(methods)
        self.prefix = title(*self.default_path.split(".")) if prefix is None else prefix
        self.client_spec = client
        self.client_class: type | None = import_client_class(client) if client else None
        # (session, path, client); the session itself, not its id, so a new
        # session that happens to reuse a closed one's address is never mistaken for it
        self._bound: tuple[sessions.Session, str, DriverClient] | None = None
        self._specs: dict[str, KeywordSpec] = {}
        if self.client_class is None and _in_run():
            self.client_class = self._live_class()
        if self.client_class is not None:
            self._build(self.client_class)
        _instances.add(self)

    # ---- keyword discovery ----

    def _live_class(self) -> type | None:
        try:
            node = context.session(self.session_name).resolve(self.path)
        except Exception as e:  # noqa: BLE001 - reported, the library just has no keywords
            from robot.api import logger as robot_logger

            robot_logger.warn(f"Driver '{self.path}': cannot read its client class from the exporter: {e}")
            return None
        if isinstance(node, StubDriverClient):
            return None
        if not self.shell_methods:
            self.shell_methods = shell_methods(node)
        return type(node)

    def _add(self, spec: KeywordSpec) -> None:
        key = normalize(spec.name)
        for existing in self._specs.values():
            if normalize(existing.name) == key:
                logger.warning("Driver '%s': keyword '%s' defined twice; keeping the first", self.path, spec.name)
                return
        self._specs[spec.name] = spec

    def _footer(self, cls: type, member: str) -> str:
        receiver = f"client.{self.default_path}" if self.default_path else "client"
        return (
            f"\n\n_Driver ``{self.default_path or '(root)'}``, client ``{class_path(cls)}``. "
            f"Python: ``{receiver}.{member}(...)``._"
        )

    def _build(self, cls: type) -> None:
        base_tags = ("jumpstarter", f"driver:{self.default_path or 'root'}", f"class:{cls.__name__}")
        contexts = []
        for member in iter_client_members(cls):
            if not self._wanted(member, cls):
                continue
            if member.kind == "contextmanager":
                contexts.append(member.name)
            elif (spec := self._member_spec(cls, member, base_tags)) is not None:
                self._add(spec)
        if is_shell(cls):
            self._build_shell(cls, base_tags)
        for extension in extensions_for(cls):
            self._build_extension(extension, base_tags)
        self._contexts = contexts

    def _wanted(self, member, cls: type) -> bool:
        if not is_exposed(member, cls):
            return False
        if self.include and not matches(member.name, self.include):
            return False
        return not matches(member.name, self.exclude)

    def _member_spec(self, cls: type, member, base_tags: tuple[str, ...]) -> KeywordSpec | None:
        """The keyword of one client member; None for members that get none (async, unreadable signature)."""
        if member.kind == "async":
            return None
        doc = documentation(member) + self._footer(cls, member.name)
        member_tags = (*base_tags, member.kind, *tags(member))
        source = source_of(member.function)
        if member.kind == "property":
            return KeywordSpec(
                name=keyword_name("Get", keyword_name(self.prefix, method_words(member))),
                args=[],
                types={},
                doc=doc,
                tags=member_tags,
                source=source,
                invoke=_property_invoker(member.name),
                kind="property",
            )
        try:
            arguments = argument_spec(member.function)
        except (TypeError, ValueError):
            return None
        overrides = type_overrides(member)
        rf_types = None if overrides is None else {**arguments.types, **overrides}
        args = list(arguments.args)
        if member.kind == "generator":
            args = _with_keyword_only(args, ("max_items", self.max_items))
            invoke = _generator_invoker(member.name)
        else:
            invoke = _method_invoker(member.name)
        return KeywordSpec(
            name=keyword_name(self.prefix, method_words(member)),
            args=args,
            types=rf_types,
            doc=doc,
            tags=member_tags,
            source=source,
            invoke=invoke,
            arguments=arguments,
            kind=member.kind,
        )

    def _build_shell(self, cls: type, base_tags: tuple[str, ...]) -> None:
        run_doc = (
            "Run a method configured on this shell driver and return ``rc``, ``stdout`` and ``stderr``.\n\n"
            "Positional arguments are passed to the method; named arguments become its environment. "
            "Fails unless the return code is ``expected_rc`` (default 0); use ``expected_rc=any`` to accept any."
        )
        self._add(
            KeywordSpec(
                name=keyword_name(self.prefix, "Run Method"),
                args=["method", "*args", ("expected_rc", 0), "**env"],
                types={"method": str},
                doc=run_doc + self._footer(cls, "<method>"),
                tags=(*base_tags, "shell"),
                source=None,
                invoke=lambda node, a, k: run_shell_method(node, a[0], a[1:], k),
                kind="shell",
            )
        )
        for method in self.shell_methods:
            self._add(
                KeywordSpec(
                    name=keyword_name(self.prefix, title(method)),
                    args=["*args", ("expected_rc", 0), "**env"],
                    types={},
                    doc=f"Run the shell method ``{method}``.\n\n{run_doc}" + self._footer(cls, method),
                    tags=(*base_tags, "shell"),
                    source=None,
                    invoke=_shell_invoker(method),
                    kind="shell",
                )
            )

    def _build_extension(self, extension, base_tags: tuple[str, ...]) -> None:
        try:
            arguments = argument_spec(extension.function, skip=2)
        except (TypeError, ValueError):
            return

        def invoke(node, a, k, _function=extension.function):
            return _function(self.path, self.session_name, *a, **k)

        self._add(
            KeywordSpec(
                name=keyword_name(self.prefix, extension.method_words),
                args=list(arguments.args),
                types=arguments.types,
                doc=(extension.doc or inspect.getdoc(extension.function) or ""),
                tags=(*base_tags, "extension", *extension.tags),
                source=source_of(extension.function),
                invoke=invoke,
                arguments=arguments,
                needs_node=False,
                kind="extension",
            )
        )

    # ---- dynamic library API ----

    def get_keyword_names(self) -> list[str]:
        return list(self._specs)

    def get_keyword_arguments(self, name: str) -> list[Any]:
        return self._specs[name].args

    def get_keyword_types(self, name: str) -> dict[str, Any] | None:
        return self._specs[name].types

    def get_keyword_tags(self, name: str) -> list[str]:
        return list(self._specs[name].tags)

    def get_keyword_source(self, name: str) -> str | None:
        return self._specs[name].source

    def get_keyword_documentation(self, name: str) -> str:
        if name == "__intro__":
            return self._intro()
        if name == "__init__":
            return inspect.getdoc(Driver) or ""
        return self._specs[name].doc

    def _intro(self) -> str:
        cls = self.client_class
        if cls is None:
            return f"Driver ``{self.default_path}``: client class unknown, no keywords."
        # @robot.library(doc=...) replaces the class docstring, as it does for Robot libraries
        text = getattr(cls, "ROBOT_LIBRARY_DOC", None) or inspect.getdoc(cls) or ""
        text += f"\n\nKeywords for the driver at ``{self.default_path or '(root)'}``, client ``{class_path(cls)}``."
        if getattr(self, "_contexts", None):
            listed = ", ".join(f"``{c}``" for c in self._contexts)
            text += f" Context managers ({listed}) are used with `Open Driver Context`."
        return text

    def run_keyword(self, name: str, args: list[Any], named: dict[str, Any] | None = None) -> Any:
        spec = self._specs[name]
        named = dict(named or {})
        with sessions.lock(), mapped_errors(name):
            node = self.node() if spec.needs_node else None
            if spec.arguments is not None and spec.kind != "extension":
                if spec.kind == "generator":
                    max_items = named.pop("max_items", self.max_items)
                    args, named = spec.arguments.bind(args, named)
                    named["__max_items__"] = int(max_items)
                else:
                    args, named = spec.arguments.bind(args, named)
            elif spec.arguments is not None:
                args, named = spec.arguments.bind(args, named)
            result = spec.invoke(node, list(args), named)
            return context.to_robot(result, max_items=self.max_items, stem=name)

    # ---- binding ----

    def node(self) -> DriverClient:
        """The bound client, resolved in the session and checked against ``client``."""
        from robot.api import Failure

        session = context.session(self.session_name)
        session.check_alive()
        if self._bound is not None and self._bound[0] is session and self._bound[1] == self.path:
            return self._bound[2]
        try:
            node = session.resolve(self.path)
        except KeyError as e:
            kind = "required" if self.required else "optional"
            raise Failure(f"{e.args[0]} (this {kind} driver is not on exporter '{session.exporter}')") from None
        if isinstance(node, StubDriverClient):
            node._raise_missing_error()
        cls = self.client_class
        if cls is not None and not isinstance(node, cls) and node.labels.get(CLIENT_LABEL) != class_path(cls):
            actual = node.labels.get(CLIENT_LABEL) or class_path(type(node))
            raise Failure(
                f"driver '{self.path}' on exporter '{session.exporter}' is {actual}, "
                f"but this library was generated for {class_path(cls)}; regenerate the client resource"
            )
        self._bound = (session, self.path, node)
        return node

    def bind(self, path: str) -> str:
        """Point this library at another driver; returns the previous path."""
        previous, self.path = self.path, path
        self._bound = None
        return previous

    def reset_binding(self) -> None:
        if self.path != self.default_path:
            self.bind(self.default_path)

    # ---- listener ----

    def end_test(self, data, result) -> None:
        context.end_test()
        if self.role:
            self.reset_binding()

    def end_suite(self, data, result) -> None:
        context.end_suite(data.id)

    def close(self) -> None:
        context.end_run()


def is_shell(cls: type) -> bool:
    return any(k.__name__ == "ShellClient" for k in cls.__mro__)


def shell_methods(node: DriverClient) -> list[str]:
    """The methods configured on a shell driver, or [] for other drivers."""
    if not is_shell(type(node)):
        return []
    try:
        return list(node.call("get_methods"))
    except Exception:  # noqa: BLE001 - they stay reachable through Run Method
        return []


def instances() -> list[Driver]:
    return list(_instances)


def _with_keyword_only(args: list[Any], extra: tuple[str, Any]) -> list[Any]:
    names = [a if isinstance(a, str) else a[0] for a in args]
    if extra[0] in names:
        return args
    if names and names[-1].startswith("**"):
        head, tail = args[:-1], args[-1:]
    else:
        head, tail = args, []
    if not any(isinstance(a, str) and a.startswith("*") and not a.startswith("**") for a in head):
        head = [*head, "*"]
    return [*head, extra, *tail]


def _method_invoker(name: str):
    def invoke(node, args, kwargs):
        return getattr(node, name)(*args, **kwargs)

    return invoke


def _generator_invoker(name: str):
    def invoke(node, args, kwargs):
        max_items = kwargs.pop("__max_items__", 1000)
        generator = getattr(node, name)(*args, **kwargs)
        try:
            return list(itertools.islice(generator, max_items))
        finally:
            close = getattr(generator, "close", None)
            if close is not None:
                close()

    return invoke


def _shell_invoker(method: str):
    def invoke(node, args, kwargs):
        return run_shell_method(node, method, args, kwargs)

    return invoke


def _property_invoker(name: str):
    def invoke(node, args, kwargs):
        return getattr(node, name)

    return invoke


def run_shell_method(node: DriverClient | None, method: str, args, env: dict[str, Any]):
    """Run a shell driver method the way ShellClient does, but capture its output."""
    from robot.api import Failure
    from robot.api import logger as robot_logger
    from robot.utils import DotDict

    assert node is not None  # shell keywords always bind their node
    env = dict(env)
    expected = env.pop("expected_rc", 0)
    stdout, stderr, rc = [], [], 0
    variables = {k: str(v) for k, v in env.items()}
    for out, err, code in node.streamingcall("call_method", method, variables, *map(str, args)):
        if out:
            stdout.append(out)
        if err:
            stderr.append(err)
        if code is not None:
            rc = int(code)  # numbers arrive as floats over the wire
    result = DotDict(rc=rc, stdout="".join(stdout), stderr="".join(stderr))
    if result.stdout:
        robot_logger.info(result.stdout)
    if result.stderr:
        robot_logger.info(f"stderr:\n{result.stderr}")
    if str(expected).lower() != "any" and rc != int(expected):
        raise Failure(f"shell method '{method}' returned {rc}, expected {expected}")
    return result
