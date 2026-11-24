"""Options of `jmp create lease` and `jmp shell` for a lease with members."""

import re
from dataclasses import dataclass, field

import click

from jumpstarter.client.grpc import (
    MAX_FORWARD_ENDPOINTS,
    MAX_FORWARDS,
    ForwardEndpoint,
    LeaseConstraint,
    LeaseForward,
    LeaseMember,
    LeaseTemplate,
    is_dns_label,
    validate_forwards,
)

# A role name is a DNS label, as the Lease CRD requires.
_ROLE_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_MAX_ROLE_LENGTH = 63
_MAX_MEMBERS = 8
_MAX_CONSTRAINTS = 4

opt_lease_template = click.option(
    "-t",
    "--lease-template",
    "lease_template",
    type=str,
    default=None,
    metavar="NAME",
    help="Request the lease template NAME: a set of exporters that a lab administrator published. "
    "See 'jmp get lease-templates'.",
)

opt_without = click.option(
    "--without",
    "without",
    multiple=True,
    metavar="ROLE",
    help="With --lease-template: leave out the optional role ROLE. Can be specified multiple times.",
)

opt_member = click.option(
    "--member",
    "member_entries",
    multiple=True,
    metavar="ROLE=SELECTOR",
    help="With --lease-template: add the labels of SELECTOR to the selector of ROLE. "
    "Without it: define the member ROLE with the label selector SELECTOR. Can be specified multiple times.",
)

opt_same_label = click.option(
    "--same-label",
    "same_label_entries",
    multiple=True,
    metavar="KEY=ROLE,ROLE",
    help="With --member and no template: the exporters of the listed roles must have the same value "
    "of the label KEY. Can be specified multiple times.",
)
opt_forward = click.option(
    "--forward",
    "forward_entries",
    multiple=True,
    metavar="NAME=ROLE.PORT,ROLE.PORT",
    help="With --member and no template: the forward NAME connects the port PORT of each listed role. "
    "The exporters declare the ports and their directions (see 'jmp get exporters --with ports'). "
    "More than two endpoints connect one 'provides' port to many 'requires' ports. "
    "Can be specified multiple times.",
)


def opt_members(f):
    """Add the options for a lease with members to a command."""
    for option in (opt_forward, opt_same_label, opt_member, opt_without, opt_lease_template):
        f = option(f)
    return f


@dataclass
class MemberRequest:
    """A request for a lease with members: from a template, or with ad-hoc members."""

    template: str | None = None
    excluded_members: list[str] = field(default_factory=list)
    member_selectors: dict[str, str] = field(default_factory=dict)
    members: list[LeaseMember] = field(default_factory=list)
    constraints: list[LeaseConstraint] = field(default_factory=list)
    forwards: list[LeaseForward] = field(default_factory=list)

    def create_kwargs(self) -> dict:
        """The arguments of `create_lease` and `lease_members_async` that this request sets."""
        if self.template is not None:
            kwargs: dict = {"template": self.template}
            if self.excluded_members:
                kwargs["excluded_members"] = self.excluded_members
            if self.member_selectors:
                kwargs["member_selectors"] = self.member_selectors
            return kwargs
        kwargs = {"members": self.members}
        if self.constraints:
            kwargs["constraints"] = self.constraints
        if self.forwards:
            kwargs["forwards"] = self.forwards
        return kwargs

    def check_template(self, template: LeaseTemplate) -> None:
        """Reject a role that the template does not have, and --without for a required role (DD-12)."""
        roles = ", ".join(f"{name}{'*' if (m := template.member(name)) and m.optional else ''}"
                          for name in template.role_names())
        for role in self.member_selectors:
            if template.member(role) is None:
                raise click.UsageError(
                    f"--member {role}=...: the lease template '{template.name}' has no role '{role}' (roles: {roles})"
                )
        for role in self.excluded_members:
            member = template.member(role)
            if member is None:
                raise click.UsageError(
                    f"--without {role}: the lease template '{template.name}' has no role '{role}' (roles: {roles})"
                )
            if not member.optional:
                raise click.UsageError(
                    f"--without {role}: the role '{role}' is required in the lease template '{template.name}'. "
                    "Only an optional role (marked *) can be left out"
                )


def _check_role(option: str, role: str) -> None:
    if len(role) > _MAX_ROLE_LENGTH or not _ROLE_PATTERN.match(role):
        raise click.UsageError(
            f"{option}: invalid role name {role!r} (lowercase letters, digits and '-', at most "
            f"{_MAX_ROLE_LENGTH} characters)"
        )


def _parse_member_entries(entries: tuple[str, ...]) -> dict[str, str]:
    selectors: dict[str, str] = {}
    for entry in entries:
        role, sep, selector = entry.partition("=")
        if not sep or not selector.strip():
            raise click.UsageError(
                f"Invalid --member format: {entry!r} (expected ROLE=SELECTOR, e.g. phone=device=pixel)"
            )
        _check_role("--member", role)
        if role in selectors:
            raise click.UsageError(f"--member: the role '{role}' is given more than once")
        selectors[role] = selector.strip()
    return selectors


def _parse_same_label_entries(entries: tuple[str, ...], roles: dict[str, str]) -> list[LeaseConstraint]:
    constraints = []
    for entry in entries:
        key, sep, names = entry.partition("=")
        members = [name.strip() for name in names.split(",")] if sep else []
        if not sep or not key or any(not name for name in members):
            raise click.UsageError(
                f"Invalid --same-label format: {entry!r} (expected KEY=ROLE,ROLE, e.g. rf-domain=phone,headunit)"
            )
        if len(members) < 2 or len(set(members)) != len(members):
            raise click.UsageError(f"--same-label {entry}: list two or more different roles")
        for name in members:
            if name not in roles:
                raise click.UsageError(f"--same-label {entry}: no --member defines the role '{name}'")
        constraints.append(LeaseConstraint(member_names=members, same_label=key))
    if len(constraints) > _MAX_CONSTRAINTS:
        raise click.UsageError(f"Too many --same-label entries (max {_MAX_CONSTRAINTS})")
    return constraints


_FORWARD_FORMAT = "expected NAME=ROLE.PORT,ROLE.PORT, e.g. bt=headunit.rootcanal,peer.controller"


def _parse_forward_endpoint(entry: str, text: str) -> ForwardEndpoint:
    # A role name and a port name are DNS labels, so neither has a '.'.
    role, sep, port = text.strip().partition(".")
    if not sep or not role or not port:
        raise click.UsageError(f"--forward {entry}: invalid endpoint {text.strip()!r} ({_FORWARD_FORMAT})")
    if not is_dns_label(port):
        raise click.UsageError(
            f"--forward {entry}: invalid port name {port!r} (lowercase letters, digits and '-', at most "
            f"{_MAX_ROLE_LENGTH} characters)"
        )
    return ForwardEndpoint(member_name=role, port_name=port)


def _parse_forward_entries(entries: tuple[str, ...], roles: dict[str, str]) -> list[LeaseForward]:
    forwards = []
    for entry in entries:
        name, sep, endpoints = entry.partition("=")
        if not sep or not name or not endpoints.strip():
            raise click.UsageError(f"Invalid --forward format: {entry!r} ({_FORWARD_FORMAT})")
        if not is_dns_label(name):
            raise click.UsageError(
                f"--forward: invalid forward name {name!r} (lowercase letters, digits and '-', at most "
                f"{_MAX_ROLE_LENGTH} characters)"
            )
        forward = LeaseForward(name=name, endpoints=[_parse_forward_endpoint(entry, e) for e in endpoints.split(",")])
        for endpoint in forward.endpoints:
            if endpoint.member_name not in roles:
                raise click.UsageError(
                    f"--forward {entry}: no --member defines the role '{endpoint.member_name}'"
                )
        if len(forward.endpoints) < 2:
            raise click.UsageError(f"--forward {entry}: a forward connects two or more endpoints")
        if len(forward.endpoints) > MAX_FORWARD_ENDPOINTS:
            raise click.UsageError(f"--forward {entry}: too many endpoints (max {MAX_FORWARD_ENDPOINTS})")
        forwards.append(forward)
    if len(forwards) > MAX_FORWARDS:
        raise click.UsageError(f"Too many --forward entries (max {MAX_FORWARDS})")
    try:
        # The rules that are left: one forward for each name, no name of a role, one endpoint for each role.
        validate_forwards(forwards, roles)
    except ValueError as e:
        raise click.UsageError(f"--forward: {e}") from None
    return forwards


def _reject_scalar_options(form: str, selector: str | None, exporter_name: str | None, allow_disabled: bool) -> None:
    """The options that choose one exporter do not name a role (DD-12)."""
    if selector:
        raise click.UsageError(
            f"--selector/-l cannot be used with {form}: a selector does not name a role. "
            "Use --member ROLE=SELECTOR"
        )
    if exporter_name:
        raise click.UsageError(f"--name/-n cannot be used with {form}: an exporter name does not name a role")
    if allow_disabled:
        raise click.UsageError(f"--allow-disabled cannot be used with {form}")


def _template_request(
    lease_template: str,
    without: tuple[str, ...],
    selectors: dict[str, str],
    same_label_entries: tuple[str, ...],
    forward_entries: tuple[str, ...] = (),
) -> MemberRequest:
    if same_label_entries:
        raise click.UsageError("--same-label cannot be used with --lease-template/-t: the template has the constraints")
    if forward_entries:
        raise click.UsageError("--forward cannot be used with --lease-template/-t: the template has the forwards")
    for role in without:
        _check_role("--without", role)
        if role in selectors:
            raise click.UsageError(f"the role '{role}' is given to both --without and --member")
    return MemberRequest(
        template=lease_template,
        excluded_members=list(dict.fromkeys(without)),
        member_selectors=selectors,
    )


def parse_member_request(
    *,
    lease_template: str | None,
    without: tuple[str, ...],
    member_entries: tuple[str, ...],
    same_label_entries: tuple[str, ...],
    selector: str | None,
    exporter_name: str | None,
    allow_disabled: bool = False,
    forward_entries: tuple[str, ...] = (),
) -> MemberRequest | None:
    """Validate the member options. Returns None when the command asks for one exporter."""
    if lease_template is None and not member_entries:
        if forward_entries:
            raise click.UsageError(
                "--forward needs --member: a forward connects the members of the request. "
                "A lease of one exporter has no forwards"
            )
        if without:
            raise click.UsageError("--without needs --lease-template/-t: it leaves out an optional role of a template")
        if same_label_entries:
            raise click.UsageError("--same-label needs --member: it is a rule for the members of the request")
        return None

    _reject_scalar_options(
        "--lease-template/-t" if lease_template is not None else "--member", selector, exporter_name, allow_disabled
    )

    selectors = _parse_member_entries(member_entries)

    if lease_template is not None:
        return _template_request(lease_template, without, selectors, same_label_entries, forward_entries)

    if without:
        raise click.UsageError("--without needs --lease-template/-t: it leaves out an optional role of a template")
    if len(selectors) > _MAX_MEMBERS:
        raise click.UsageError(f"Too many --member entries (max {_MAX_MEMBERS})")
    return MemberRequest(
        members=[LeaseMember(name=role, selector=role_selector) for role, role_selector in selectors.items()],
        constraints=_parse_same_label_entries(same_label_entries, selectors),
        forwards=_parse_forward_entries(forward_entries, selectors),
    )
