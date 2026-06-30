"""Emit sink — stdout JSON + a JSONL audit log (pluggable).

Every emit is one line of JSON to stdout and appended to `emits.jsonl`, so the full
quoting history (including every `"no sportsbook fair"`) is auditable after the fact.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TextIO

from ..models import EmitRecord


class JsonlSink:
    def __init__(self, path: str | Path = "emits.jsonl", stream: TextIO | None = None):
        self._path = Path(path)
        self._stream = stream if stream is not None else sys.stdout

    def emit(self, record: EmitRecord) -> dict:
        line = record.to_line()
        text = json.dumps(line)
        self._stream.write(text + "\n")
        self._stream.flush()
        with self._path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
        return line
