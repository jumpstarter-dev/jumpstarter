import dataclasses
import inspect
import sys
import typing
from contextlib import redirect_stdout
from importlib.metadata import entry_points
from typing import Any

import click
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print
from pydantic import BaseModel, Field, TypeAdapter, create_model


class DriverEntry(BaseModel):
    name: str
    type: str
    package: str | None = None
    version: str | None = None

    def rich_add_rows(self, table):
        table.add_row(self.name, self.type)

    def rich_add_names(self, names):
        names.append(self.name)


class DriverEntryList(BaseModel):
    drivers: list[DriverEntry]

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME", no_wrap=True)
        table.add_column("TYPE")

    def rich_add_rows(self, table):
        for entry in self.drivers:
            entry.rich_add_rows(table)

    def rich_add_names(self, names):
        for entry in self.drivers:
            entry.rich_add_names(names)


class DriverSchema(BaseModel):
    """A driver's user-settable config keys, as JSON Schema."""

    name: str
    type: str
    # Client class this driver is consumed through — what a client config's
    # drivers.allow patterns are matched against.
    client: str | None = None
    package: str | None = None
    version: str | None = None
    description: str | None = None
    # JSON Schema fragment for the exporter config's `config:` block.
    properties: dict[str, Any] = {}
    required: list[str] = []
    # Definitions the properties reference (enums, nested models). Kept so a
    # consumer can resolve the "#/$defs/..." refs inside properties.
    defs: dict[str, Any] = {}
    # Set instead of the schema when the driver class could not be loaded
    # (an optional dependency is missing, most often).
    error: str | None = None

    def rich_add_rows(self, table):
        keys = ", ".join(sorted(self.properties)) if self.properties else ""
        table.add_row(self.name, keys or (self.error or ""), ", ".join(self.required))

    def rich_add_names(self, names):
        names.append(self.name)


class DriverSchemaList(BaseModel):
    drivers: list[DriverSchema]

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME", no_wrap=True)
        table.add_column("CONFIG KEYS")
        table.add_column("REQUIRED")

    def rich_add_rows(self, table):
        for entry in self.drivers:
            entry.rich_add_rows(table)

    def rich_add_names(self, names):
        for entry in self.drivers:
            entry.rich_add_names(names)


def _base_field_names() -> set[str]:
    """Fields every Driver has, which are not part of a driver's own config."""
    from jumpstarter.driver import Driver

    return {field.name for field in dataclasses.fields(Driver)}


def _first_docstring_line(cls: type) -> str | None:
    doc = inspect.getdoc(cls)
    if not doc:
        return None
    return doc.strip().splitlines()[0]


def _client_class_path(cls: type) -> str | None:
    """The driver client class path, which drivers.allow patterns are matched against."""
    client = getattr(cls, "client", None)
    if not callable(client):
        return None
    try:
        value = client()
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _resolve_annotation(cls: type, annotation: Any) -> Any:
    """Resolve a single string annotation, so one bad field cannot poison the rest."""
    if not isinstance(annotation, str):
        return annotation
    try:
        return eval(annotation, vars(sys.modules[cls.__module__]), {cls.__name__: cls})
    except Exception:
        return annotation


def _config_fields(cls: type, base_fields: set[str]) -> list[tuple[dataclasses.Field, Any]]:
    """Constructor fields that belong to the driver's own config, with resolved types."""
    try:
        hints = typing.get_type_hints(cls, include_extras=True)
    except Exception:
        hints = {}
    return [
        (field, hints.get(field.name) or _resolve_annotation(cls, field.type))
        for field in dataclasses.fields(typing.cast(Any, cls))
        if field.name not in base_fields and field.init
    ]


def _config_model(cls: type, fields: list[tuple[dataclasses.Field, Any]]):
    kwargs = {}
    for field, annotation in fields:
        if field.default is not dataclasses.MISSING:
            kwargs[field.name] = (annotation, field.default)
        elif field.default_factory is not dataclasses.MISSING:
            kwargs[field.name] = (annotation, Field(default_factory=field.default_factory))
        else:
            kwargs[field.name] = (annotation, ...)
    return create_model(f"{cls.__name__}Config", **kwargs)


def _per_field_schema(fields: list[tuple[dataclasses.Field, Any]]):
    """Best-effort schema: degrade only the fields pydantic cannot model."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    defs: dict[str, Any] = {}
    for field, annotation in fields:
        try:
            schema = TypeAdapter(annotation).json_schema(ref_template="#/$defs/{model}")
            defs.update(schema.pop("$defs", {}))
        except Exception:
            name = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", None)
            schema = {"title": field.name} | ({"description": f"type: {name}"} if name else {})
        properties[field.name] = schema
        if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
            required.append(field.name)
    return properties, required, defs


def _schema_for_entry_point(entry_point, base_fields: set[str]) -> DriverSchema:
    dist = entry_point.dist
    entry = DriverSchema(
        name=entry_point.name,
        type=entry_point.value.replace(":", "."),
        package=dist.name if dist else None,
        version=dist.version if dist else None,
    )
    try:
        cls = entry_point.load()
    except Exception as e:
        # One uninstallable driver must not sink the whole listing.
        entry.error = f"{type(e).__name__}: {e}"
        return entry
    if not isinstance(cls, type) or not dataclasses.is_dataclass(cls):
        entry.error = f"Entry point did not resolve to a dataclass driver: {cls!r}"
        return entry

    entry.description = _first_docstring_line(cls)
    entry.client = _client_class_path(cls)
    fields = _config_fields(cls, base_fields)
    try:
        # Build the schema from config fields only: runtime-only fields
        # (init=False, or handles like TemporaryDirectory) are not user config
        # and pydantic cannot model many of them.
        schema = TypeAdapter(_config_model(cls, fields)).json_schema()
        entry.defs = schema.get("$defs", {})
        # Recursive models can put the root object in $defs as well.
        if schema.get("$ref", "").startswith("#/$defs/"):
            schema = entry.defs[schema["$ref"].removeprefix("#/$defs/")]
    except Exception as e:
        # Unmodelable constructor fields must not sink the rest of the schema.
        entry.error = f"{type(e).__name__}: {e}"
        entry.properties, entry.required, entry.defs = _per_field_schema(fields)
        return entry
    entry.properties = schema.get("properties", {})
    entry.required = schema.get("required", [])
    return entry


@click.command("schema")
@click.argument("names", nargs=-1)
@opt_output_all
def driver_schema(names: tuple[str, ...], output: OutputType):
    """Show the config keys accepted by installed drivers

    Reports each driver's user-settable config keys as JSON Schema, derived
    from the driver class itself, for the `config:` block of an exporter
    config. NAMES filters by driver name or by full type path.
    """
    wanted = set(names)
    installed = sorted(entry_points(group="jumpstarter.drivers"), key=lambda ep: (ep.name, ep.value))
    known = {name for ep in installed for name in (ep.name, ep.value.replace(":", "."))}
    missing = wanted - known
    if missing:
        raise click.ClickException(f"No installed driver matches: {', '.join(sorted(missing))}")

    # Imports (and driver schema hooks) can print. Keep Python-level output on
    # stderr so stdout remains a single machine-readable document. Loading a
    # driver executes local package code; this is not a sandbox.
    with redirect_stdout(sys.stderr):
        base_fields = _base_field_names()
        drivers = [
            _schema_for_entry_point(entry_point, base_fields)
            for entry_point in installed
            if not wanted or entry_point.name in wanted or entry_point.value.replace(":", ".") in wanted
        ]

    if not drivers and output is None:
        click.echo("No drivers found.")
        return

    model_print(DriverSchemaList(drivers=drivers), output)


@click.command("list")
@opt_output_all
def list_drivers(output: OutputType):
    """List drivers installed in the current environment"""
    drivers = []
    for entry_point in entry_points(group="jumpstarter.drivers"):
        dist = entry_point.dist
        drivers.append(
            DriverEntry(
                name=entry_point.name,
                type=entry_point.value.replace(":", "."),
                package=dist.name if dist else None,
                version=dist.version if dist else None,
            )
        )

    if not drivers and output is None:
        click.echo("No drivers found.")
        return

    model_print(DriverEntryList(drivers=drivers), output)
