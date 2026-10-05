"""``FlashManifest``: the bundle format the fastboot flasher reads.

A bundle is an OCI artifact (or a tar archive, or a local directory) with the
manifest YAML at its root and the files it references, conventionally under
``data/``. The manifest says what to write and in what order::

    apiVersion: jumpstarter.dev/v1alpha1
    kind: FlashManifest
    metadata:
      name: pixel-8-ap2a
    spec:
      manufacturer: Google
      requires:                    # checked against the device before any write
        product: shiba
        unlocked: "yes"
      fastboot:                    # options
        slot: inactive
        finally: continue
      steps:
        - name: Bootloader
          critical: true
          fastboot:
            flash:
              - {partition: bootloader, file: data/bootloader.img}
        - fastboot: {reboot: fastboot}
        - name: Wipe
          when: wipe
          fastboot: {erase: [userdata, metadata]}
        - sleep: 5

Steps and options are namespaced by tool (``fastboot:``) so the format can
grow other tools without changing what a fastboot bundle looks like; fastboot
is the only one today. What the format has:

* the envelope (``apiVersion``, ``kind``, ``metadata``) and ``spec.manufacturer``,
  ``spec.link``, ``spec.description``;
* ``spec.requires``: device variables that must match before anything is
  written (``name: value``, ``name: [a, b]``, or ``{equals: ..., optional: true}``);
* step fields ``name``, ``critical`` (a failure mid-step can leave the device
  unbootable), and ``when: wipe`` (run only when the flash asks for a wipe);
* the built-in ``sleep: <seconds>`` step;
* file references: every bundle file is referenced by a ``file:`` key,
  anywhere in a step, with a path relative to the manifest;
* ``variant:`` on a ``flash`` entry selects it by the exporter's board ``variant``;
* ``spec.fastboot``: the slot policy, ``finally``, and ``android_info``.

What a bundle never says: how to put the device into fastboot (that is the
exporter's ``entry`` scripts), or anything that loosens the exporter's safety
policy. A bundle can only tighten it (mark a step ``critical``); allowing
critical writes to an active slot, or an ``oem`` command, is the exporter's call.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

API_VERSION = "jumpstarter.dev/v1alpha1"
KIND = "FlashManifest"
TOOL = "fastboot"
SLEEP = "sleep"
STEP_FIELDS = ("name", "critical", "when")
_SPEC_FIELDS = ("manufacturer", "link", "description", "requires", "steps")
_ACTIONS = f"{TOOL} or {SLEEP}"

SlotPolicy = Literal["current", "inactive", "a", "b", "all"]
FinalAction = Literal["continue", "reboot", "stay"]


class ManifestError(ValueError):
    """The manifest is invalid, or cannot be applied with this flasher or exporter."""


class Model(BaseModel):
    """Base for manifest models: unknown keys are errors, aliases accepted."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _yes_no(value: Any) -> Any:
    """YAML 1.1 (PyYAML) reads unquoted yes/no as booleans; devices report the strings."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return [_yes_no(v) for v in value]
    return value


class Requirement(Model):
    """A device variable that must match (or, with ``reject``, must not) before anything is written.

    Matching follows AOSP's ``android-info.txt``: a value matches exactly, or by
    prefix when it ends in ``*`` (``version-bootloader: "abl-2.*"``).
    """

    equals: str | list[str]
    optional: bool = False
    """Skip the check when the device doesn't report the variable (otherwise that fails it)."""
    reject: bool = False
    """The device must *not* report any of the values."""
    for_product: str | None = None
    """Check only on devices whose ``product`` is this (``require-for-product:``)."""
    variable: str | None = Field(default=None, exclude=True)
    """The variable to read; the ``requires`` key unless set by ``android-info.txt`` parsing."""

    @field_validator("equals", mode="before")
    @classmethod
    def _coerce(cls, value):
        return _yes_no(value)

    @property
    def allowed(self) -> list[str]:
        return [self.equals] if isinstance(self.equals, str) else self.equals

    def matches(self, value: str) -> bool:
        """Whether ``value`` satisfies the requirement (``reject`` included)."""
        hit = any(value == option or (option.endswith("*") and value.startswith(option[:-1]))
                  for option in self.allowed)
        return hit != self.reject


# -- the fastboot vocabulary ---------------------------------------------------


class FlashEntry(Model):
    partition: str
    file: str
    variant: str | list[str] | None = None
    critical: bool = False


class FastbootStep(Model):
    """The body of a ``fastboot:`` step: exactly one action."""

    flash: list[FlashEntry] | None = None
    erase: str | list[str] | None = None
    set_active: Literal["inactive", "current", "a", "b"] | None = None
    reboot: Literal["bootloader", "fastboot"] | None = None
    oem: str | None = None

    @model_validator(mode="after")
    def _exactly_one_action(self):
        actions = [a for a in ("flash", "erase", "set_active", "reboot", "oem") if getattr(self, a) is not None]
        if len(actions) != 1:
            raise ValueError(
                f"a fastboot step needs exactly one of flash/erase/set_active/reboot/oem, got {actions or 'none'}"
            )
        return self


class FileRef(Model):
    file: str


class FastbootOptions(Model):
    """``spec.fastboot``."""

    slot: SlotPolicy = "current"
    """Which A/B slot ``flash``/``erase`` write on an A/B device."""
    finally_: FinalAction = Field(default="reboot", alias="finally")
    """What to do once every step succeeded."""
    android_info: FileRef | None = None
    """An AOSP ``android-info.txt`` in the bundle: its ``require`` lines are checked like ``fastboot flashall`` does."""


# -- the manifest ----------------------------------------------------------------


class _Spec(Model):
    manufacturer: str | None = None
    link: str | None = None
    description: str | None = None
    requires: dict[str, str | list[str] | Requirement] = Field(default_factory=dict)
    fastboot: FastbootOptions = Field(default_factory=FastbootOptions)
    steps: list[dict[str, Any]]

    @field_validator("fastboot", mode="before")
    @classmethod
    def _empty_options(cls, value):
        return {} if value is None else value  # `fastboot:` with nothing under it

    @field_validator("requires", mode="before")
    @classmethod
    def _coerce_requires(cls, value):
        if isinstance(value, dict):
            return {k: v if isinstance(v, dict) else _yes_no(v) for k, v in value.items()}
        return value


class _Envelope(Model):
    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = API_VERSION
    kind: Literal["FlashManifest"] = KIND
    metadata: dict[str, Any] = Field(default_factory=dict)
    spec: _Spec


class _StepFields(Model):
    name: str | None = None
    critical: bool = False
    when: Literal["wipe"] | None = None


@dataclass
class ManifestStep:
    """One manifest step: the shared fields, plus the action (``sleep`` or ``fastboot``) and its body."""

    index: int
    action: str
    body: Any
    """Seconds for ``sleep``; the validated :class:`FastbootStep` otherwise."""
    name: str | None = None
    critical: bool = False
    when: Literal["wipe"] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def label(self) -> str:
        return self.name or f"step {self.index + 1}"


@dataclass
class FlashManifest:
    """A parsed, validated ``FlashManifest``."""

    raw: dict[str, Any]
    """The manifest as given (what a stage stores, and re-parses at start)."""
    name: str | None
    requires: dict[str, Requirement]
    options: FastbootOptions
    steps: list[ManifestStep]
    files: list[str]
    """Every referenced bundle file (normalized), in first-reference order."""

    @classmethod
    def parse(cls, source: str | Mapping[str, Any]) -> FlashManifest:
        """Parse YAML text or a mapping."""
        data = yaml.safe_load(source) if isinstance(source, str) else dict(source)
        if not isinstance(data, dict):
            raise ManifestError("manifest is not a YAML mapping")
        if data.get("kind") != KIND:
            raise ManifestError(f"manifest kind is {data.get('kind')!r}, expected {KIND!r}")
        try:
            envelope = _Envelope.model_validate(data)
        except ValidationError as exc:
            raise ManifestError(f"invalid {KIND}: {exc}") from exc
        spec = envelope.spec

        steps = [_parse_step(index, raw) for index, raw in enumerate(spec.steps)]
        if not steps:
            raise ManifestError("the manifest has no steps")
        requires = {
            k: (v if isinstance(v, Requirement) else Requirement(equals=v)).model_copy(update={"variable": k})
            for k, v in spec.requires.items()
        }
        return cls(
            raw=data,
            name=envelope.metadata.get("name"),
            requires=requires,
            options=spec.fastboot,
            steps=steps,
            files=referenced_files(data),
        )


def _parse_step(index: int, raw: Any) -> ManifestStep:
    where = f"step {index + 1}"
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: a step is a mapping, got {type(raw).__name__}")
    if isinstance(raw.get("name"), str):
        where = f"step {index + 1} ({raw['name']})"
    actions = [key for key in raw if key not in STEP_FIELDS]
    if len(actions) != 1:
        raise ManifestError(f"{where}: needs exactly one action ({_ACTIONS}), got {actions or 'none'}")
    (action,) = actions
    common = {key: raw[key] for key in STEP_FIELDS if key in raw}
    try:
        shared = _StepFields.model_validate(common)
    except ValidationError as exc:
        raise ManifestError(f"{where}: {exc}") from exc

    if action == SLEEP:
        seconds = raw[SLEEP]
        if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds < 0:
            raise ManifestError(f"{where}: sleep takes a number of seconds, got {seconds!r}")
        body: Any = float(seconds)
    elif action == TOOL:
        try:
            body = FastbootStep.model_validate(raw[action])
        except ValidationError as exc:
            raise ManifestError(f"{where}: invalid {action!r} step: {exc}") from exc
    else:
        raise ManifestError(f"{where}: unknown action {action!r} (expected {_ACTIONS})")
    return ManifestStep(index=index, action=action, body=body, raw=raw, **shared.model_dump())


def normalize_path(path: str) -> str:
    """Normalize a bundle-relative path and reject anything escaping the bundle."""
    if not isinstance(path, str) or not path:
        raise ManifestError(f"file reference {path!r} is not a path")
    norm = posixpath.normpath(path)
    if norm.startswith(("/", "../")) or norm in ("..", "."):
        raise ManifestError(f"file reference {path!r} escapes the bundle")
    return norm


def _file_values(node: Any) -> Iterator[Any]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "file":
                yield value
            else:
                yield from _file_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _file_values(item)


def referenced_files(manifest: Mapping[str, Any]) -> list[str]:
    """Every ``file:`` reference in a raw manifest's steps and options, normalized, in first-reference order.

    The client uses this to know what to upload for a local bundle; the
    flasher uses it to check that a stage holds everything before it starts.
    """
    spec = manifest.get("spec") or {}
    nodes = [spec.get("steps") or [], *(v for k, v in spec.items() if k not in _SPEC_FIELDS)]
    seen: dict[str, None] = {}
    for value in _file_values(nodes):
        seen.setdefault(normalize_path(value), None)
    return list(seen)


def synthesized_manifest(name: str, steps: list[dict[str, Any]], **spec: Any) -> dict[str, Any]:
    """A raw ``FlashManifest`` built by the flasher, for images given without one."""
    return {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {"name": name, "synthesized": True},
        "spec": {**spec, "steps": steps},
    }
