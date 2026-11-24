"""Compile a mermaid stateDiagram from any FSM built on jumpstarter.fsm."""

from __future__ import annotations

import ast
import inspect
import textwrap
import typing

from jumpstarter.fsm import ExitFSM, State


def _source(func) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(func)))


def _state_class(node: ast.expr, module) -> type | None:
    """The state class (or ExitFSM) a call constructs, if it names one in the module."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and module is not None:
        target = module.__dict__.get(node.func.id)
        if target is ExitFSM or (isinstance(target, type) and hasattr(target, "run")):
            return target
    return None


def _returned(value: ast.expr | None, func, owner: type) -> list[type]:
    """States one return expression can yield; the owner itself for a wait."""
    module = inspect.getmodule(func)
    if isinstance(value, ast.Await):
        return _returned(value.value, func, owner)
    if isinstance(value, ast.IfExp):
        return _returned(value.body, func, owner) + _returned(value.orelse, func, owner)
    if value is None or (isinstance(value, ast.Constant) and value.value is None):
        return [owner]  # wait for changed inputs
    if isinstance(value, ast.Name) and value.id == "self":
        return [owner]
    # Only the state the call constructs: its arguments may be states too, such
    # as the one a composite state starts its nested machine at.
    target = _state_class(value, module)
    if target is not None:
        return [target]
    if isinstance(value, ast.Call):
        # Successors a helper returns on this state's behalf: a method, or a module function.
        callee = value.func
        if isinstance(callee, ast.Attribute) and isinstance(callee.value, ast.Name) and callee.value.id == "self":
            helper = getattr(owner, callee.attr, None)
        elif isinstance(callee, ast.Name) and module is not None:
            helper = module.__dict__.get(callee.id)
        else:
            helper = None
        if inspect.isfunction(helper):
            return _edges_from(helper, owner)
    return []


def _edges_from(func, owner: type) -> list[type]:
    """Edges read statically from what a function returns."""
    # ponytail: resolves bare Name calls via the defining module's globals;
    # states reached through aliases, variables or dynamic construction won't appear.
    edges: list[type] = []
    for node in ast.walk(_source(func)):
        if isinstance(node, ast.Return):
            edges.extend(_returned(node.value, func, owner))
    return list(dict.fromkeys(edges))


def _successors(state_cls: type) -> list[type]:
    """Edges out of one state, read statically from its run method."""
    return _edges_from(state_cls.run, state_cls)  # ty: ignore[unresolved-attribute]


def _annotated_states(state_cls: type, field: str) -> list[type]:
    """State classes a field of the state is annotated with: one class or a union of them."""
    try:
        annotation = inspect.get_annotations(state_cls).get(field)
        if isinstance(annotation, str):
            module = inspect.getmodule(state_cls)
            annotation = eval(annotation, dict(vars(module)) if module is not None else {})
    except Exception:  # noqa: BLE001 - an annotation this cannot read just draws no entry
        return []
    members = typing.get_args(annotation) or (annotation,)
    return [member for member in members if isinstance(member, type) and hasattr(member, "run")]


def _nested_entries(state_cls: type) -> list[type]:
    """States a composite state can start its nested machine at.

    Read from the argument of the run_nested() call in its run method: a state
    it constructs there, or a field of the state, whose annotation names the
    possible first states.
    """
    run = state_cls.run  # ty: ignore[unresolved-attribute]
    module = inspect.getmodule(run)
    entries: list[type] = []
    for node in ast.walk(_source(run)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run_nested"
            and node.args
        ):
            continue
        first = node.args[0]
        constructed = _state_class(first, module)
        if constructed is not None and constructed is not ExitFSM:
            entries.append(constructed)
        elif isinstance(first, ast.Attribute) and isinstance(first.value, ast.Name) and first.value.id == "self":
            entries.extend(_annotated_states(state_cls, first.attr))
    return list(dict.fromkeys(entries))


def _machine(initial: list[type], indent: str) -> list[str]:
    """Lines for one machine: its entries, then every state reachable from them."""
    lines = [f"{indent}[*] --> {state_cls.__name__}" for state_cls in initial]
    seen: dict[type, None] = {}
    stack: list[type] = list(reversed(initial))
    while stack:
        state_cls = stack.pop()
        if state_cls in seen:
            continue
        seen[state_cls] = None
        entries = _nested_entries(state_cls)
        if entries:
            # A composite state: the machine it runs inside itself, drawn inside it.
            lines.append(f"{indent}state {state_cls.__name__} {{")
            lines.extend(_machine(entries, indent + "    "))
            lines.append(f"{indent}}}")
        for target in _successors(state_cls):
            to = "[*]" if target is ExitFSM else target.__name__
            lines.append(f"{indent}{state_cls.__name__} --> {to}")
            if target is not ExitFSM and target is not state_cls:
                stack.append(target)
    return lines


def mermaid(*initial: State | type[State]) -> str:
    """Statically compile a mermaid stateDiagram from the states a machine can start at.

    Each is a state class or instance. A state that runs a nested machine is
    drawn as a composite state with that machine inside it.
    """
    entries: list[type] = [state if isinstance(state, type) else type(state) for state in initial]
    return "\n".join(["stateDiagram-v2", *_machine(entries, "    ")])
