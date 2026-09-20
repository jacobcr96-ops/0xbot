"""Atomic JSON write helpers — never leave partial JSON readable as final name."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel


def atomic_write_json(
    path: Path,
    payload: Union[BaseModel, dict[str, Any], str],
    *,
    indent: int = 2,
    append_newline: bool = True,
) -> Path:
    """Write JSON via ``<name>.tmp`` then ``os.replace`` to ``path``.

    On failure the final path is untouched; tmp is best-effort removed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(indent=indent)
    elif isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, indent=indent, default=str)
    if append_newline and not text.endswith("\n"):
        text += "\n"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return path


def atomic_read_json(path: Path) -> Optional[dict[str, Any]]:
    """Read JSON; skip ``*.tmp`` and tolerate missing/partial files.

    Returns None if the file is missing, is a tmp, or fails to parse.
    """
    path = Path(path)
    if path.name.endswith(".tmp"):
        return None
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON object as a line and flush (best-effort durability)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
