"""The model catalog: every known model identity and every slug that names it."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

import yaml

from . import sources
from .config import CatalogConfig
from .slugs import OPENROUTER_MODIFIERS, Identity, parse, parse_styled, snapshot_to_date, split_openrouter
from .vendors import VENDORS, vendor_for_aa_creator, vendor_for_openrouter_prefix

log = logging.getLogger("modelbump")

STYLES = ("native", "openrouter", "bedrock", "vertex", "hf")
# Bedrock has two id shapes with different request APIs: versioned ids
# (anthropic.claude-...-v1:0) for InvokeModel/Converse and bare ids
# (anthropic.claude-opus-5-5) for the Mantle Messages endpoint. They are kept
# apart so a bump never moves code from one API to the other.
STYLE_LABELS = {
    "native": "first-party API",
    "openrouter": "OpenRouter",
    "bedrock": "Amazon Bedrock (versioned id)",
    "bedrock-mantle": "Amazon Bedrock (Mantle)",
    "vertex": "Google Vertex AI",
    "hf": "Hugging Face",
}
_STATUS_RANK = {"active": 0, "deprecated": 1, "retired": 2}
# OpenRouter-only reasoning-effort aliases that are not first-party ids.
_NOT_NATIVE_SUFFIXES = ("-high",)


@dataclass
class Slug:
    text: str
    style: str
    pinned: bool = False
    modifier: str | None = None  # OpenRouter variant such as "free" or "batch"
    released: dt.date | None = None
    snapshot: str | None = None
    derived: bool = False  # inferred from another host's id, not confirmed

    def order_key(self) -> tuple[dt.date, str, str]:
        return (self.released or snapshot_to_date(self.snapshot) or dt.date.min, self.snapshot or "", self.text)


@dataclass
class Model:
    identity: Identity
    name: str | None = None
    released: dt.date | None = None
    retires: dt.date | None = None
    status: str = "active"
    price_in: float | None = None  # USD per million input tokens
    price_out: float | None = None
    context: int | None = None
    input_modalities: frozenset[str] | None = None
    output_modalities: frozenset[str] | None = None
    open_weights: bool = False
    score: float | None = None
    score_source: str | None = None
    slugs: list[Slug] = field(default_factory=list)
    sources: set[str] = field(default_factory=set)

    @property
    def vendor(self) -> str:
        return self.identity.vendor

    @property
    def key(self) -> str:
        return self.identity.key

    @property
    def display_name(self) -> str:
        return self.name or f"{self.identity.family} {self.identity.version_str}"

    def lifecycle(self, today: dt.date) -> str:
        if self.status == "retired" or (self.retires and self.retires <= today):
            return "retired"
        if self.status == "deprecated" or self.retires:
            return "deprecated"
        return "active"

    def blended_price(self, weights: tuple[float, float] = (3.0, 1.0)) -> float | None:
        if self.price_in is None or self.price_out is None:
            return None
        wi, wo = weights
        if wi + wo == 0:
            return None
        return (wi * self.price_in + wo * self.price_out) / (wi + wo)

    def slugs_for(self, style: str, modifier: str | None = None) -> list[Slug]:
        return sorted(
            (s for s in self.slugs if s.style == style and s.modifier == modifier), key=Slug.order_key
        )


class Catalog:
    def __init__(self) -> None:
        self.models: dict[str, Model] = {}
        self.origins: dict[str, str] = {}  # source name -> where its data came from
        self.notes: list[str] = []
        self._index: dict[str, tuple[Model, Slug]] | None = None

    # --- building ---------------------------------------------------------------

    def model(self, identity: Identity, source: str) -> Model:
        m = self.models.get(identity.key)
        if m is None:
            m = self.models[identity.key] = Model(identity)
        m.sources.add(source)
        return m

    def add_slug(self, model: Model, slug: Slug) -> None:
        owner = self.index().get(slug.text)
        if owner and owner[0] is not model:
            log.debug("slug %s already belongs to %s; not adding to %s", slug.text, owner[0].key, model.key)
            return
        for existing in model.slugs:
            if existing.text == slug.text and existing.style == slug.style:
                existing.derived = existing.derived and slug.derived
                existing.released = existing.released or slug.released
                return
        model.slugs.append(slug)
        self._index = None

    def remove_slugs(self, predicate) -> None:
        for m in self.models.values():
            m.slugs = [s for s in m.slugs if not predicate(m, s)]
        self._index = None

    @staticmethod
    def merge(
        model: Model,
        *,
        name: str | None = None,
        released: dt.date | None = None,
        retires: dt.date | None = None,
        status: str | None = None,
        prices: tuple[float | None, float | None] | None = None,
        override_prices: bool = False,
        context: int | None = None,
        input_modalities: Iterable[str] | None = None,
        output_modalities: Iterable[str] | None = None,
        open_weights: bool | None = None,
    ) -> None:
        if name and not model.name:
            model.name = name
        if released and (model.released is None or released < model.released):
            model.released = released
        if retires and (model.retires is None or retires < model.retires):
            model.retires = retires
        if status and _STATUS_RANK.get(status, 0) > _STATUS_RANK.get(model.status, 0):
            model.status = status
        if prices and None not in prices and (override_prices or model.price_in is None):
            model.price_in, model.price_out = prices
        if context and not model.context:
            model.context = context
        if input_modalities and model.input_modalities is None:
            model.input_modalities = frozenset(input_modalities)
        if output_modalities and model.output_modalities is None:
            model.output_modalities = frozenset(output_modalities)
        if open_weights:
            model.open_weights = True

    # --- queries ----------------------------------------------------------------

    def index(self) -> dict[str, tuple[Model, Slug]]:
        if self._index is None:
            idx: dict[str, tuple[Model, Slug]] = {}
            for m in self.models.values():
                for s in m.slugs:
                    idx.setdefault(s.text, (m, s))
            self._index = idx
        return self._index

    def lookup(self, text: str) -> tuple[Model, Slug] | None:
        return self.index().get(text)

    def family(self, model: Model) -> list[Model]:
        ident = model.identity
        return [
            m for m in self.models.values() if m.identity.vendor == ident.vendor and m.identity.family == ident.family
        ]

    def by_vendors(self, vendors: Iterable[str]) -> list[Model]:
        wanted = set(vendors)
        return [m for m in self.models.values() if m.vendor in wanted]

    def all_slugs(self) -> list[str]:
        return list(self.index())


# --- loaders ----------------------------------------------------------------------


def _as_date(value: Any) -> dt.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _pinned(style: str, text: str, snapshot: str | None) -> bool:
    if style == "vertex":
        return "@" in text
    if style == "bedrock":
        return bool(re.search(r"-v\d+:\d+$", text)) or snapshot is not None
    return snapshot is not None


def load_entries(cat: Catalog, entries: Iterable[dict[str, Any]], source: str) -> None:
    """Load hand-written model entries (the bundled seed or ``catalog.models``)."""
    for i, e in enumerate(entries):
        vendor = e.get("vendor")
        if vendor not in VENDORS:
            cat.notes.append(f"{source} entry {i}: unknown vendor {vendor!r}; skipped")
            continue
        styled = [(style, text) for style in STYLES for text in (e.get(style) or [])]
        parsed = [(style, text, parse_styled(vendor, style, text)) for style, text in styled]
        identity = next((p.identity for _, _, p in parsed if p), None)
        if identity is None:
            cat.notes.append(f"{source} entry {i}: no parseable slug; skipped")
            continue
        m = cat.model(identity, source)
        pricing = e.get("pricing") or {}
        cat.merge(
            m,
            name=e.get("name"),
            released=_as_date(e.get("released")),
            retires=_as_date(e.get("retires")),
            status=e.get("status"),
            prices=(pricing.get("input"), pricing.get("output")) if pricing else None,
            context=e.get("context"),
            open_weights=e.get("open-weights") or e.get("open_weights"),
        )
        if e.get("score") is not None:
            m.score, m.score_source = float(e["score"]), source
        for style, text, p in parsed:
            snap = p.snapshot if p else None
            if style == "bedrock" and not re.search(r"-v\d+(:\d+)?$", text):
                style = "bedrock-mantle"
            cat.add_slug(m, Slug(text, style, pinned=_pinned(style, text, snap), snapshot=snap))


def load_bundled_seed(cat: Catalog) -> None:
    text = resources.files("modelbump").joinpath("data/seed.yaml").read_text(encoding="utf-8")
    load_entries(cat, yaml.safe_load(text)["models"], "seed")


def _vendor_name_prefix(name: str | None) -> str | None:
    if not name:
        return None
    return name.split(": ", 1)[1] if ": " in name else name


def load_openrouter(cat: Catalog, records: Iterable[dict[str, Any]], *, derive_native: bool = True) -> None:
    rows = []
    for r in records:
        parts = split_openrouter(r.get("id", ""))
        if not parts:
            continue
        prefix, name, modifier = parts
        vendor = vendor_for_openrouter_prefix(prefix)
        if vendor is None or (modifier and modifier not in OPENROUTER_MODIFIERS):
            continue
        p = parse(vendor.key, name)
        if p is not None:
            rows.append((r, name, modifier, vendor, p))
    # Floating ids first, then pinned snapshots newest-first, so a model's name
    # and price come from the id most people use rather than an old snapshot.
    rows.sort(key=lambda row: (row[4].snapshot is not None, -(row[0].get("created") or 0)))

    for r, name, modifier, vendor, p in rows:
        created = r.get("created")
        released = dt.datetime.fromtimestamp(created, dt.timezone.utc).date() if created else None
        m = cat.model(p.identity, "openrouter")
        cat.add_slug(
            m,
            Slug(
                r["id"],
                "openrouter",
                pinned=p.snapshot is not None,
                modifier=modifier,
                released=released,
                snapshot=p.snapshot,
            ),
        )
        if modifier:
            continue  # :free / :batch variants carry their own prices
        pricing = r.get("pricing") or {}
        try:
            prices = (round(float(pricing["prompt"]) * 1e6, 6), round(float(pricing["completion"]) * 1e6, 6))
            if prices[0] < 0 or prices[1] < 0:
                prices = None
        except (KeyError, TypeError, ValueError):
            prices = None
        arch = r.get("architecture") or {}
        cat.merge(
            m,
            name=_vendor_name_prefix(r.get("name")),
            released=released,
            retires=_as_date(r.get("expiration_date")),
            prices=prices,
            override_prices=p.snapshot is None,
            context=r.get("context_length"),
            input_modalities=arch.get("input_modalities"),
            output_modalities=arch.get("output_modalities"),
            open_weights=bool(r.get("hugging_face_id")),
        )
        hf = r.get("hugging_face_id")
        if hf:
            cat.add_slug(m, Slug(hf, "hf", pinned=p.snapshot is not None, released=released, snapshot=p.snapshot))
        if derive_native and vendor.derive_native and not name.endswith(_NOT_NATIVE_SUFFIXES):
            cat.add_slug(
                m,
                Slug(
                    name,
                    "native",
                    pinned=p.snapshot is not None,
                    released=released,
                    snapshot=p.snapshot,
                    derived=True,
                ),
            )


def load_native_list(
    cat: Catalog,
    vendor: str,
    items: Iterable[tuple[str, dt.date | None]],
    *,
    source: str,
    create_models: bool,
) -> None:
    """Attach first-party ids from a vendor's live model list.

    Derived ids for the vendor that the list does not contain are dropped, and
    the ones it does contain become confirmed.
    """
    listed: set[str] = set()
    for model_id, created in items:
        p = parse(vendor, model_id)
        if p is None:
            continue
        m = cat.models.get(p.identity.key)
        if m is None:
            if not create_models:
                continue
            m = cat.model(p.identity, source)
        m.sources.add(source)
        cat.merge(m, released=created)
        cat.add_slug(
            m, Slug(model_id, "native", pinned=p.snapshot is not None, released=created, snapshot=p.snapshot)
        )
        listed.add(model_id)
    cat.remove_slugs(lambda m, s: m.vendor == vendor and s.style == "native" and s.derived and s.text not in listed)
    for m in cat.models.values():
        if m.vendor == vendor:
            for s in m.slugs:
                if s.style == "native" and s.text in listed:
                    s.derived = False


AA_METRICS = {
    "intelligence": "artificial_analysis_intelligence_index",
    "coding": "artificial_analysis_coding_index",
    "math": "artificial_analysis_math_index",
}
# Reasoning-effort variants Artificial Analysis lists separately; the best
# variant's score is used for the model.
_AA_EFFORT_TOKENS = {
    "reasoning", "non-reasoning", "nonreasoning", "thinking", "adaptive", "minimal",
    "low", "medium", "high", "xhigh", "max",
}  # fmt: skip


def load_aa_scores(cat: Catalog, records: Iterable[dict[str, Any]], metric: str) -> int:
    """Attach Artificial Analysis scores. Returns how many models got one."""
    field_name = AA_METRICS.get(metric, metric)
    best: dict[str, float] = {}
    for r in records:
        creator = (r.get("model_creator") or {}).get("slug") or ""
        vendor = vendor_for_aa_creator(creator)
        value = (r.get("evaluations") or {}).get(field_name)
        slug = (r.get("slug") or "").lower()
        if vendor is None or value is None or not slug:
            continue
        stripped = "-".join(t for t in slug.split("-") if t not in _AA_EFFORT_TOKENS)
        for candidate in dict.fromkeys([slug, stripped]):
            p = parse(vendor.key, candidate, hyphen_versions=True)
            if p and p.identity.key in cat.models:
                key = p.identity.key
                best[key] = max(best.get(key, float("-inf")), float(value))
                break
    for key, value in best.items():
        m = cat.models[key]
        if m.score_source in (None, "artificial-analysis"):
            m.score, m.score_source = value, "artificial-analysis"
    return len(best)


def apply_scores(cat: Catalog, scores: dict[str, float], source: str) -> None:
    for slug, value in scores.items():
        hit = cat.lookup(slug)
        if hit is None:
            cat.notes.append(f"{source}: no catalog model for {slug!r}; score ignored")
            continue
        hit[0].score, hit[0].score_source = float(value), source


def build_catalog(
    cfg: CatalogConfig,
    *,
    root: Path | None = None,
    offline: bool = False,
    refresh: bool = False,
) -> Catalog:
    cat = Catalog()
    load_bundled_seed(cat)
    cat.origins["seed"] = "bundled"
    kw = {"ttl_hours": cfg.cache_ttl_hours, "refresh": refresh, "offline": offline}

    if "openrouter" in cfg.sources:
        records, origin = sources.openrouter_models(**kw)
        load_openrouter(cat, records, derive_native=cfg.derive_native_slugs)
        cat.origins["openrouter"] = origin
    if "anthropic" in cfg.sources and (items := sources.anthropic_models(**kw)):
        load_native_list(cat, "anthropic", items, source="anthropic", create_models=True)
        cat.origins["anthropic"] = "api.anthropic.com"
    if "openai" in cfg.sources and (items := sources.openai_models(**kw)):
        # The OpenAI list includes embeddings, audio, image models: only confirm
        # ids for models already known from OpenRouter or the seed.
        load_native_list(cat, "openai", items, source="openai", create_models=False)
        cat.origins["openai"] = "api.openai.com"

    if cfg.models:
        load_entries(cat, cfg.models, "config")

    if "artificial-analysis" in cfg.sources and (records := sources.artificial_analysis_models(**kw)):
        n = load_aa_scores(cat, records, cfg.benchmark)
        cat.origins["artificial-analysis"] = f"artificialanalysis.ai ({n} models scored)"
    if cfg.scores_file:
        path = Path(cfg.scores_file)
        if root and not path.is_absolute():
            path = root / path
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
            apply_scores(cat, {str(k): float(v) for k, v in (data or {}).items()}, f"scores-file:{path.name}")
        except (OSError, ValueError, yaml.YAMLError) as e:
            cat.notes.append(f"could not read scores file {path}: {e}")
    if cfg.scores:
        apply_scores(cat, cfg.scores, "config")
    return cat
