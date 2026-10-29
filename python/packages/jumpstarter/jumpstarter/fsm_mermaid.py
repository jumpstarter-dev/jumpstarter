"""Compile a mermaid stateDiagram from any FSM built on jumpstarter.fsm."""

from __future__ import annotations

import ast
import inspect
import textwrap

from jumpstarter.fsm import ExitFSM, State


def _is_wait(value: ast.expr | None) -> bool:
    """A return that keeps the state: bare, None, or self, including conditional branches."""
    if value is None:
        return True
    if isinstance(value, ast.Name) and value.id in {"None", "self"}:
        return True
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    if isinstance(value, ast.IfExp):
        return _is_wait(value.body) or _is_wait(value.orelse)
    return False


def _successors(state_cls: type) -> list[type]:
    """Edges out of one state, read statically from its run method."""
    # ponytail: resolves bare Name calls via the defining module's globals;
    # states reached through aliases or dynamic construction won't appear.
    return _edges_from(state_cls.run, state_cls)  # ty: ignore[unresolved-attribute]


def _edges_from(func, owner: type) -> list[type]:
    module = inspect.getmodule(func)
    edges: list[type] = []
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(func)))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            target = module.__dict__.get(node.func.id) if module is not None else None
            if target is ExitFSM:
                edges.append(ExitFSM)
            elif isinstance(target, type) and hasattr(target, "run"):
                edges.append(target)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            # Successors a helper method returns on this state's behalf.
            helper = getattr(owner, node.func.attr, None)
            if callable(helper):
                edges.extend(_edges_from(helper, owner))
        elif isinstance(node, ast.Return) and _is_wait(node.value):
            edges.append(owner)
    return list(dict.fromkeys(edges))


def mermaid(initial: State | type[State]) -> str:
    """Statically compile a mermaid stateDiagram from one state (class or instance)."""
    start = initial if isinstance(initial, type) else type(initial)
    seen: dict[type, None] = {}
    lines = [
        "stateDiagram-v2",
        f"    [*] --> {start.__name__}",
    ]
    stack: list[type] = [start]
    while stack:
        state_cls = stack.pop()
        if state_cls in seen:
            continue
        seen[state_cls] = None
        for target in _successors(state_cls):
            to = "[*]" if target is ExitFSM else target.__name__
            lines.append(f"    {state_cls.__name__} --> {to}")
            if target is not ExitFSM and target is not state_cls:
                stack.append(target)
    return "\n".join(lines)
