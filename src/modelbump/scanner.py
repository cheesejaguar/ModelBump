"""Find catalog model slugs in a repository's files.

Detection is driven by the catalog: only exact, known slugs match, so every
match can be looked up and rendered on the same host after a bump. A slug
must stand alone -- ``claude-opus-5`` does not match inside
``claude-opus-5-5`` or ``claude-opus-5-turbo``.
"""

from __future__ import annotations

import bisect
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog
from .config import CONFIG_LOCATIONS, Config, glob_match

DEFAULT_EXCLUDES = (
    ".git/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.mypy_cache/**",
    "**/.pytest_cache/**",
    "**/*.lock",
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/go.sum",
    "**/*.min.js",
    "**/*.map",
)
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox"}
MAX_FILE_BYTES = 2_000_000

_IGNORE_LINE = re.compile(r"modelbump:\s*ignore(?!-)", re.IGNORECASE)
_IGNORE_NEXT = re.compile(r"modelbump:\s*ignore-next-line", re.IGNORECASE)
# OpenRouter routing shortcuts that work on any model; kept verbatim.
_ROUTE = r"(?P<route>:(?:nitro|floor|online|exacto))?"
_AFTER = r"(?![A-Za-z0-9_]|[.\-:@][A-Za-z0-9])"
# Not inside a longer token, and not the tail of a Bedrock id we don't know.
_BEFORE = r"(?<![A-Za-z0-9_\-])(?<!anthropic\.)"


@dataclass(frozen=True)
class Occurrence:
    path: str  # repo-relative, forward slashes
    line: int  # 1-based
    column: int  # 1-based
    start: int  # character offsets of the slug in the file
    end: int
    text: str
    line_text: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def _trie_regex(words: list[str]) -> str:
    trie: dict = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = {}

    def build(node: dict) -> str:
        end = "" in node
        alts = [re.escape(ch) + build(child) for ch, child in sorted(node.items()) if ch]
        if not alts:
            return ""
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        return f"(?:{body})?" if end else body

    return build(trie)


class Matcher:
    def __init__(self, slugs: list[str]) -> None:
        # Slugs without a separator ("o3") are too ambiguous in prose and code
        # identifiers; they only match inside quotes.
        separated = sorted({s for s in slugs if re.search(r"[-./:@]", s)})
        bare = sorted({s for s in slugs if s not in separated})
        self._main = re.compile(f"{_BEFORE}(?P<slug>{_trie_regex(separated)}){_ROUTE}{_AFTER}") if separated else None
        self._bare = re.compile(f"(?<=[\"'`])(?P<slug>{_trie_regex(bare)})(?=[\"'`])") if bare else None

    def finditer(self, text: str):
        spans = []
        for rx in (self._main, self._bare):
            if rx is not None:
                spans.extend((m.start("slug"), m.end("slug"), m.group("slug")) for m in rx.finditer(text))
        return sorted(spans)


def list_files(root: Path) -> list[str]:
    """Tracked and untracked-but-not-ignored files, or a plain walk outside git."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        files = sorted({f for f in out.decode("utf-8", "replace").split("\0") if f})
        return [f for f in files if (root / f).is_file()]
    except (OSError, subprocess.CalledProcessError):
        pass
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            files.append(Path(dirpath, name).relative_to(root).as_posix())
    return sorted(files)


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_text(path: str, text: str, matcher: Matcher) -> list[Occurrence]:
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
    lines = text.split("\n")
    found = []
    for start, end, slug in matcher.finditer(text):
        idx = bisect.bisect_right(line_starts, start) - 1
        line_text = lines[idx].rstrip("\r")
        if _IGNORE_LINE.search(line_text) or (idx > 0 and _IGNORE_NEXT.search(lines[idx - 1])):
            continue
        found.append(Occurrence(path, idx + 1, start - line_starts[idx] + 1, start, end, slug, line_text))
    return found


def scan(root: Path, catalog: Catalog, config: Config, paths: list[str] | None = None) -> list[Occurrence]:
    matcher = Matcher(catalog.all_slugs())
    # Config files name models in ignore rules and scores; never rewrite them.
    skip = set(CONFIG_LOCATIONS)
    if config.path is not None:
        try:
            skip.add(config.path.resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            pass
    if config.catalog.scores_file:
        skip.add(Path(config.catalog.scores_file).as_posix().removeprefix("./"))
    occurrences: list[Occurrence] = []
    for rel in paths if paths is not None else list_files(root):
        if rel in skip or any(glob_match(rel, p) for p in DEFAULT_EXCLUDES):
            continue
        if config.rule_for(rel) is None:
            continue
        text = read_text(root / rel)
        if text is not None:
            occurrences.extend(scan_text(rel, text, matcher))
    return occurrences
