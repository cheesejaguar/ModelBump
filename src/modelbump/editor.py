"""Apply planned changes to files on disk."""

from __future__ import annotations

import difflib
from collections import defaultdict
from pathlib import Path

from .planner import Change


class StaleFileError(RuntimeError):
    """A file changed between scanning and editing."""


def _rewrite(text: str, changes: list[Change]) -> str:
    for c in sorted(changes, key=lambda c: c.occurrence.start, reverse=True):
        o = c.occurrence
        if text[o.start : o.end] != o.text:
            raise StaleFileError(f"{o.path}:{o.line} no longer contains {o.text!r}; re-run the scan")
        text = text[: o.start] + c.to_slug.text + text[o.end :]
    return text


def _by_file(changes: list[Change]) -> dict[str, list[Change]]:
    out: dict[str, list[Change]] = defaultdict(list)
    for c in changes:
        out[c.occurrence.path].append(c)
    return out


def _read(path: Path) -> str:
    # newline="" keeps CRLF files byte-identical outside the edited slugs
    with path.open(encoding="utf-8", newline="") as f:
        return f.read()


def apply_changes(root: Path, changes: list[Change]) -> list[str]:
    """Rewrite files in place. Returns the paths that changed."""
    edited = []
    for rel, file_changes in _by_file(changes).items():
        path = root / rel
        new = _rewrite(_read(path), file_changes)
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(new)
        edited.append(rel)
    return edited


def render_diff(root: Path, changes: list[Change]) -> str:
    chunks = []
    for rel, file_changes in sorted(_by_file(changes).items()):
        old = _read(root / rel)
        new = _rewrite(old, file_changes)
        chunks.extend(
            difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"
            )
        )
    return "".join(chunks)
