import json
import logging
import os
from contextlib import AbstractContextManager, AsyncExitStack, ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import overload

from anyio.from_thread import start_blocking_portal
from pydantic import ValidationError

from jumpstarter.client import client_from_path
from jumpstarter.client.exceptions import LeaseError, LeaseMemberLostError
from jumpstarter.client.grpc import ForwardEndpoint, LeaseForwardEndStatus, LeaseForwardStatus
from jumpstarter.client.lease import Forward, LeaseMembers, LostMember, _ForwardStatusSource
from jumpstarter.common.exceptions import EnvironmentVariableNotSetError
from jumpstarter.config.client import ClientConfigV1Alpha1Drivers
from jumpstarter.config.env import (
    JMP_CLIENT_CONFIG,
    JMP_DRIVERS_ALLOW,
    JMP_EXPORTER,
    JMP_EXPORTER_LABELS,
    JMP_GRPC_INSECURE,
    JMP_GRPC_PASSPHRASE,
    JMP_LEASE,
    JMP_LEASE_MEMBERS,
    JUMPSTARTER_GRPC_INSECURE,
    JUMPSTARTER_HOST,
)

logger = logging.getLogger(__name__)

# The forwards of the lease of a `jmp shell` on a lease with members, as JSON:
# a list with the `name`, the `endpoints` (`member`, `port`, and the
# `direction` when the exporter reports it), the `state` and the `message` of
# each forward at the start of the shell. It is not set for a lease without
# forwards.
JMP_LEASE_FORWARDS = "JMP_LEASE_FORWARDS"


@dataclass(frozen=True)
class ExporterMetadata:
    """Metadata about the exporter connected to the current shell session."""

    name: str
    labels: dict[str, str] = field(default_factory=dict)
    lease: str | None = None

    @classmethod
    def from_env(cls) -> "ExporterMetadata":
        """Build metadata from JMP_EXPORTER, JMP_EXPORTER_LABELS, and JMP_LEASE env vars."""
        name = os.environ.get(JMP_EXPORTER, "")
        lease = os.environ.get(JMP_LEASE) or None

        labels: dict[str, str] = {}
        raw_labels = os.environ.get(JMP_EXPORTER_LABELS, "")
        if raw_labels:
            for pair in raw_labels.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k:
                        labels[k] = v

        return cls(name=name, labels=labels, lease=lease)


def _resolve_drivers_config() -> ClientConfigV1Alpha1Drivers:
    """Resolve driver settings from env vars or the current client config.

    Env vars (JMP_DRIVERS_ALLOW / JMP_DRIVERS_UNSAFE) take precedence.
    Falls back to the active client config's driver settings, then defaults.
    """
    if os.environ.get(JMP_DRIVERS_ALLOW) or os.environ.get("JMP_DRIVERS_UNSAFE"):
        return ClientConfigV1Alpha1Drivers()

    try:
        from jumpstarter.config.user import UserConfigV1Alpha1

        user_config = UserConfigV1Alpha1.load()
        if user_config.config.current_client is not None:
            return user_config.config.current_client.drivers
    except (FileNotFoundError, ValueError, ValidationError):
        logger.debug("No client config found, using default driver settings")

    return ClientConfigV1Alpha1Drivers()


@asynccontextmanager
async def env_async(portal, stack):
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    Async version of env()

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    host = os.environ.get(JUMPSTARTER_HOST, None)
    if host is None:
        if os.environ.get(JMP_LEASE_MEMBERS):
            roles = ", ".join(lease_members_from_env())
            raise EnvironmentVariableNotSetError(
                f"{JUMPSTARTER_HOST} not set: this shell holds a lease with members ({roles}), "
                "not one exporter. Call env_members() instead of env() or env_with_metadata()"
            )
        raise EnvironmentVariableNotSetError(f"{JUMPSTARTER_HOST} not set")

    drivers = _resolve_drivers_config()

    insecure = os.environ.get(JMP_GRPC_INSECURE, "") == "1" or os.environ.get(JUMPSTARTER_GRPC_INSECURE, "") == "1"
    passphrase = os.environ.get(JMP_GRPC_PASSPHRASE) or None

    async with client_from_path(
        host,
        portal,
        stack,
        allow=drivers.allow,
        unsafe=drivers.unsafe,
        insecure=insecure,
        passphrase=passphrase,
    ) as client:
        try:
            yield client
        finally:
            if hasattr(client, "close"):
                client.close()


@asynccontextmanager
async def env_with_metadata_async(portal, stack):
    """Provide a client and exporter metadata for an existing Jumpstarter shell.

    Async version of env_with_metadata()

    Yields a (client, ExporterMetadata) tuple. The metadata is read from environment
    variables set by ``jmp shell``: JMP_EXPORTER, JMP_EXPORTER_LABELS, and JMP_LEASE.
    """
    async with env_async(portal, stack) as client:
        yield client, ExporterMetadata.from_env()


@contextmanager
def env():
    """Provide a client for an existing JUMPSTARTER_HOST environment variable.

    This is useful when interacting with an already established Jumpstarter shell,
    to either a local exporter or a remote one.
    """
    with (
        start_blocking_portal() as portal,
        ExitStack() as stack,
        portal.wrap_async_context_manager(env_async(portal, stack)) as client,
    ):
        yield client


@contextmanager
def env_with_metadata():
    """Provide a client and exporter metadata for an existing Jumpstarter shell.

    This is useful when you need both the client and information about the connected
    exporter (name, labels, lease ID).

    Example::

        with env_with_metadata() as (client, metadata):
            print(metadata.name)
            print(metadata.labels)
            print(metadata.lease)
    """
    with (
        start_blocking_portal() as portal,
        ExitStack() as stack,
        portal.wrap_async_context_manager(env_with_metadata_async(portal, stack)) as result,
    ):
        yield result


def lease_members_from_env() -> dict[str, dict | None]:
    """The members of the lease of the current shell, as `jmp shell` describes them in JMP_LEASE_MEMBERS.

    Each role maps to its `exporter`, `labels` and `socket`, or to None for a
    role without an exporter. A role that lost its exporter has no socket
    (`"socket": null`) and a `lost` object with the `reason` and the `message`.
    """
    raw = os.environ.get(JMP_LEASE_MEMBERS)
    if not raw:
        raise EnvironmentVariableNotSetError(
            f"{JMP_LEASE_MEMBERS} not set: env_members() needs a jmp shell on a lease with members"
        )
    try:
        members = json.loads(raw)
    except ValueError as e:
        raise EnvironmentVariableNotSetError(f"{JMP_LEASE_MEMBERS} is not valid JSON: {e}") from None
    if not isinstance(members, dict) or any(v is not None and not isinstance(v, dict) for v in members.values()):
        raise EnvironmentVariableNotSetError(f"{JMP_LEASE_MEMBERS} is not a mapping from each role to its member")
    return members


def member_lost_error(role: str, entry: dict | None) -> LeaseMemberLostError | None:
    """The error of a role that JMP_LEASE_MEMBERS describes as lost, or None for any other role."""
    if entry is None:
        return None
    lost = entry.get("lost")
    if lost is None:
        return None
    reason = (lost.get("reason") if isinstance(lost, dict) else None) or "Lost"
    message = lost.get("message") if isinstance(lost, dict) else None
    text = f"role '{role}' is lost: {reason}"
    if message:
        text += f": {message}"
    if entry.get("exporter"):
        text += f" (exporter {entry['exporter']})"
    return LeaseMemberLostError(text, role=role, reason=reason)


def lease_forwards_from_env() -> list[dict]:
    """The forwards of the lease of the current shell, as `jmp shell` describes them in JMP_LEASE_FORWARDS.

    Each forward has its `name`, its `endpoints` (`member`, `port` and, when
    it is known, `direction`), and the `state` and the `message` that it had
    when the shell started. The list is empty for a lease without forwards.
    """
    raw = os.environ.get(JMP_LEASE_FORWARDS)
    if not raw:
        return []
    try:
        forwards = json.loads(raw)
    except ValueError as e:
        raise EnvironmentVariableNotSetError(f"{JMP_LEASE_FORWARDS} is not valid JSON: {e}") from None
    if not isinstance(forwards, list) or any(not isinstance(f, dict) or not f.get("name") for f in forwards):
        raise EnvironmentVariableNotSetError(f"{JMP_LEASE_FORWARDS} is not a list of forwards")
    return forwards


def _current_client_config():
    """The client config of this shell.

    It is the file that JMP_CLIENT_CONFIG names (`jmp shell` on a lease with
    members sets it to the client that it used), then the JMP_* variables,
    then the current client of the user. A file that JMP_CLIENT_CONFIG names
    and that cannot be read is no client config: a different client cannot
    read the lease of this shell.
    """
    from jumpstarter.config.client import ClientConfigV1Alpha1

    if path := os.environ.get(JMP_CLIENT_CONFIG):
        try:
            return ClientConfigV1Alpha1.from_file(path)
        except (OSError, ValueError, ValidationError):
            return None
    try:
        config = ClientConfigV1Alpha1()
        if config.endpoint and config.token:
            return config
    except ValidationError:
        pass
    try:
        from jumpstarter.config.user import UserConfigV1Alpha1

        return UserConfigV1Alpha1.load().config.current_client
    except (FileNotFoundError, ValueError, ValidationError):
        return None


def forwards_from_env(portal) -> dict[str, Forward]:
    """A `Forward` for each forward of the lease of the current shell, by its name.

    The state comes from the lease, through the client config of the user.
    Without one, a forward keeps the state that it had when the shell started.
    """
    entries = lease_forwards_from_env()
    lease = os.environ.get(JMP_LEASE) or None

    async def read() -> list[LeaseForwardStatus]:
        config = _current_client_config()
        if config is None or lease is None:
            raise LeaseError(
                f"cannot read the forwards of lease {lease}: no client config is set. "
                "The states are those at the start of the shell"
            )
        return list((await config.get_lease(name=lease)).forward_statuses)

    source = _ForwardStatusSource(
        lease,
        read,
        portal,
        [
            LeaseForwardStatus(
                name=entry["name"],
                state=entry.get("state") or "Pending",
                message=entry.get("message"),
                ends=[
                    LeaseForwardEndStatus(
                        member_name=end.get("member", ""),
                        state=end.get("state") or "Pending",
                        message=end.get("message"),
                    )
                    for end in entry.get("ends") or []
                    if isinstance(end, dict)
                ],
            )
            for entry in entries
        ],
    )
    return {
        entry["name"]: Forward(
            entry["name"],
            [
                ForwardEndpoint(member_name=endpoint.get("member", ""), port_name=endpoint.get("port", ""))
                for endpoint in entry.get("endpoints") or []
            ],
            source=source,
        )
        for entry in entries
    }


@asynccontextmanager
async def env_member_async(role: str, portal, stack):
    """Provide the client of one role of the lease of the current shell.

    :raises LeaseMemberLostError: the role lost its exporter
    """
    members = lease_members_from_env()
    if role not in members:
        raise KeyError(role)
    entry = members[role]
    if (error := member_lost_error(role, entry)) is not None:
        raise error
    if entry is None or not entry.get("socket"):
        raise LookupError(f"the role '{role}' has no exporter in this lease")
    drivers = _resolve_drivers_config()
    async with client_from_path(entry["socket"], portal, stack, allow=drivers.allow, unsafe=drivers.unsafe) as client:
        try:
            yield client
        finally:
            if hasattr(client, "close"):
                client.close()


@asynccontextmanager
async def env_members_async(portal, stack, members: type[LeaseMembers] = LeaseMembers):
    """Provide the members of the lease of the current shell.

    Async version of env_members()

    A role that lost its exporter is a `LostMember`: every use of it raises
    `LeaseMemberLostError`. The other roles are connected. `forwards` has
    the forwards of the lease.
    """
    entries = lease_members_from_env()
    lease = os.environ.get(JMP_LEASE) or None
    members.check_members(_members_of(entries, members), "the lease of this shell")
    forwards = forwards_from_env(portal)
    members.check_forwards(list(forwards), "the lease of this shell")
    drivers = _resolve_drivers_config()

    async with AsyncExitStack() as clients_stack:
        clients = {}
        metadata: dict[str, ExporterMetadata | None] = {}
        for role, entry in entries.items():
            if entry is None:
                clients[role] = None
                metadata[role] = None
                continue
            metadata[role] = ExporterMetadata(
                name=entry.get("exporter") or "", labels=dict(entry.get("labels") or {}), lease=lease
            )
            if (error := member_lost_error(role, entry)) is not None:
                clients[role] = LostMember(role, error.message, error.reason)
                continue
            clients[role] = await clients_stack.enter_async_context(
                client_from_path(entry["socket"], portal, stack, allow=drivers.allow, unsafe=drivers.unsafe)
            )
        try:
            yield members(clients, metadata, forwards)
        finally:
            for client in clients.values():
                if client is not None and not isinstance(client, LostMember) and hasattr(client, "close"):
                    client.close()


def _members_of(entries: dict[str, dict | None], members: type[LeaseMembers]):
    """The roles of the shell as lease members, to compare with a members class.

    The environment does not say which roles are optional. A role without an
    exporter is optional; for a role with one, the class is taken at its word,
    and a role that the class does not declare is not required of it.
    """
    from jumpstarter.client.grpc import LeaseMember

    declared = members.__lease_roles__
    return [
        LeaseMember(name=role, optional=entry is None or role not in declared or declared[role].optional)
        for role, entry in entries.items()
    ]


@overload
def env_members() -> AbstractContextManager[LeaseMembers]: ...


@overload
def env_members[M: LeaseMembers](members: type[M]) -> AbstractContextManager[M]: ...


@contextmanager
def env_members(members=LeaseMembers):
    """Provide the members of the lease of the current shell, with the metadata of each role.

    This is the function for a `jmp shell` on a lease with members, where
    env() and env_with_metadata() do not apply. Pass a `LeaseMembers` subclass
    to get an instance of that class.

    A role that lost its exporter is a `LostMember`: every use of it raises
    `LeaseMemberLostError` with the role and the reason.

    Example::

        with env_members() as members:
            members.by_role["phone"].adb.devices()
            print(members.metadata["phone"].name)

        with env_members(Projection) as members:
            members.headunit.power.on()

    The forwards of the lease are in `members.forwards`, and in the
    attributes of the class that have the annotation `Forward`::

        with env_members() as members:
            members.forwards["bt"].wait_listening(timeout=30)
    """
    with (
        start_blocking_portal() as portal,
        ExitStack() as stack,
        portal.wrap_async_context_manager(env_members_async(portal, stack, members)) as result,
    ):
        yield result
