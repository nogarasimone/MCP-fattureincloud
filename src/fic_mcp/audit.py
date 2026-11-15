"""Registro JSONL delle operazioni di scrittura (chi, cosa, quando, esito).

Non registra i payload: solo tool, metodo, percorso, ID e stato, così il log non
diventa a sua volta un archivio di dati personali.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, tool: str, outcome: str, **fields: Any) -> None:
        if not self.path:
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool": tool,
                 "outcome": outcome, **{k: v for k, v in fields.items() if v is not None}}
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        if not self.path or not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
