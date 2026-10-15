"""Turn model slugs into identities.

An identity is (vendor, family, version, prerelease). "family" is the model
line with the version removed, so ``claude-opus-4-5-20251101``,
``anthropic/claude-opus-4.5`` and ``anthropic.claude-opus-4-5-20251101-v1:0``
all become ``anthropic/claude-opus@4.5``, and ``claude-opus-5-5`` is the same
family one version later.

Slugs are only ever *parsed* here. ModelBump never builds a slug from an
identity; replacement slugs always come from the catalog.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from .vendors import VENDORS

PRERELEASE_TOKENS = {"preview", "exp", "experimental", "beta", "alpha", "rc"}
# Tokens that carry no identity: "instruct"/"it" vary between hosts for the
# same weights, "latest" marks a floating alias.
NEUTRAL_TOKENS = {"instruct", "it", "hf", "latest"}

_SIZE_RE = re.compile(r"^(?:a?\d+(?:\.\d+)?[bmtk]|\d+e)$")  # 27b, a22b, 2.4t, 128e
_VERSION_RE = re.compile(r"^(?P<pre>[a-z]*?)(?P<ver>\d+(?:\.\d+)*)(?P<post>[a-z]*)$")
_REVISION_RE = re.compile(r"^v\d+$")
_SMALL_INT_RE = re.compile(r"^\d{1,2}$")
_HYPHEN_MAJOR_RE = re.compile(r"^[a-z]*\d{1,2}$")  # "4" in opus-4-5, "v3" in deepseek-v3-2
_ANTHROPIC_LEGACY_RE = re.compile(r"^claude-(\d+)(?:[-.](\d+))?-(opus|sonnet|haiku)(?=$|-)(.*)$")

# Lines that a vendor renamed without changing the product.
FAMILY_ALIASES: dict[str, dict[str, str]] = {
    "deepseek": {"deepseek-chat": "deepseek"},
}


@dataclass(frozen=True, order=True)
class Identity:
    vendor: str
    family: str
    version: tuple[int, ...]
    prerelease: bool = False

    @property
    def version_str(self) -> str:
        return ".".join(str(p) for p in self.version) if self.version else "0"

    @property
    def key(self) -> str:
        return f"{self.vendor}/{self.family}@{self.version_str}{'-pre' if self.prerelease else ''}"

    @property
    def family_key(self) -> str:
        return f"{self.vendor}/{self.family}"

    def __str__(self) -> str:
        return self.key


@dataclass(frozen=True)
class Parsed:
    identity: Identity
    snapshot: str | None = None  # dated snapshot / revision, e.g. "20251101"
    alias: bool = False  # explicit floating alias such as "-latest"

    @property
    def snapshot_date(self) -> dt.date | None:
        return snapshot_to_date(self.snapshot)


def _normalize_version(text: str) -> tuple[int, ...]:
    parts = [int(p) for p in text.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _strip_snapshot(tokens: list[str]) -> tuple[list[str], str | None]:
    """Remove trailing date-like tokens and return them as one snapshot string."""
    found: list[str] = []
    while tokens:
        last = tokens[-1]
        if (
            len(tokens) >= 3
            and re.fullmatch(r"(19|20)\d\d", tokens[-3])
            and re.fullmatch(r"\d\d", tokens[-2])
            and re.fullmatch(r"\d\d", last)
        ):  # 2024-08-06
            found.insert(0, tokens[-3] + tokens[-2] + last)
            del tokens[-3:]
        elif len(tokens) >= 2 and re.fullmatch(r"(0\d|1[0-2])", tokens[-2]) and re.fullmatch(r"\d\d", last):
            # 06-05 (month-day); only zero-padded pairs, so "4-5" is never a date
            found.insert(0, tokens[-2] + last)
            del tokens[-2:]
        elif re.fullmatch(r"\d{3}|\d{4}|\d{6}|\d{8}", last):  # 001, 0905, 2507, 20251101
            found.insert(0, last)
            tokens.pop()
        else:
            break
    return tokens, ("-".join(found) if found else None)


def parse(vendor: str, name: str, *, hyphen_versions: bool | None = None) -> Parsed | None:
    """Parse a vendor-local model name (no host prefix or suffix).

    Returns None when the name has no recognizable version: such models are
    never bumped, because there is nothing to compare. ``hyphen_versions``
    overrides the vendor default (Artificial Analysis writes gpt-5-1 for 5.1).
    """
    v = VENDORS.get(vendor)
    if v is None or not name:
        return None
    text = name.lower().strip()

    if vendor == "anthropic":
        m = _ANTHROPIC_LEGACY_RE.match(text)
        if m:  # claude-3-5-sonnet-20241022 -> claude-sonnet-3.5-20241022
            major, minor, tier, rest = m.groups()
            text = f"claude-{tier}-{major}{'.' + minor if minor else ''}{rest}"

    tokens = [t for t in text.split("-") if t]
    alias = "latest" in tokens
    tokens = [t for t in tokens if t not in NEUTRAL_TOKENS]
    prerelease = any(t in PRERELEASE_TOKENS for t in tokens)
    tokens = [t for t in tokens if t not in PRERELEASE_TOKENS]
    tokens, snapshot = _strip_snapshot(tokens)

    if v.hyphen_versions if hyphen_versions is None else hyphen_versions:
        for i in range(len(tokens) - 1):
            if _HYPHEN_MAJOR_RE.match(tokens[i]) and _SMALL_INT_RE.match(tokens[i + 1]):
                tokens[i : i + 2] = [f"{tokens[i]}.{tokens[i + 1]}"]
                break

    family: list[str] = []
    version: tuple[int, ...] | None = None
    revisions: list[str] = []
    for tok in tokens:
        if version is not None and _REVISION_RE.match(tok):
            revisions.append(tok)  # claude-3-5-sonnet-v2
            continue
        m = _VERSION_RE.match(tok)
        if version is None and m and not _SIZE_RE.match(tok):
            version = _normalize_version(m.group("ver"))
            if m.group("pre") and m.group("pre") != "v":
                family.append(m.group("pre"))
            if m.group("post"):
                family.append(m.group("post"))
            continue
        family.append(tok)

    if version is None or not family:
        return None
    fam = "-".join(family)
    fam = FAMILY_ALIASES.get(vendor, {}).get(fam, fam)
    if revisions:
        snapshot = "-".join(filter(None, [*revisions, snapshot]))
    return Parsed(Identity(vendor, fam, version, prerelease), snapshot=snapshot, alias=alias)


def snapshot_to_date(snapshot: str | None) -> dt.date | None:
    if not snapshot:
        return None
    m = re.search(r"((?:19|20)\d\d)(\d\d)(\d\d)", snapshot)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def update_type(old: Identity, new: Identity) -> str:
    """Classify a bump the way Dependabot classifies semver bumps."""
    if old.family != new.family or old.vendor != new.vendor:
        return "switch"
    if old.version == new.version:
        return "snapshot" if old.prerelease == new.prerelease else "minor"
    if old.version[:1] != new.version[:1]:
        return "major"
    return "minor"


# --- host-specific slug shapes -------------------------------------------------

OPENROUTER_MODIFIERS = {"free", "batch", "thinking", "extended", "beta"}
ROUTING_SUFFIXES = ("nitro", "floor", "online", "exacto")
_BEDROCK_RE = re.compile(r"^(?P<vendor>[a-z0-9-]+)\.(?P<name>[a-z0-9.-]+?)(?P<rev>-v\d+(?::\d+)?)?$")


def split_openrouter(slug: str) -> tuple[str, str, str | None] | None:
    """``anthropic/claude-opus-4.5:batch`` -> ("anthropic", "claude-opus-4.5", "batch")."""
    if "/" not in slug:
        return None
    prefix, _, rest = slug.partition("/")
    name, _, modifier = rest.partition(":")
    return prefix, name, modifier or None


def split_bedrock(slug: str) -> tuple[str, str, str | None] | None:
    """``anthropic.claude-opus-4-5-20251101-v1:0`` -> ("anthropic", "claude-opus-4-5-20251101", "-v1:0")."""
    m = _BEDROCK_RE.match(slug)
    if not m:
        return None
    return m.group("vendor"), m.group("name"), m.group("rev")


def split_vertex(slug: str) -> tuple[str, str | None]:
    """``claude-opus-4-5@20251101`` -> ("claude-opus-4-5", "20251101")."""
    name, _, date = slug.partition("@")
    return name, date or None


def parse_styled(vendor: str, style: str, slug: str) -> Parsed | None:
    """Parse a full slug written in one of the catalog styles."""
    if style == "openrouter":
        parts = split_openrouter(slug)
        return parse(vendor, parts[1]) if parts else None
    if style == "hf":
        return parse(vendor, slug.partition("/")[2])
    if style == "bedrock":
        parts = split_bedrock(slug)
        if not parts:
            return None
        p = parse(vendor, parts[1])
        if p and parts[2]:
            return Parsed(p.identity, snapshot=(p.snapshot or "") + parts[2], alias=p.alias)
        return p
    if style == "vertex":
        name, date = split_vertex(slug)
        p = parse(vendor, name)
        if p and date:
            return Parsed(p.identity, snapshot="-".join(filter(None, [p.snapshot, date])), alias=p.alias)
        return p
    return parse(vendor, slug)
