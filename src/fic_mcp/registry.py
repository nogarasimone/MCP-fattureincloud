"""Registrazione dei tool: filtri per toolset, sola lettura, privacy, audit, errori."""

from __future__ import annotations

import functools
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .audit import AuditLog
from .client import FicClient, FicError
from .config import Settings
from .privacy import Tokenizer, UnknownTokenError

MAX_RESULT_CHARS = 90_000


@dataclass
class Runtime:
    settings: Settings
    client: FicClient
    tokenizer: Tokenizer
    audit: AuditLog
    cache: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolInfo:
    name: str
    toolset: str
    write: bool
    destructive: bool


class Registrar:
    def __init__(self, server: MCPServer, rt: Runtime):
        self.server = server
        self.rt = rt
        self.registered: list[ToolInfo] = []
        self.skipped: list[str] = []

    def tool(self, *, toolset: str, write: bool = False, destructive: bool = False,
             idempotent: bool | None = None, entity_output: bool = False, name: str | None = None,
             title: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or fn.__name__
            settings = self.rt.settings
            if toolset not in settings.toolsets or (write and settings.read_only):
                self.skipped.append(tool_name)
                return fn
            wrapped = self._wrap(fn, tool_name, write, entity_output)
            annotations = ToolAnnotations(
                title=title,
                readOnlyHint=not write,
                destructiveHint=destructive if write else False,
                idempotentHint=(not write) if idempotent is None else idempotent,
                openWorldHint=True,
            )
            self.server.add_tool(wrapped, name=tool_name, description=inspect.getdoc(fn),
                                 annotations=annotations)
            self.registered.append(ToolInfo(tool_name, toolset, write, destructive))
            return fn
        return decorator

    def _wrap(self, fn: Callable[..., Any], tool_name: str, write: bool,
              entity_output: bool) -> Callable[..., Any]:
        rt = self.rt

        @functools.wraps(fn)
        async def wrapper(**kwargs: Any) -> Any:
            try:
                kwargs = {k: rt.tokenizer.unmask(v) if rt.tokenizer.enabled else v for k, v in kwargs.items()}
            except UnknownTokenError as exc:
                raise ToolError(str(exc)) from exc
            ids = {k: v for k, v in kwargs.items() if k.endswith("_id") and isinstance(v, (int, str))}
            try:
                result = await fn(**kwargs)
            except FicError as exc:
                message = rt.tokenizer.mask_text(str(exc))
                if write:
                    rt.audit.record(tool_name, "error", error=message[:300], **ids)
                raise ToolError(message) from exc
            except (ValueError, KeyError, FileNotFoundError) as exc:
                raise ToolError(rt.tokenizer.mask_text(str(exc))) from exc
            if write and not (isinstance(result, dict) and result.get("eseguito") is False):
                created = _extract_id(result)
                rt.audit.record(tool_name, "ok", result_id=created, **ids)
            result = rt.tokenizer.mask(result, entity_root=entity_output) if rt.tokenizer.enabled else result
            return _limit_size(result)

        return wrapper


def _extract_id(result: Any) -> Any:
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict):
            return data.get("id")
        return result.get("id")
    return None


def _limit_size(result: Any) -> Any:
    """Evita risposte enormi: tronca le liste e lo segnala al modello."""
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return result
    if len(text) <= MAX_RESULT_CHARS or not isinstance(result, dict):
        return result
    data = result.get("data")
    if isinstance(data, list) and data:
        keep = max(1, int(len(data) * MAX_RESULT_CHARS / len(text) * 0.9))
        trimmed = dict(result)
        trimmed["data"] = data[:keep]
        trimmed["_avviso"] = (f"Risposta troncata: mostrati {keep} elementi su {len(data)}. "
                              "Usa per_page più piccolo, fieldset='basic' o il parametro fields.")
        return trimmed
    return {"_avviso": "Risposta troppo grande, troncata.", "anteprima": text[:MAX_RESULT_CHARS]}
