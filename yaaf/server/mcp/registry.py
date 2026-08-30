"""The tool registry, and the scope gate every call passes through.

A tool declares the scope it needs at registration, so the gate cannot be
forgotten at the call site: dispatch checks the declaration, not the handler's
good intentions. Tools a principal does not hold are not merely refused — they
are absent from `tools/list`, because a tool a client can see and cannot use is
an invitation to argue with the server about it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from yaaf.server.mcp.auth import SCOPES, Principal, ToolRefusal


@dataclass(frozen=True)
class Tool:
    name: str
    scope: str
    description: str
    input_schema: dict
    handler: Callable[[Principal, Session, dict], object]
    #: True when the result can carry text an agent wrote. Those results get
    #: wrapped as untrusted data before they reach a model (M1).
    carries_agent_text: bool = False

    def declaration(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


TOOLS: dict[str, Tool] = {}


def register(name: str, *, scope: str, description: str, input_schema: dict,
             carries_agent_text: bool = False) -> Callable:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if name in TOOLS:
        raise ValueError(f"tool {name!r} is registered twice")

    def decorate(handler: Callable[[Principal, Session, dict], object]) -> Callable:
        TOOLS[name] = Tool(
            name=name,
            scope=scope,
            description=description,
            input_schema=input_schema,
            handler=handler,
            carries_agent_text=carries_agent_text,
        )
        return handler

    return decorate


def visible(principal: Principal) -> list[Tool]:
    return [tool for tool in TOOLS.values() if principal.holds(tool.scope)]


def call(principal: Principal, session: Session, name: str, arguments: dict) -> object:
    """Dispatch one tool call. Raises ToolRefusal; KeyError means no such tool.

    An out-of-scope tool raises KeyError rather than a refusal, so it answers
    exactly as an unknown name does. The client was never shown it, and the
    server does not confirm that it exists.
    """
    tool = TOOLS.get(name)
    if tool is None or not principal.holds(tool.scope):
        raise KeyError(name)
    return tool.handler(principal, session, arguments or {})


def required(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolRefusal(f"{key!r} is required")
    return value.strip()


def whole(arguments: dict, key: str, default: int = 0) -> int:
    """A non-negative whole number, or a refusal that says what was wrong.

    Nothing validates arguments against the declared schema at dispatch, so a
    bad offset would coerce quietly to zero and re-serve the first window for
    ever — which is exactly the confusion the argument exists to end.
    """
    value = arguments.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ToolRefusal(f"{key!r} must be a whole number of characters")
    try:
        number = int(value)
    except ValueError:
        raise ToolRefusal(f"{key!r} must be a whole number of characters") from None
    if number < 0:
        raise ToolRefusal(f"{key!r} cannot be negative")
    return number
