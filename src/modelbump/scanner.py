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
    # History: rewriting it would falsify the record.
    "**/CHANGELOG*",
    "**/HISTORY*",
    "**/CHANGES*",
    "**/NEWS*",
    "**/RELEASE*NOTES*",
    "**/RELEASES*",
    "**/_posts/**",
    "**/blog/**",
    "**/_data/**",
    # Recorded API traffic, fixtures and snapshots must match what was recorded.
    "**/cassettes/**",
    "**/fixtures/**",
    "**/__fixtures__/**",
    "**/testdata/**",
    "**/__snapshots__/**",
    "**/*.snap",
    # Data files and charts: logs, eval results, datasets, plotted benchmarks.
    "**/*.svg",
    "**/*.jsonl",
    "**/*.csv",
    "**/*.tsv",
)
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox"}
# Larger files are generated code or data dumps, not hand-picked model choices.
MAX_FILE_BYTES = 512_000

_IGNORE_LINE = re.compile(r"modelbump:\s*ignore(?!-)", re.IGNORECASE)
_IGNORE_NEXT = re.compile(r"modelbump:\s*ignore-next-line", re.IGNORECASE)
# OpenRouter routing shortcuts that work on any model; kept verbatim.
_ROUTE = r"(?P<route>:(?:nitro|floor|online|exacto))?"
# Not followed by more slug characters, and not part of a regular expression
# (^gpt-5\.4, ^gpt-5(?:-mini)?$).
_AFTER = r"(?![A-Za-z0-9_($]|\\[.dwsDWSb\-(\[{]|[.\-:@][A-Za-z0-9])"
# Not inside a longer token or after a regex anchor, and not the tail of a
# Bedrock id we don't know.
_BEFORE = r"(?<![A-Za-z0-9_\-^])(?<!anthropic\.)"


_CMP_BEFORE = re.compile(
    r"""(?:[=!]==?|\b(?:startswith|endswith|startsWith|endsWith|includes|contains|indexOf|match|search)\(|"""
    r"""\bcase)\s*[rbf]?["'`]$"""
)
_CMP_AFTER = re.compile(r"""^["'`]\s*(?:[=!]==?|in\b|not\s+in\b)""")
_DATA_SUFFIXES = (".json", ".yml", ".yaml", ".toml")
_KEY_INLINE = re.compile(r"""^["'`]?\s*:(?!:)\s*\S""")  # "gpt-4o": {...}
_KEY_BLOCK = re.compile(r"""^["']?\s*:\s*$""")  # gpt-4o:   (YAML key with a nested block)
_KEY_TOML = re.compile(r"""^["']\s*=""")  # "gpt-4o" = {...}


@dataclass(frozen=True)
class Occurrence:
    path: str  # repo-relative, forward slashes
    line: int  # 1-based
    column: int  # 1-based
    start: int  # character offsets of the slug in the file
    end: int
    text: str
    line_text: str
    context: str = "code"  # "code", "comment" or "prose"

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"

    @property
    def is_comparison(self) -> bool:
        """The slug is tested against (``model == "gpt-4"``, ``"x" in name``):
        changing it changes what the condition matches."""
        col0 = self.column - 1
        before = self.line_text[:col0]
        after = self.line_text[col0 + len(self.text) :]
        return bool(_CMP_BEFORE.search(before) or _CMP_AFTER.match(after))

    @property
    def is_mapping_key(self) -> bool:
        """The slug names an entry in a table (``"gpt-4o": {...}``) rather than
        being a value. Renaming a table entry corrupts the table."""
        after = self.line_text[self.column - 1 + len(self.text) :]
        if _KEY_INLINE.match(after):
            return True
        if self.path.endswith(_DATA_SUFFIXES):
            return bool(_KEY_BLOCK.match(after) or (self.path.endswith(".toml") and _KEY_TOML.match(after)))
        return False


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


# --- where in a file a slug sits ---------------------------------------------------

_MARKDOWN = (".md", ".mdx", ".markdown")
_MARKUP = (".html", ".htm", ".xml", ".svg")
_PLAIN = (".txt", ".rst", ".adoc")
_HASH = {
    ".py", ".pyi", ".rb", ".sh", ".bash", ".zsh", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".conf", ".r", ".pl", ".tf", ".hcl", ".ps1", ".nix", ".dockerfile", ".mk",
}  # fmt: skip
_SLASH = {
    ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs", ".java", ".kt",
    ".kts", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".scala", ".dart",
    ".groovy", ".gradle", ".vue", ".svelte", ".astro", ".tf", ".hcl", ".jsonc", ".json5", ".proto",
}  # fmt: skip
_DASH = {".sql", ".lua", ".hs", ".elm"}
_HASH_NAMES = {"dockerfile", "makefile", "procfile", "gemfile", "rakefile", "justfile"}
_FENCE = re.compile(r"^\s*(```|~~~)")


def _comment_markers(path: str) -> tuple[str, ...]:
    name = path.rsplit("/", 1)[-1].lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    markers: list[str] = []
    if ext in _HASH or name in _HASH_NAMES or name.startswith(".env"):
        markers.append("#")
    if ext in _SLASH:
        markers += ["//", "/*", "*"]
    if ext in _DASH:
        markers.append("--")
    if ext == ".json" or ext == ".ipynb":
        return ()
    if not markers:  # unknown type: the two most common styles
        markers = ["#", "//"]
    return tuple(markers)


def _quoted(line: str, col0: int) -> bool:
    return col0 > 0 and line[col0 - 1] in "\"'`"


def _in_inline_code(line: str, col0: int) -> bool:
    return line[:col0].count("`") % 2 == 1


_URL_TAIL = re.compile(r"[a-z][a-z0-9+.-]*://\S*$", re.IGNORECASE)


def _classify(path: str, line: str, col0: int, region: str) -> str:
    """region is what the surrounding file structure says: "code", "fenced"
    (a Markdown code block), "prose" (Markdown text, notebook Markdown cells)
    or "output" (recorded notebook output)."""
    lower = path.lower()
    before = line[:col0]
    if region in ("prose", "output") and not lower.endswith(_MARKDOWN):
        return "prose"
    if _URL_TAIL.search(before):  # https://docs.example.com/pricing#gpt-4o
        return "prose"
    if "<!--" in before and "-->" not in before[before.rfind("<!--") :]:
        return "comment"
    if lower.endswith(_MARKDOWN):
        if region == "fenced":
            return "comment" if re.match(r"\s*(#|//)", line) else "code"
        return "code" if _in_inline_code(line, col0) else "prose"
    if lower.endswith(".ipynb"):
        # notebook source lines are JSON strings: '    "# a comment\\n",'
        return "comment" if re.match(r'\s*"\s*(#|//)', line) else "code"
    if lower.endswith(_MARKUP) or lower.endswith(_PLAIN):
        return "code" if _quoted(line, col0) or _in_inline_code(line, col0) else "prose"
    stripped = before.lstrip()
    for marker in _comment_markers(path):
        if stripped.startswith(marker):
            return "comment"
        # trailing comment: "x = 1  # uses gpt-4"
        if marker in ("#", "//", "--") and re.search(rf"(^|\s){re.escape(marker)}", before):
            return "comment"
    return "code"


_CELL_TYPE = re.compile(r'^\s*"cell_type":\s*"(\w+)"')
_OUTPUTS_OPEN = re.compile(r'^(\s*)"outputs":\s*\[\s*$')


def _regions(path: str, lines: list[str]) -> dict[int, str]:
    """Line index -> region for Markdown fences and Jupyter notebook cells."""
    lower = path.lower()
    regions: dict[int, str] = {}
    if lower.endswith(_MARKDOWN):
        inside = False
        for i, line in enumerate(lines):
            if _FENCE.match(line):
                inside = not inside
            elif inside:
                regions[i] = "fenced"
    elif lower.endswith(".ipynb"):
        cell = "code"
        closer: str | None = None
        for i, line in enumerate(lines):
            if closer is not None:
                if line.rstrip().rstrip(",") == closer:
                    closer = None
                else:
                    regions[i] = "output"
                continue
            m = _CELL_TYPE.match(line)
            if m:
                cell = m.group(1)
            m = _OUTPUTS_OPEN.match(line)
            if m:
                closer = m.group(1) + "]"
            elif cell != "code":
                regions[i] = "prose"
    return regions


def scan_text(path: str, text: str, matcher: Matcher) -> list[Occurrence]:
    line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
    lines = text.split("\n")
    regions = _regions(path, lines)
    found = []
    for start, end, slug in matcher.finditer(text):
        idx = bisect.bisect_right(line_starts, start) - 1
        line_text = lines[idx].rstrip("\r")
        if _IGNORE_LINE.search(line_text) or (idx > 0 and _IGNORE_NEXT.search(lines[idx - 1])):
            continue
        col0 = start - line_starts[idx]
        context = _classify(path, line_text, col0, regions.get(idx, "code"))
        found.append(Occurrence(path, idx + 1, col0 + 1, start, end, slug, line_text, context))
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
        if rel in skip or any(glob_match(rel.lower(), p.lower()) for p in DEFAULT_EXCLUDES):
            continue
        if config.rule_for(rel) is None:
            continue
        text = read_text(root / rel)
        if text is not None:
            occurrences.extend(scan_text(rel, text, matcher))
    return occurrences
